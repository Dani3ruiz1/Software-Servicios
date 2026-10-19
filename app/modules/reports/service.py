"""Reportes. Todos devuelven la misma forma (ReportOut) y respetan la zona horaria del negocio."""
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.repository import scoped
from app.core.tenancy import Principal
from app.modules.appointments.availability import ESTADOS_QUE_OCUPAN
from app.modules.appointments.models import Appointment
from app.modules.appointments.service import Ctx, get_ctx
from app.modules.reports import occupancy as occ
from app.modules.reports.schemas import Col, ReportOut
from app.modules.staff.models import ScheduleBlock, StaffProfile, StaffSchedule

MAX_DAYS = 366
GROUP = {"dia": ("day", "YYYY-MM-DD"), "mes": ("month", "YYYY-MM"), "anio": ("year", "YYYY")}
ZERO = Decimal("0")


def pct(part: Any, whole: Any) -> float:
    return round(100 * float(part) / float(whole), 1) if whole else 0.0


def utc_range(desde: date, hasta: date, ctx: Ctx) -> tuple[datetime, datetime]:
    """Fechas locales inclusivas -> [inicio, fin) en UTC."""
    if hasta < desde:
        raise AppError("VALIDATION_ERROR", "hasta debe ser igual o posterior a desde")
    if (hasta - desde).days >= MAX_DAYS:
        raise AppError("VALIDATION_ERROR", f"El rango máximo es de {MAX_DAYS} días")
    a = datetime.combine(desde, time.min, tzinfo=ctx.tz).astimezone(UTC)
    b = datetime.combine(hasta + timedelta(days=1), time.min, tzinfo=ctx.tz).astimezone(UTC)
    return a, b


def _out(name: str, titulo: str, desde: date, hasta: date, ctx: Ctx, cols: list[Col], filas: list[dict[str, Any]],
         totales: dict[str, Any]) -> ReportOut:
    return ReportOut(reporte=name, titulo=titulo, desde=desde, hasta=hasta, zona_horaria=ctx.tz_name, columnas=cols,
                     filas=filas, totales=totales)


async def _rows(db: AsyncSession, sql: str, **prm: Any) -> list[Any]:
    return list((await db.execute(text(sql), prm)).all())


# ------------------------------------------------------------------ ventas
async def ventas(db: AsyncSession, tid: UUID, ctx: Ctx, desde: date, hasta: date, agrupar: str, staff_id: UUID | None) -> ReportOut:
    a, b = utc_range(desde, hasta, ctx)
    g, fmt = GROUP[agrupar]
    rows = await _rows(db, (
        "SELECT to_char(date_trunc(CAST(:g AS text), pagada_en AT TIME ZONE CAST(:tz AS text)), CAST(:f AS text)) AS periodo, "
        "count(*) AS ventas, sum(subtotal) AS subtotal, sum(iva) AS iva, sum(propina) AS propina, sum(total) AS total "
        "FROM sales WHERE tenant_id = :t AND estado = 'pagada' AND pagada_en >= :a AND pagada_en < :b "
        "AND (CAST(:s AS uuid) IS NULL OR staff_id = :s) GROUP BY 1 ORDER BY 1"),
        g=g, f=fmt, tz=ctx.tz_name, t=tid, a=a, b=b, s=staff_id)
    por_periodo = {r.periodo: {"periodo": r.periodo, "ventas": r.ventas, "subtotal": r.subtotal, "iva": r.iva, "propina": r.propina,
                               "total": r.total, "ticket_promedio": (r.total / r.ventas).quantize(Decimal("0.01"))} for r in rows}
    # Notas crédito aceptadas: restan en el periodo en que SE EMITEN (el pasado ya cerrado no se reescribe).
    # No tienen profesional: con filtro por profesional no se muestran.
    nc = {} if staff_id else {r.periodo: r.valor for r in await _rows(db, (
        "SELECT to_char(date_trunc(CAST(:g AS text), fecha_emision AT TIME ZONE CAST(:tz AS text)), CAST(:f AS text)) AS periodo, "
        "sum((datos->'totales'->>'total')::numeric) AS valor FROM invoices WHERE tenant_id = :t AND tipo = 'nota_credito' AND estado = 'aceptada' "
        "AND fecha_emision >= :a AND fecha_emision < :b GROUP BY 1"), g=g, f=fmt, tz=ctx.tz_name, t=tid, a=a, b=b)}
    for per in nc:
        por_periodo.setdefault(per, {"periodo": per, "ventas": 0, "subtotal": ZERO, "iva": ZERO, "propina": ZERO, "total": ZERO, "ticket_promedio": ZERO})
    filas = [{**f, "notas_credito": nc.get(per, ZERO), "neto": f["total"] - nc.get(per, ZERO)} for per, f in sorted(por_periodo.items())]
    n, tot = sum(f["ventas"] for f in filas), sum((f["total"] for f in filas), ZERO)
    tot_nc = sum((f["notas_credito"] for f in filas), ZERO)
    totales = {"periodo": "Total", "ventas": n, "subtotal": sum((f["subtotal"] for f in filas), ZERO),
               "iva": sum((f["iva"] for f in filas), ZERO), "propina": sum((f["propina"] for f in filas), ZERO), "total": tot,
               "ticket_promedio": (tot / n).quantize(Decimal("0.01")) if n else ZERO, "notas_credito": tot_nc, "neto": tot - tot_nc}
    cols = [Col(key="periodo", label="Periodo", tipo="texto"), Col(key="ventas", label="Ventas", tipo="numero"),
            Col(key="subtotal", label="Subtotal", tipo="dinero"), Col(key="iva", label="IVA", tipo="dinero"),
            Col(key="propina", label="Propinas", tipo="dinero"), Col(key="total", label="Total", tipo="dinero"),
            Col(key="ticket_promedio", label="Ticket promedio", tipo="dinero"),
            Col(key="notas_credito", label="Notas crédito", tipo="dinero"), Col(key="neto", label="Neto", tipo="dinero")]
    return _out("ventas", f"Ventas por {agrupar}", desde, hasta, ctx, cols, filas, totales)


async def por_profesional(db: AsyncSession, tid: UUID, ctx: Ctx, desde: date, hasta: date, agrupar: str, staff_id: UUID | None) -> ReportOut:
    a, b = utc_range(desde, hasta, ctx)
    rows = await _rows(db, (
        "SELECT coalesce(sp.nombre_publico, 'Sin profesional') AS profesional, count(*) AS ventas, sum(s.subtotal) AS subtotal, "
        "sum(s.propina) AS propina, sum(s.total) AS total FROM sales s LEFT JOIN staff_profiles sp "
        "ON sp.tenant_id = s.tenant_id AND sp.id = s.staff_id WHERE s.tenant_id = :t AND s.estado = 'pagada' "
        "AND s.pagada_en >= :a AND s.pagada_en < :b AND (CAST(:s AS uuid) IS NULL OR s.staff_id = :s) "
        "GROUP BY 1 ORDER BY total DESC"), t=tid, a=a, b=b, s=staff_id)
    filas = [{"profesional": r.profesional, "ventas": r.ventas, "subtotal": r.subtotal, "propina": r.propina, "total": r.total}
             for r in rows]
    totales = {"profesional": "Total", "ventas": sum(f["ventas"] for f in filas), "subtotal": sum((f["subtotal"] for f in filas), ZERO),
               "propina": sum((f["propina"] for f in filas), ZERO), "total": sum((f["total"] for f in filas), ZERO)}
    cols = [Col(key="profesional", label="Profesional", tipo="texto"), Col(key="ventas", label="Ventas", tipo="numero"),
            Col(key="subtotal", label="Servicios", tipo="dinero"), Col(key="propina", label="Propinas", tipo="dinero"),
            Col(key="total", label="Total", tipo="dinero")]
    return _out("por-profesional", "Ventas por profesional", desde, hasta, ctx, cols, filas, totales)


async def por_servicio(db: AsyncSession, tid: UUID, ctx: Ctx, desde: date, hasta: date, agrupar: str, staff_id: UUID | None) -> ReportOut:
    a, b = utc_range(desde, hasta, ctx)
    rows = await _rows(db, (
        "SELECT i.descripcion AS servicio, count(*) AS cantidad, sum(i.precio_cobrado) AS ingresos, "
        "sum(i.precio_original - i.precio_cobrado) AS descuentos FROM sale_items i JOIN sales s "
        "ON s.tenant_id = i.tenant_id AND s.id = i.sale_id WHERE s.tenant_id = :t AND s.estado = 'pagada' "
        "AND s.pagada_en >= :a AND s.pagada_en < :b AND (CAST(:s AS uuid) IS NULL OR s.staff_id = :s) "
        "GROUP BY 1 ORDER BY ingresos DESC"), t=tid, a=a, b=b, s=staff_id)
    filas = [{"servicio": r.servicio, "cantidad": r.cantidad, "ingresos": r.ingresos, "descuentos": r.descuentos} for r in rows]
    totales = {"servicio": "Total", "cantidad": sum(f["cantidad"] for f in filas), "ingresos": sum((f["ingresos"] for f in filas), ZERO),
               "descuentos": sum((f["descuentos"] for f in filas), ZERO)}
    cols = [Col(key="servicio", label="Servicio", tipo="texto"), Col(key="cantidad", label="Cantidad", tipo="numero"),
            Col(key="ingresos", label="Ingresos", tipo="dinero"), Col(key="descuentos", label="Descuentos", tipo="dinero")]
    return _out("por-servicio", "Ventas por servicio", desde, hasta, ctx, cols, filas, totales)


async def por_metodo(db: AsyncSession, tid: UUID, ctx: Ctx, desde: date, hasta: date, agrupar: str, staff_id: UUID | None) -> ReportOut:
    a, b = utc_range(desde, hasta, ctx)
    rows = await _rows(db, (
        "SELECT pg.metodo, count(*) AS pagos, sum(pg.monto) AS monto FROM payments pg JOIN sales s "
        "ON s.tenant_id = pg.tenant_id AND s.id = pg.sale_id WHERE s.tenant_id = :t AND s.estado = 'pagada' "
        "AND s.pagada_en >= :a AND s.pagada_en < :b AND (CAST(:s AS uuid) IS NULL OR s.staff_id = :s) "
        "GROUP BY 1 ORDER BY monto DESC"), t=tid, a=a, b=b, s=staff_id)
    total = sum((r.monto for r in rows), ZERO)
    filas = [{"metodo": r.metodo, "pagos": r.pagos, "monto": r.monto, "porcentaje": pct(r.monto, total)} for r in rows]
    totales = {"metodo": "Total", "pagos": sum(f["pagos"] for f in filas), "monto": total, "porcentaje": 100.0 if total else 0.0}
    cols = [Col(key="metodo", label="Método de pago", tipo="texto"), Col(key="pagos", label="Pagos", tipo="numero"),
            Col(key="monto", label="Monto", tipo="dinero"), Col(key="porcentaje", label="% del total", tipo="porcentaje")]
    return _out("por-metodo", "Ventas por método de pago", desde, hasta, ctx, cols, filas, totales)


# ------------------------------------------------------------------ ocupación
def _period(d: date, agrupar: str) -> str:
    return {"dia": d.isoformat(), "mes": f"{d.year}-{d.month:02d}", "anio": str(d.year)}[agrupar]


async def ocupacion(db: AsyncSession, tid: UUID, ctx: Ctx, desde: date, hasta: date, agrupar: str, staff_id: UUID | None) -> ReportOut:
    a, b = utc_range(desde, hasta, ctx)
    q = scoped(StaffProfile, tid)
    if staff_id:
        q = q.where(StaffProfile.id == staff_id)
    staff = list((await db.execute(q.order_by(StaffProfile.nombre_publico))).scalars())
    ids = [s.id for s in staff]
    sched: dict[tuple[UUID, int], list[tuple[time, time]]] = defaultdict(list)
    blocks: dict[UUID, list[occ.Interval]] = defaultdict(list)
    appts: dict[UUID, list[occ.Interval]] = defaultdict(list)
    if ids:
        for sch in (await db.execute(select(StaffSchedule).where(StaffSchedule.tenant_id == tid, StaffSchedule.staff_id.in_(ids)))).scalars():
            sched[(sch.staff_id, sch.dia_semana)].append((sch.hora_inicio, sch.hora_fin))
        for blk in (await db.execute(select(ScheduleBlock).where(ScheduleBlock.tenant_id == tid, ScheduleBlock.staff_id.in_(ids),
                                                              ScheduleBlock.inicio < b, ScheduleBlock.fin > a))).scalars():
            blocks[blk.staff_id].append((blk.inicio, blk.fin))
        for r in (await db.execute(scoped(Appointment, tid).where(
                Appointment.staff_id.in_(ids), Appointment.estado.in_(ESTADOS_QUE_OCUPAN),
                Appointment.inicio < b, Appointment.fin > a))).scalars():
            appts[r.staff_id].append((r.inicio, r.fin))
    acc: dict[tuple[str, UUID], list[float]] = defaultdict(lambda: [0.0, 0.0])
    for d in occ.daterange(desde, hasta):
        for s in staff:
            tr = sched.get((s.id, d.weekday()), [])
            if not tr:
                continue
            d0 = datetime.combine(d, time.min, tzinfo=ctx.tz).astimezone(UTC)
            day = (d0, d0 + timedelta(days=1))
            bl = [x for x in blocks[s.id] if occ.overlap_min(x, day) > 0]
            ap = [x for x in appts[s.id] if occ.overlap_min(x, day) > 0]
            disp, res = occ.staff_day_minutes(d, tr, bl, ap, ctx.tz)
            k = acc[(_period(d, agrupar), s.id)]
            k[0] += disp
            k[1] += res
    names = {s.id: s.nombre_publico for s in staff}
    filas = [{"periodo": per, "profesional": names[sid], "horas_disponibles": round(v[0] / 60, 2),
              "horas_reservadas": round(v[1] / 60, 2), "ocupacion": occ.pct(v[1], v[0])}
             for (per, sid), v in sorted(acc.items(), key=lambda kv: (kv[0][0], names[kv[0][1]]))]
    td, tr_ = sum(v[0] for v in acc.values()), sum(v[1] for v in acc.values())
    totales = {"periodo": "Total", "profesional": "Todos", "horas_disponibles": round(td / 60, 2),
               "horas_reservadas": round(tr_ / 60, 2), "ocupacion": occ.pct(tr_, td)}
    cols = [Col(key="periodo", label="Periodo", tipo="texto"), Col(key="profesional", label="Profesional", tipo="texto"),
            Col(key="horas_disponibles", label="Horas disponibles", tipo="numero"),
            Col(key="horas_reservadas", label="Horas reservadas", tipo="numero"), Col(key="ocupacion", label="Ocupación", tipo="porcentaje")]
    return _out("ocupacion", f"Ocupación por {agrupar} y profesional", desde, hasta, ctx, cols, filas, totales)


# ------------------------------------------------------------------ clientes y citas
async def clientes(db: AsyncSession, tid: UUID, ctx: Ctx, desde: date, hasta: date, agrupar: str, staff_id: UUID | None) -> ReportOut:
    a, b = utc_range(desde, hasta, ctx)
    g, fmt = GROUP[agrupar]
    prm = dict(g=g, f=fmt, tz=ctx.tz_name, t=tid, a=a, b=b)
    rows = await _rows(db, (
        "WITH primera AS (SELECT customer_id, min(pagada_en) AS f FROM sales WHERE tenant_id = :t AND estado = 'pagada' "
        "  AND customer_id IS NOT NULL GROUP BY 1), "
        "v AS (SELECT DISTINCT s.customer_id, date_trunc(CAST(:g AS text), s.pagada_en AT TIME ZONE CAST(:tz AS text)) AS p, "
        "  date_trunc(CAST(:g AS text), pr.f AT TIME ZONE CAST(:tz AS text)) AS fp FROM sales s "
        "  JOIN primera pr ON pr.customer_id = s.customer_id WHERE s.tenant_id = :t AND s.estado = 'pagada' "
        "  AND s.pagada_en >= :a AND s.pagada_en < :b) "
        "SELECT to_char(p, CAST(:f AS text)) AS periodo, count(*) FILTER (WHERE p = fp) AS nuevos, "
        "count(*) FILTER (WHERE p > fp) AS recurrentes FROM v GROUP BY p ORDER BY p"), **prm)
    filas = [{"periodo": r.periodo, "nuevos": r.nuevos, "recurrentes": r.recurrentes, "total": r.nuevos + r.recurrentes,
              "pct_recurrentes": pct(r.recurrentes, r.nuevos + r.recurrentes)} for r in rows]
    # Totales sobre TODO el rango (un cliente que vuelve varios meses se cuenta una sola vez)
    t = (await db.execute(text(
        "WITH primera AS (SELECT customer_id, min(pagada_en) AS f FROM sales WHERE tenant_id = :t AND estado = 'pagada' "
        "  AND customer_id IS NOT NULL GROUP BY 1) "
        "SELECT count(*) FILTER (WHERE f >= :a) AS nuevos, count(*) FILTER (WHERE f < :a) AS recurrentes FROM primera pr "
        "WHERE EXISTS (SELECT 1 FROM sales s WHERE s.tenant_id = :t AND s.estado = 'pagada' AND s.customer_id = pr.customer_id "
        "  AND s.pagada_en >= :a AND s.pagada_en < :b)"), prm)).one()
    totales = {"periodo": "Total", "nuevos": t.nuevos, "recurrentes": t.recurrentes, "total": t.nuevos + t.recurrentes,
               "pct_recurrentes": pct(t.recurrentes, t.nuevos + t.recurrentes)}
    cols = [Col(key="periodo", label="Periodo", tipo="texto"), Col(key="nuevos", label="Nuevos", tipo="numero"),
            Col(key="recurrentes", label="Recurrentes", tipo="numero"), Col(key="total", label="Clientes atendidos", tipo="numero"),
            Col(key="pct_recurrentes", label="% recurrentes", tipo="porcentaje")]
    return _out("clientes", "Clientes nuevos vs recurrentes (por ventas pagadas)", desde, hasta, ctx, cols, filas, totales)


async def citas(db: AsyncSession, tid: UUID, ctx: Ctx, desde: date, hasta: date, agrupar: str, staff_id: UUID | None) -> ReportOut:
    a, b = utc_range(desde, hasta, ctx)
    g, fmt = GROUP[agrupar]
    rows = await _rows(db, (
        "SELECT to_char(date_trunc(CAST(:g AS text), inicio AT TIME ZONE CAST(:tz AS text)), CAST(:f AS text)) AS periodo, "
        "count(*) AS total, count(*) FILTER (WHERE estado = 'atendida') AS atendidas, "
        "count(*) FILTER (WHERE estado = 'cancelada') AS canceladas, count(*) FILTER (WHERE estado = 'no_asistio') AS no_asistio "
        "FROM appointments WHERE tenant_id = :t AND deleted_at IS NULL AND inicio >= :a AND inicio < :b "
        "AND (CAST(:s AS uuid) IS NULL OR staff_id = :s) GROUP BY 1 ORDER BY 1"),
        g=g, f=fmt, tz=ctx.tz_name, t=tid, a=a, b=b, s=staff_id)
    filas = [{"periodo": r.periodo, "total": r.total, "atendidas": r.atendidas, "canceladas": r.canceladas,
              "no_asistio": r.no_asistio, "pct_cancelacion": pct(r.canceladas, r.total), "pct_no_show": pct(r.no_asistio, r.total)}
             for r in rows]
    T = {k: sum(f[k] for f in filas) for k in ("total", "atendidas", "canceladas", "no_asistio")}
    totales = {"periodo": "Total", **T, "pct_cancelacion": pct(T["canceladas"], T["total"]), "pct_no_show": pct(T["no_asistio"], T["total"])}
    cols = [Col(key="periodo", label="Periodo", tipo="texto"), Col(key="total", label="Citas", tipo="numero"),
            Col(key="atendidas", label="Atendidas", tipo="numero"), Col(key="canceladas", label="Canceladas", tipo="numero"),
            Col(key="no_asistio", label="No asistió", tipo="numero"), Col(key="pct_cancelacion", label="% cancelación", tipo="porcentaje"),
            Col(key="pct_no_show", label="% no-show", tipo="porcentaje")]
    return _out("citas", "Cancelaciones y no-shows", desde, hasta, ctx, cols, filas, totales)



# ------------------------------------------------------------------ reportes solo de reservas (sin dinero): lo que usa la versión "motor de reservas"
_VIVAS = "estado <> 'cancelada'"   # una reserva cancelada no cuenta como visita ni como demanda


async def ranking_clientes(db: AsyncSession, tid: UUID, ctx: Ctx, desde: date, hasta: date, agrupar: str, staff_id: UUID | None) -> ReportOut:
    """Quién reserva más: reservas, atendidas, canceladas, no vino y cada cuántos días vuelve (según las visitas atendidas)."""
    a, b = utc_range(desde, hasta, ctx)
    rows = await _rows(db, (
        "SELECT c.nombre AS cliente, count(*) AS reservas, count(*) FILTER (WHERE x.estado = 'atendida') AS atendidas, "
        "count(*) FILTER (WHERE x.estado = 'cancelada') AS canceladas, count(*) FILTER (WHERE x.estado = 'no_asistio') AS no_asistio, "
        "to_char(max(x.inicio) FILTER (WHERE x.estado = 'atendida') AT TIME ZONE CAST(:tz AS text), 'YYYY-MM-DD') AS ultima, "
        "CASE WHEN count(*) FILTER (WHERE x.estado = 'atendida') > 1 THEN round(extract(epoch FROM (max(x.inicio) FILTER (WHERE x.estado = 'atendida') "
        "- min(x.inicio) FILTER (WHERE x.estado = 'atendida'))) / 86400.0 / (count(*) FILTER (WHERE x.estado = 'atendida') - 1)) END AS cada_dias "
        "FROM appointments x JOIN customers c ON c.tenant_id = x.tenant_id AND c.id = x.customer_id "
        "WHERE x.tenant_id = :t AND x.deleted_at IS NULL AND x.inicio >= :a AND x.inicio < :b AND (CAST(:s AS uuid) IS NULL OR x.staff_id = :s) "
        "GROUP BY c.id, c.nombre ORDER BY reservas DESC, atendidas DESC, c.nombre LIMIT 100"), tz=ctx.tz_name, t=tid, a=a, b=b, s=staff_id)
    filas = [{"cliente": r.cliente, "reservas": r.reservas, "atendidas": r.atendidas, "canceladas": r.canceladas, "no_asistio": r.no_asistio,
              "ultima": r.ultima or "—", "cada_dias": int(r.cada_dias) if r.cada_dias is not None else 0} for r in rows]
    totales = {"cliente": "Total", **{k: sum(f[k] for f in filas) for k in ("reservas", "atendidas", "canceladas", "no_asistio")}, "ultima": "", "cada_dias": 0}
    cols = [Col(key="cliente", label="Cliente", tipo="texto"), Col(key="reservas", label="Reservas", tipo="numero"), Col(key="atendidas", label="Atendidas", tipo="numero"),
            Col(key="canceladas", label="Canceladas", tipo="numero"), Col(key="no_asistio", label="No vino", tipo="numero"),
            Col(key="ultima", label="Última visita", tipo="texto"), Col(key="cada_dias", label="Vuelve cada (días)", tipo="numero")]
    return _out("ranking-clientes", "Clientes que más reservan (hasta 100)", desde, hasta, ctx, cols, filas, totales)


async def clientes_reservas(db: AsyncSession, tid: UUID, ctx: Ctx, desde: date, hasta: date, agrupar: str, staff_id: UUID | None) -> ReportOut:
    """Clientes distintos que reservaron en cada periodo: nuevos (su primera reserva del negocio cae ahí) vs recurrentes."""
    a, b = utc_range(desde, hasta, ctx)
    g, fmt = GROUP[agrupar]
    prm = dict(g=g, f=fmt, tz=ctx.tz_name, t=tid, a=a, b=b)
    rows = await _rows(db, (
        f"WITH primera AS (SELECT customer_id, min(inicio) AS f FROM appointments WHERE tenant_id = :t AND deleted_at IS NULL AND {_VIVAS} GROUP BY 1), "  # noqa: S608
        "v AS (SELECT DISTINCT x.customer_id, date_trunc(CAST(:g AS text), x.inicio AT TIME ZONE CAST(:tz AS text)) AS p, "
        "  date_trunc(CAST(:g AS text), pr.f AT TIME ZONE CAST(:tz AS text)) AS fp FROM appointments x JOIN primera pr ON pr.customer_id = x.customer_id "
        f"  WHERE x.tenant_id = :t AND x.deleted_at IS NULL AND x.{_VIVAS} AND x.inicio >= :a AND x.inicio < :b) "  # noqa: S608
        "SELECT to_char(p, CAST(:f AS text)) AS periodo, count(*) FILTER (WHERE p = fp) AS nuevos, count(*) FILTER (WHERE p > fp) AS recurrentes "
        "FROM v GROUP BY p ORDER BY p"), **prm)
    filas = [{"periodo": r.periodo, "nuevos": r.nuevos, "recurrentes": r.recurrentes, "total": r.nuevos + r.recurrentes,
              "pct_recurrentes": pct(r.recurrentes, r.nuevos + r.recurrentes)} for r in rows]
    t = (await db.execute(text(
        f"WITH primera AS (SELECT customer_id, min(inicio) AS f FROM appointments WHERE tenant_id = :t AND deleted_at IS NULL AND {_VIVAS} GROUP BY 1) "  # noqa: S608
        "SELECT count(*) FILTER (WHERE f >= :a) AS nuevos, count(*) FILTER (WHERE f < :a) AS recurrentes FROM primera pr WHERE EXISTS "
        f"(SELECT 1 FROM appointments x WHERE x.tenant_id = :t AND x.deleted_at IS NULL AND x.{_VIVAS} AND x.customer_id = pr.customer_id AND x.inicio >= :a AND x.inicio < :b)"),  # noqa: S608
        prm)).one()
    totales = {"periodo": "Total", "nuevos": t.nuevos, "recurrentes": t.recurrentes, "total": t.nuevos + t.recurrentes, "pct_recurrentes": pct(t.recurrentes, t.nuevos + t.recurrentes)}
    cols = [Col(key="periodo", label="Periodo", tipo="texto"), Col(key="nuevos", label="Nuevos", tipo="numero"), Col(key="recurrentes", label="Recurrentes", tipo="numero"),
            Col(key="total", label="Clientes que reservaron", tipo="numero"), Col(key="pct_recurrentes", label="% recurrentes", tipo="porcentaje")]
    return _out("clientes-reservas", "Clientes nuevos vs recurrentes (por reservas)", desde, hasta, ctx, cols, filas, totales)


async def reservas_profesional(db: AsyncSession, tid: UUID, ctx: Ctx, desde: date, hasta: date, agrupar: str, staff_id: UUID | None) -> ReportOut:
    a, b = utc_range(desde, hasta, ctx)
    rows = await _rows(db, (
        "SELECT coalesce(sp.nombre_publico, 'Sin profesional') AS profesional, count(*) AS reservas, count(*) FILTER (WHERE x.estado = 'atendida') AS atendidas, "
        "count(*) FILTER (WHERE x.estado = 'cancelada') AS canceladas, count(*) FILTER (WHERE x.estado = 'no_asistio') AS no_asistio, "
        "count(DISTINCT x.customer_id) AS clientes FROM appointments x LEFT JOIN staff_profiles sp ON sp.tenant_id = x.tenant_id AND sp.id = x.staff_id "
        "WHERE x.tenant_id = :t AND x.deleted_at IS NULL AND x.inicio >= :a AND x.inicio < :b AND (CAST(:s AS uuid) IS NULL OR x.staff_id = :s) "
        "GROUP BY 1 ORDER BY reservas DESC"), t=tid, a=a, b=b, s=staff_id)
    filas = [{"profesional": r.profesional, "reservas": r.reservas, "atendidas": r.atendidas, "canceladas": r.canceladas, "no_asistio": r.no_asistio,
              "clientes": r.clientes} for r in rows]
    totales = {"profesional": "Total", **{k: sum(f[k] for f in filas) for k in ("reservas", "atendidas", "canceladas", "no_asistio")}, "clientes": 0}
    cols = [Col(key="profesional", label="Profesional", tipo="texto"), Col(key="reservas", label="Reservas", tipo="numero"), Col(key="atendidas", label="Atendidas", tipo="numero"),
            Col(key="canceladas", label="Canceladas", tipo="numero"), Col(key="no_asistio", label="No vino", tipo="numero"), Col(key="clientes", label="Clientes distintos", tipo="numero")]
    return _out("reservas-profesional", "Reservas por profesional", desde, hasta, ctx, cols, filas, totales)


async def reservas_servicio(db: AsyncSession, tid: UUID, ctx: Ctx, desde: date, hasta: date, agrupar: str, staff_id: UUID | None) -> ReportOut:
    a, b = utc_range(desde, hasta, ctx)
    rows = await _rows(db, (
        "SELECT sv.nombre AS servicio, count(*) AS reservas, count(*) FILTER (WHERE x.estado = 'atendida') AS atendidas, "
        "count(*) FILTER (WHERE x.estado = 'cancelada') AS canceladas FROM appointment_services aps "
        "JOIN appointments x ON x.tenant_id = aps.tenant_id AND x.id = aps.appointment_id JOIN services sv ON sv.tenant_id = aps.tenant_id AND sv.id = aps.service_id "
        "WHERE x.tenant_id = :t AND x.deleted_at IS NULL AND x.inicio >= :a AND x.inicio < :b AND (CAST(:s AS uuid) IS NULL OR x.staff_id = :s) "
        "GROUP BY sv.id, sv.nombre ORDER BY reservas DESC, sv.nombre"), t=tid, a=a, b=b, s=staff_id)
    filas = [{"servicio": r.servicio, "reservas": r.reservas, "atendidas": r.atendidas, "canceladas": r.canceladas} for r in rows]
    totales = {"servicio": "Total", **{k: sum(f[k] for f in filas) for k in ("reservas", "atendidas", "canceladas")}}
    cols = [Col(key="servicio", label="Servicio", tipo="texto"), Col(key="reservas", label="Reservas", tipo="numero"),
            Col(key="atendidas", label="Atendidas", tipo="numero"), Col(key="canceladas", label="Canceladas", tipo="numero")]
    return _out("reservas-servicio", "Reservas por servicio", desde, hasta, ctx, cols, filas, totales)


REPORTS = {"ventas": ventas, "por-profesional": por_profesional, "por-servicio": por_servicio, "por-metodo": por_metodo,
           "ocupacion": ocupacion, "clientes": clientes, "citas": citas,
           "ranking-clientes": ranking_clientes, "clientes-reservas": clientes_reservas, "reservas-profesional": reservas_profesional,
           "reservas-servicio": reservas_servicio}


async def run(db: AsyncSession, p: Principal, name: str, desde: date, hasta: date, agrupar: str, staff_id: UUID | None) -> ReportOut:
    ctx = await get_ctx(db, p.tid)
    return await REPORTS[name](db, p.tid, ctx, desde, hasta, agrupar, staff_id)
