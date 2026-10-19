# ruff: noqa: S608 - solo se interpolan las constantes de este archivo (_ACTIVOS, _TENANTS_VIVOS); todo valor variable viaja como parámetro
"""Analíticas de TODA la plataforma para el Super Admin: negocios, reservas, ventas, retención y horas pico.

Solo agregados (conteos y sumas): nunca datos personales de clientes. Se calcula en vivo con la sesión de plataforma (RLS de plataforma).
Las fechas se agrupan en hora de Colombia, salvo el mapa de calor, que usa la zona de cada negocio. Con muchos negocios y años de historia
conviene precalcular (tabla resumen diaria); hoy, con el índice por fecha de cada tabla, responde en milisegundos.
"""
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.platform import service
from app.modules.platform.schemas import (
    AnalyticsOut,
    AnalyticsSummary,
    CitasPorEstado,
    DayPoint,
    GrowthPoint,
    HeatCell,
    OriginPoint,
    PlanSlice,
    ThinTenant,
    TopTenant,
    TypeSlice,
    UpcomingExpiry,
)

TZ = "America/Bogota"
_ACTIVOS = "t.deleted_at IS NULL"
_TENANTS_VIVOS = "tenant_id IN (SELECT id FROM tenants WHERE deleted_at IS NULL)"


def _pct(parte: int, total: int) -> float:
    return round(100 * parte / total, 1) if total else 0.0


async def run(db: AsyncSession, dias: int, top: int = 8) -> AnalyticsOut:
    hoy: date = (await db.execute(text("SELECT (now() AT TIME ZONE :tz)::date"), {"tz": TZ})).scalar_one()
    desde = hoy - timedelta(days=dias - 1)
    prm = {"tz": TZ, "desde": desde, "hoy": hoy}

    # ---- negocios y usuarios
    total, activos, nuevos = (await db.execute(text(
        "SELECT count(*), count(*) FILTER (WHERE estado IN ('activa','por_vencer')), "
        "       count(*) FILTER (WHERE (created_at AT TIME ZONE :tz)::date >= :desde) FROM tenants WHERE deleted_at IS NULL"), prm)).one()
    usuarios = (await db.execute(text(
        f"SELECT count(*) FROM users WHERE activo AND deleted_at IS NULL AND {_TENANTS_VIVOS}"))).scalar_one()  # noqa: S608
    clientes_nuevos = (await db.execute(text(
        f"SELECT count(*) FROM customers WHERE deleted_at IS NULL AND {_TENANTS_VIVOS} AND (created_at AT TIME ZONE :tz)::date >= :desde"), prm)).scalar_one()  # noqa: S608

    # ---- citas del periodo (por fecha de la cita) y ventas cobradas
    estados = {e: n for e, n in (await db.execute(text(
        f"SELECT estado, count(*) FROM appointments WHERE deleted_at IS NULL AND {_TENANTS_VIVOS} "  # noqa: S608
        "AND (inicio AT TIME ZONE :tz)::date BETWEEN :desde AND :hoy GROUP BY 1"), prm)).all()}
    ventas_total, ventas_n = (await db.execute(text(
        f"SELECT coalesce(sum(total), 0), count(*) FROM sales WHERE deleted_at IS NULL AND estado = 'pagada' AND {_TENANTS_VIVOS} "  # noqa: S608
        "AND (pagada_en AT TIME ZONE :tz)::date BETWEEN :desde AND :hoy"), prm)).one()
    por_origen = [OriginPoint(origen=o, reservas=n) for o, n in (await db.execute(text(
        f"SELECT origen, count(*) FROM appointments WHERE deleted_at IS NULL AND {_TENANTS_VIVOS} "  # noqa: S608
        "AND (created_at AT TIME ZONE :tz)::date BETWEEN :desde AND :hoy GROUP BY 1 ORDER BY 2 DESC"), prm)).all()]
    reservas_creadas = sum(o.reservas for o in por_origen)
    web = next((o.reservas for o in por_origen if o.origen == "reserva_web"), 0)
    mrr = (await service.dashboard(db)).mrr

    total_citas = sum(estados.values())
    atendidas, no_asistio, canceladas = estados.get("atendida", 0), estados.get("no_asistio", 0), estados.get("cancelada", 0)
    resumen = AnalyticsSummary(
        negocios_total=total, negocios_activos=activos, negocios_nuevos=nuevos, usuarios_activos=usuarios, clientes_nuevos=clientes_nuevos,
        mrr=mrr, reservas_creadas=reservas_creadas, citas_periodo=total_citas, citas_atendidas=atendidas,
        tasa_no_show=_pct(no_asistio, atendidas + no_asistio), tasa_cancelacion=_pct(canceladas, total_citas),
        reservas_online_pct=_pct(web, reservas_creadas), ventas_total=Decimal(ventas_total), ventas_cantidad=ventas_n,
        ticket_promedio=(Decimal(ventas_total) / ventas_n).quantize(Decimal("0.01")) if ventas_n else Decimal("0"))

    # ---- serie diaria (todos los días del rango, con ceros)
    serie = [DayPoint(fecha=f, reservas=r, atendidas=a, ventas=Decimal(v)) for f, r, a, v in (await db.execute(text(
        "WITH dias AS (SELECT d::date AS fecha FROM generate_series(CAST(:desde AS date), CAST(:hoy AS date), interval '1 day') d), "
        f" cr AS (SELECT (created_at AT TIME ZONE :tz)::date f, count(*) n FROM appointments WHERE deleted_at IS NULL AND {_TENANTS_VIVOS} "  # noqa: S608
        "          AND (created_at AT TIME ZONE :tz)::date BETWEEN :desde AND :hoy GROUP BY 1), "
        f" ate AS (SELECT (inicio AT TIME ZONE :tz)::date f, count(*) n FROM appointments WHERE deleted_at IS NULL AND estado = 'atendida' AND {_TENANTS_VIVOS} "  # noqa: S608
        "          AND (inicio AT TIME ZONE :tz)::date BETWEEN :desde AND :hoy GROUP BY 1), "
        f" vt AS (SELECT (pagada_en AT TIME ZONE :tz)::date f, sum(total) n FROM sales WHERE deleted_at IS NULL AND estado = 'pagada' AND {_TENANTS_VIVOS} "  # noqa: S608
        "         AND (pagada_en AT TIME ZONE :tz)::date BETWEEN :desde AND :hoy GROUP BY 1) "
        "SELECT dias.fecha, coalesce(cr.n, 0), coalesce(ate.n, 0), coalesce(vt.n, 0) FROM dias "
        "LEFT JOIN cr ON cr.f = dias.fecha LEFT JOIN ate ON ate.f = dias.fecha LEFT JOIN vt ON vt.f = dias.fecha ORDER BY 1"), prm)).all()]

    # ---- reparto por plan y por tipo de negocio
    por_plan = [PlanSlice(plan=p, negocios=n, mrr=Decimal(m)) for p, n, m in (await db.execute(text(
        "SELECT p.nombre, count(*), coalesce(sum(s.precio_plan_aplicado + coalesce(x.extra, 0)), 0) AS mrr FROM tenants t "
        "JOIN tenant_subscriptions s ON s.tenant_id = t.id AND s.estado = 'vigente' JOIN plans p ON p.id = s.plan_id "
        "LEFT JOIN LATERAL (SELECT sum(coalesce(tm.precio_override, m.precio_mensual_base)) AS extra FROM tenant_modules tm "
        "  JOIN modules m ON m.id = tm.module_id WHERE tm.tenant_id = t.id AND tm.origen = 'adicional' AND tm.activo) x ON true "
        f"WHERE {_ACTIVOS} AND t.estado IN ('activa', 'por_vencer', 'vencida') GROUP BY p.nombre ORDER BY mrr DESC, p.nombre"))).all()]
    por_tipo = [TypeSlice(tipo=tp, negocios=n) for tp, n in (await db.execute(text(
        f"SELECT bt.nombre, count(*) FROM tenants t JOIN business_types bt ON bt.codigo = t.business_type WHERE {_ACTIVOS} GROUP BY 1 ORDER BY 2 DESC, 1"))).all()]  # noqa: S608

    # ---- ranking de negocios del periodo
    top = [TopTenant(nombre=n, slug=s, citas=c, atendidas=a, no_asistio=na, ventas=Decimal(v)) for n, s, c, a, na, v in (await db.execute(text(
        "SELECT t.nombre, t.slug::text, count(*) FILTER (WHERE a.estado <> 'cancelada'), count(*) FILTER (WHERE a.estado = 'atendida'), "
        "  count(*) FILTER (WHERE a.estado = 'no_asistio'), "
        "  coalesce((SELECT sum(s.total) FROM sales s WHERE s.tenant_id = t.id AND s.estado = 'pagada' AND s.deleted_at IS NULL "
        "            AND (s.pagada_en AT TIME ZONE :tz)::date BETWEEN :desde AND :hoy), 0) "
        "FROM tenants t JOIN appointments a ON a.tenant_id = t.id AND a.deleted_at IS NULL "
        f"AND (a.inicio AT TIME ZONE :tz)::date BETWEEN :desde AND :hoy WHERE {_ACTIVOS} GROUP BY t.id ORDER BY 3 DESC, 2 LIMIT :top"), {**prm, "top": top})).all()]

    # ---- horas pico (día de la semana x hora, en la zona de cada negocio)
    calor = [HeatCell(dia=d, hora=h, citas=n) for d, h, n in (await db.execute(text(
        "SELECT extract(isodow FROM a.inicio AT TIME ZONE t.zona_horaria)::int - 1, extract(hour FROM a.inicio AT TIME ZONE t.zona_horaria)::int, count(*) "
        f"FROM appointments a JOIN tenants t ON t.id = a.tenant_id AND {_ACTIVOS} WHERE a.deleted_at IS NULL AND a.estado <> 'cancelada' "
        "AND (a.inicio AT TIME ZONE :tz)::date BETWEEN :desde AND :hoy GROUP BY 1, 2"), prm)).all()]

    # ---- a quién llamar: vencimientos cercanos y negocios sin actividad
    vencen = [UpcomingExpiry(nombre=n, slug=s, estado=e, fecha_fin=f, dias=d, plan=p) for n, s, e, f, d, p in (await db.execute(text(
        "SELECT t.nombre, t.slug::text, t.estado, s.fecha_fin, (s.fecha_fin - (now() AT TIME ZONE t.zona_horaria)::date), p.nombre FROM tenants t "
        "JOIN tenant_subscriptions s ON s.tenant_id = t.id AND s.estado = 'vigente' JOIN plans p ON p.id = s.plan_id "
        f"WHERE {_ACTIVOS} AND (s.fecha_fin - (now() AT TIME ZONE t.zona_horaria)::date) <= 14 ORDER BY s.fecha_fin, t.nombre LIMIT 10"))).all()]
    sin_filtro = ("FROM tenants t WHERE t.deleted_at IS NULL AND t.estado IN ('activa', 'por_vencer') AND NOT EXISTS (SELECT 1 FROM appointments a "
                  "WHERE a.tenant_id = t.id AND a.deleted_at IS NULL AND a.created_at >= now() - interval '14 days')")
    sin_actividad_n = (await db.execute(text(f"SELECT count(*) {sin_filtro}"))).scalar_one()  # noqa: S608
    sin_actividad = [ThinTenant(nombre=n, slug=s) for n, s in (await db.execute(text(
        f"SELECT t.nombre, t.slug::text {sin_filtro} ORDER BY t.created_at DESC LIMIT 8"))).all()]  # noqa: S608

    # ---- crecimiento de los últimos 6 meses
    crecimiento = [GrowthPoint(mes=m, nuevos=n, acumulado=a) for m, n, a in (await db.execute(text(
        "WITH meses AS (SELECT date_trunc('month', CAST(:hoy AS date) - make_interval(months => k))::date AS mes FROM generate_series(5, 0, -1) k) "
        "SELECT meses.mes, (SELECT count(*) FROM tenants t WHERE t.deleted_at IS NULL AND date_trunc('month', t.created_at AT TIME ZONE :tz)::date = meses.mes), "
        "       (SELECT count(*) FROM tenants t WHERE t.deleted_at IS NULL AND (t.created_at AT TIME ZONE :tz)::date < (meses.mes + interval '1 month')::date) "
        "FROM meses ORDER BY 1"), prm)).all()]

    return AnalyticsOut(
        desde=desde, hasta=hoy, dias=dias, zona_horaria=TZ, resumen=resumen, serie=serie, citas_por_estado=CitasPorEstado(**{
            k: estados.get(k, 0) for k in ("pendiente", "confirmada", "en_atencion", "atendida", "cancelada", "no_asistio")}),
        por_plan=por_plan, por_tipo=por_tipo, por_origen=por_origen, top_negocios=top, mapa_calor=calor, vencimientos=vencen,
        sin_actividad_total=sin_actividad_n, sin_actividad=sin_actividad, crecimiento=crecimiento)
