# ruff: noqa: S311, S105, S106 - datos de demostración: aleatoriedad no criptográfica y contraseña de desarrollo a propósito
"""DATOS DE DEMOSTRACIÓN para ver el sistema con vida (SOLO desarrollo; se niega a correr en producción).

Crea, sobre una base con schema.sql + rls.sql + seed.sql cargados, varios negocios de distintos tipos y planes, con sus empleados, clientes
y unos 2 meses de reservas y ventas (más algunas reservas futuras), de forma que el tablero del Super Admin, los reportes y la agenda
tengan datos creíbles. Es determinista (misma semilla = mismos datos). Todos los usuarios usan la contraseña de desarrollo `DEV_PASSWORD`.

    python scripts/seed_demo.py            # sobre una base vacía
"""
import asyncio
import random
import sys
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.db import dispose_engine, scoped_session  # noqa: E402
from app.core.tenancy import Principal  # noqa: E402
from app.modules.booking import service as booking  # noqa: E402
from app.modules.booking.schemas import SettingsIn  # noqa: E402
from app.modules.platform import service as platform  # noqa: E402
from app.modules.platform.schemas import AdminIn, TenantCreate  # noqa: E402
from app.modules.staff import service as staff_service  # noqa: E402
from app.modules.staff.schemas import AccessIn  # noqa: E402

DEV_PASSWORD = "Clave.Larga.123"
TZ = ZoneInfo("America/Bogota")
RNG = random.Random(2026)

# nombre, código, tipo, plan, meses de antigüedad, equipo, popularidad (0-1), días de vigencia (negativo = vencido)
NEGOCIOS = [
    ("Barbería Demo", "demo", "barberia", "Profesional", 5, ["Andrés", "Camila"], 0.80, 40),
    ("Barbería Don Pepe", "barberia-don-pepe", "barberia", "Premium", 6, ["Pepe", "Julián", "Mateo", "Sebastián"], 0.85, 55),
    ("La Navaja Dorada", "navaja-dorada", "barberia", "Profesional", 4, ["Esteban", "Camilo", "Andrey"], 0.70, 25),
    ("Corte y Estilo Medellín", "corte-estilo-medellin", "barberia", "Básico", 3, ["Santiago", "Luis"], 0.60, 4),
    ("Barbería El Patrón", "barberia-el-patron", "barberia", "Independiente", 2, ["Rafael"], 0.55, 18),
    ("Oasis Spa Bogotá", "oasis-spa-bogota", "spa", "Premium", 6, ["Daniela", "Natalia", "Paula"], 0.65, 33),
    ("Spa Aurora Medellín", "spa-aurora-medellin", "spa", "Básico", 3, ["Marcela", "Sofía"], 0.50, 9),
    ("Uñas Glam Studio", "unas-glam-studio", "unas", "Profesional", 4, ["Valeria", "Karen", "Mariana"], 0.75, 21),
    ("Nails by Vale", "nails-by-vale", "unas", "Independiente", 1, ["Valentina"], 0.60, 27),
    ("Centro Estético Lumière", "estetica-lumiere", "estetica", "Profesional", 5, ["Juliana", "Carolina"], 0.55, -3),
    ("Salón Bella Vista", "salon-bella-vista", "salon", "Básico", 1, ["Ana", "Lucía"], 0.40, 11),
]
# colores de marca por tipo: primario, secundario, fondo
MARCA = {
    "barberia": ("#1D3557", "#E63946", "#FFF9F0"), "spa": ("#2F6F5E", "#C9A66B", "#F4FAF6"), "unas": ("#C2185B", "#7E57C2", "#FFF5F9"),
    "estetica": ("#6D4C9F", "#F2A65A", "#FBF8FF"), "salon": ("#D1495B", "#2E86AB", "#FFF8F6"),
}
PALETA = ["#E63946", "#1D6FB8", "#F2A541", "#2E9E73", "#8E5DB8", "#D1495B", "#2A9D8F", "#E07A5F"]
NOMBRES = ["Carlos", "Laura", "Andrés", "Valentina", "Juan", "Camila", "Santiago", "Daniela", "Mateo", "Sofía", "Sebastián", "Isabella", "Nicolás", "Mariana",
           "David", "Luisa", "Felipe", "Natalia", "Miguel", "Paula", "Alejandro", "Juliana", "Daniel", "Carolina", "Esteban", "Manuela", "Julián", "Gabriela"]
APELLIDOS = ["Pérez", "Gómez", "Rodríguez", "Martínez", "López", "García", "Hernández", "Ramírez", "Torres", "Vargas", "Castro", "Rojas", "Moreno", "Jiménez",
             "Ortiz", "Muñoz", "Ríos", "Cardona", "Londoño", "Restrepo"]
# negocios que dejaron de recibir reservas hace días (para que el tablero muestre alertas de retención): último día con reservas, relativo a hoy
ULTIMO_DIA = {"salon-bella-vista": -16, "nails-by-vale": -3}
DOW_PESO = {0: 0.75, 1: 0.9, 2: 0.95, 3: 1.0, 4: 1.15, 5: 1.3, 6: 0.0}      # lunes … domingo (cerrado)


async def crear_negocio(n: tuple, planes: dict[str, UUID], sa: Principal, hoy: date) -> tuple[UUID, UUID]:  # type: ignore[type-arg]
    nombre, slug, tipo, plan, meses, equipo, _pop, vigencia = n
    creado = hoy - timedelta(days=meses * 30)
    async with scoped_session(platform=True) as db:
        t = await platform.create_tenant(db, sa, TenantCreate(
            nombre=nombre, slug=slug, business_type=tipo, plan_id=planes[plan], fecha_inicio=creado, fecha_fin=hoy + timedelta(days=vigencia),
            admin=AdminIn(nombre=f"Admin {nombre}", email=f"admin@{slug}.co", password=DEV_PASSWORD), contacto_email=f"hola@{slug}.co",
            contacto_telefono=f"3{RNG.randint(100000000, 199999999)}"), None)
        await db.execute(text("UPDATE tenants SET created_at = :c, updated_at = :c WHERE id = :t"),
                         {"c": datetime.combine(creado, time(9), TZ), "t": t.id})
        admin_id = (await db.execute(text("SELECT id FROM users WHERE tenant_id = :t AND rol = 'admin'"), {"t": t.id})).scalar_one()
    p = Principal(admin_id, "user", t.id, "admin")
    async with scoped_session(tenant_id=t.id) as db:                        # página de reservas activa y con los colores del tipo de negocio
        prim, sec, fondo = MARCA[tipo]
        await booking.put_admin_settings(db, p, SettingsIn(slug=slug, activo=True, color_primario=prim, color_secundario=sec, color_fondo=fondo,
                                                           anticipacion_min_horas=1, cancelacion_min_horas=3))
    async with scoped_session(platform=True) as db:                         # equipo: horario lunes a sábado con descanso y todos los servicios (o casi)
        servicios = (await db.execute(text("SELECT id FROM services WHERE tenant_id = :t AND deleted_at IS NULL"), {"t": t.id})).scalars().all()
        for i, nom in enumerate(equipo):
            sid = uuid4()
            await db.execute(text("INSERT INTO staff_profiles (id, tenant_id, nombre_publico, color_calendario) VALUES (:i, :t, :n, :c)"),
                             {"i": sid, "t": t.id, "n": nom, "c": PALETA[i % len(PALETA)]})
            for d in range(7 if slug == "demo" else 6):          # el negocio demo abre también los domingos, para que al probarlo siempre haya reservas hoy
                if d == 6:
                    tramos = ((time(10), time(16)),)
                else:
                    tramos = ((time(9), time(13)), (time(14), time(17) if d == 5 else time(19)))
                for ini, fi in tramos:
                    await db.execute(text("INSERT INTO staff_schedules (tenant_id, staff_id, dia_semana, hora_inicio, hora_fin) VALUES (:t, :s, :d, :a, :b)"),
                                     {"t": t.id, "s": sid, "d": d, "a": ini, "b": fi})
            for k, sv in enumerate(servicios):
                if RNG.random() < 0.85 or k % len(equipo) == i:      # todo servicio queda asignado a alguien del equipo
                    await db.execute(text("INSERT INTO staff_services (tenant_id, staff_id, service_id) VALUES (:t, :s, :v)"), {"t": t.id, "s": sid, "v": sv})
    return t.id, admin_id


async def poblar(tid: UUID, n: tuple, hoy: date) -> dict[str, int]:  # type: ignore[type-arg]
    _, slug, _, _, meses, _, pop, _ = n
    ahora = datetime.now(UTC)
    inicio_negocio = hoy - timedelta(days=meses * 30)
    async with scoped_session(platform=True) as db:
        staff = [(r[0], r[1]) for r in (await db.execute(text("SELECT id, nombre_publico FROM staff_profiles WHERE tenant_id = :t ORDER BY created_at, nombre_publico"), {"t": tid})).all()]
        servicios = [(r[0], int(r[1]), Decimal(r[2])) for r in (await db.execute(text(
            "SELECT id, duracion_min, precio FROM services WHERE tenant_id = :t AND deleted_at IS NULL ORDER BY precio"), {"t": tid})).all()]
        svc_de = {s: {r[0] for r in (await db.execute(text("SELECT service_id FROM staff_services WHERE staff_id = :s"), {"s": s})).all()} for s, _ in staff}

        # clientes (se van registrando a lo largo de la vida del negocio, más al principio)
        clientes: list[tuple[UUID, datetime]] = []
        n_clientes = int(25 + 70 * pop * len(staff) / 3)
        filas = []
        for _ in range(n_clientes):
            cid = uuid4()
            alta = datetime.combine(inicio_negocio + timedelta(days=int((hoy - inicio_negocio).days * RNG.random() ** 1.6)), time(RNG.randint(9, 18), RNG.choice([0, 15, 30, 45])), TZ)
            clientes.append((cid, alta))
            filas.append({"i": cid, "t": tid, "n": f"{RNG.choice(NOMBRES)} {RNG.choice(APELLIDOS)}", "tel": f"3{RNG.randint(0, 299999999):09d}", "c": alta,
                          "o": RNG.choices(["reserva_web", "whatsapp", "recepcion"], [52, 20, 28])[0]})
        await db.execute(text("INSERT INTO customers (id, tenant_id, nombre, telefono, consentimiento_datos, consentimiento_fecha, consentimiento_version, origen, created_at, updated_at) "
                              "VALUES (:i, :t, :n, :tel, true, :c, 'demo', :o, :c, :c)"), filas)

        # reservas: de hace 70 días (o desde que existe el negocio) hasta dentro de 7 días, sin choques por profesional
        citas, lineas, ventas, pagos = [], [], [], []
        dia = max(inicio_negocio, hoy - timedelta(days=70))
        while dia <= hoy + timedelta(days=ULTIMO_DIA.get(slug, 7)):
            peso = 0.8 if (slug == "demo" and dia.weekday() == 6) else DOW_PESO[dia.weekday()]
            edad = min(1.0, 0.35 + 0.65 * (dia - inicio_negocio).days / max((hoy - inicio_negocio).days, 1))        # el negocio va creciendo
            for sid, _nom in staff:
                if peso == 0:
                    break
                domingo_demo = slug == "demo" and dia.weekday() == 6
                t = datetime.combine(dia, time(10 if domingo_demo else 9), TZ)
                fin_dia = datetime.combine(dia, time(16 if domingo_demo else 17 if dia.weekday() == 5 else 19), TZ)
                while t < fin_dia:
                    if time(13) <= t.timetz().replace(tzinfo=None) < time(14):
                        t = datetime.combine(dia, time(14), TZ)
                        continue
                    opciones = [s for s in servicios if s[0] in svc_de[sid]] or servicios
                    sv, dur, precio = opciones[min(int(RNG.random() ** 1.5 * len(opciones)), len(opciones) - 1)]
                    fin = t + timedelta(minutes=dur)
                    if fin > fin_dia:
                        break
                    if RNG.random() < min(0.92, pop * peso * edad * 0.85):
                        elegibles = [c for c in clientes if c[1] <= t] or clientes
                        cid = RNG.choice(elegibles)[0]
                        if t.astimezone(UTC) < ahora:
                            creada = max(t - timedelta(days=RNG.random() * 6, hours=RNG.random() * 5), datetime.combine(inicio_negocio, time(9), TZ))
                        else:                                               # una cita futura se reservó en los últimos días, no "hoy"
                            creada = ahora - timedelta(hours=RNG.random() * 120)
                        if t.astimezone(UTC) < ahora:
                            estado = RNG.choices(["atendida", "cancelada", "no_asistio"], [80, 10, 10])[0]
                        else:
                            estado = RNG.choices(["pendiente", "confirmada"], [45, 55])[0]
                        aid = uuid4()
                        citas.append({"i": aid, "t": tid, "c": cid, "s": sid, "a": t, "f": fin, "e": estado, "o": RNG.choices(["reserva_web", "whatsapp", "recepcion"], [52, 20, 28])[0], "cr": creada})
                        lineas.append({"t": tid, "a": aid, "v": sv, "p": precio, "d": dur})
                        if estado == "atendida":
                            propina = Decimal(RNG.choice([0, 0, 0, 2000, 3000, 5000]))
                            vid = uuid4()
                            ventas.append({"i": vid, "t": tid, "c": cid, "s": sid, "a": aid, "sub": precio, "pr": propina, "tot": precio + propina, "pa": fin})
                            metodo = RNG.choices(["efectivo", "datafono", "transferencia"], [45, 25, 30])[0]
                            pagos.append({"t": tid, "v": vid, "m": metodo, "monto": precio + propina, "b": "Bancolombia" if metodo == "transferencia" else None, "ca": fin})
                    t = fin + timedelta(minutes=RNG.choice([0, 0, 15, 30]))
            dia += timedelta(days=1)

        for i in range(0, len(citas), 800):
            await db.execute(text("INSERT INTO appointments (id, tenant_id, customer_id, staff_id, inicio, fin, estado, origen, created_at, updated_at) "
                                  "VALUES (:i, :t, :c, :s, :a, :f, :e, :o, :cr, :cr)"), citas[i:i + 800])
        for i in range(0, len(lineas), 800):
            await db.execute(text("INSERT INTO appointment_services (tenant_id, appointment_id, service_id, precio_pactado, duracion_min) VALUES (:t, :a, :v, :p, :d)"), lineas[i:i + 800])
        for i in range(0, len(ventas), 800):
            await db.execute(text("INSERT INTO sales (id, tenant_id, customer_id, staff_id, appointment_id, subtotal, iva, propina, total, estado, pagada_en, created_at, updated_at) "
                                  "VALUES (:i, :t, :c, :s, :a, :sub, 0, :pr, :tot, 'pagada', :pa, :pa, :pa)"), ventas[i:i + 800])
        for i in range(0, len(pagos), 800):
            await db.execute(text("INSERT INTO payments (tenant_id, sale_id, metodo, monto, entidad_bancaria, created_at) VALUES (:t, :v, :m, :monto, :b, :ca)"), pagos[i:i + 800])
    return {"clientes": len(clientes), "citas": len(citas), "ventas": len(ventas)}


async def main() -> int:
    if get_settings().is_production:
        print("Este script es solo para desarrollo: se niega a correr en producción.")
        return 2
    async with scoped_session(platform=True) as db:
        if (await db.execute(text("SELECT 1 FROM tenants WHERE slug = 'demo'"))).first():
            print("Ya existen datos de demostración (negocio «demo»). Usa una base vacía.")
            return 1
        planes = {nombre: pid for pid, nombre in (await db.execute(text("SELECT id, nombre FROM plans"))).all()}
        sa_id = (await db.execute(text("SELECT id FROM platform_users LIMIT 1"))).scalar_one()
        hoy = (await db.execute(text("SELECT (now() AT TIME ZONE 'America/Bogota')::date"))).scalar_one()
    sa = Principal(sa_id, "platform_user", None, "super_admin")
    totales = {"clientes": 0, "citas": 0, "ventas": 0}
    demo: tuple[UUID, UUID] | None = None
    for n in NEGOCIOS:
        ids = await crear_negocio(n, planes, sa, hoy)
        r = await poblar(ids[0], n, hoy)
        for k, v in r.items():
            totales[k] += v
        if n[1] == "demo":
            demo = ids
        print(f"  {n[0]:<28} {n[3]:<14} {len(n[5])} empleado(s) · {r['clientes']:>3} clientes · {r['citas']:>4} citas · {r['ventas']:>4} ventas")
    # los empleados del negocio demo entran con su propio usuario a ver SU agenda
    assert demo is not None
    adm = Principal(demo[1], "user", demo[0], "admin")
    async with scoped_session(tenant_id=demo[0]) as db:
        for nombre, correo in (("Andrés", "andres@demo.co"), ("Camila", "camila@demo.co")):
            sid = (await db.execute(text("SELECT id FROM staff_profiles WHERE tenant_id = :t AND nombre_publico = :n"), {"t": demo[0], "n": nombre})).scalar_one()
            await staff_service.grant_access(db, adm, sid, AccessIn(email=correo, password=DEV_PASSWORD))
    async with scoped_session(platform=True) as db:                         # historial de cobros: un pago mensual por cada mes de vida de cada negocio
        await db.execute(text(
            "INSERT INTO subscription_payments (tenant_id, monto, fecha_pago, periodo_desde, periodo_hasta, metodo, referencia) "
            "SELECT x.tenant_id, x.precio_plan_aplicado, d::date, d::date, (d + interval '30 days')::date, (ARRAY['transferencia','efectivo','nequi'])[1 + (extract(month FROM d)::int % 3)], "
            "'DEMO-' || to_char(d, 'YYYYMM') FROM tenant_subscriptions x, "
            "LATERAL generate_series(x.fecha_inicio::timestamp, least(x.fecha_fin, current_date)::timestamp - interval '1 day', interval '30 days') d"))
    async with scoped_session(platform=True) as db:
        await platform.sync_subscription_states(db)                         # deja por vencer / vencido / bloqueado según la fecha de cada suscripción
    print(f"Listo: {len(NEGOCIOS)} negocios · {totales['clientes']} clientes · {totales['citas']} citas · {totales['ventas']} ventas.")
    await dispose_engine()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
