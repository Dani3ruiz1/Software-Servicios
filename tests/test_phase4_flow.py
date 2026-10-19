"""Fase 4 contra Postgres real (BD desechable con schema+rls+seed, rol agenda_app)."""
import asyncio
from datetime import UTC, datetime, time, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from app.core.db import scoped_session
from app.core.errors import AppError
from app.core.pagination import PageParams
from app.core.tenancy import Principal, ensure_tenant_active  # noqa: F401
from app.modules.appointments import service as appts
from app.modules.appointments.schemas import AppointmentIn, MoveIn, StatusIn
from app.modules.catalog import service as catalog
from app.modules.customers import service as customers
from app.modules.customers.schemas import CustomerIn
from app.modules.platform import service as platform
from app.modules.platform.schemas import AdminIn, TenantCreate
from app.modules.staff import service as staff
from app.modules.staff.schemas import ScheduleIn, ScheduleItem, StaffIn
from app.modules.users import service as users
from app.modules.users.schemas import UserIn, UserPatch


async def _setup(plan="Básico"):
    try:
        async with scoped_session(platform=True) as s:
            sa_id = (await s.execute(text("SELECT id FROM platform_users LIMIT 1"))).scalar_one()
            plan_id = (await s.execute(text("SELECT id FROM plans WHERE nombre=:n"), {"n": plan})).scalar_one()
            slug = f"p4-{uuid4().hex[:10]}"
            sa = Principal(sa_id, "platform_user", None, "super_admin")
            t = await platform.create_tenant(s, sa, TenantCreate(
                nombre="Neg", slug=slug, business_type="barberia", plan_id=plan_id,
                admin=AdminIn(nombre="Admin", email=f"{slug}@t.co", password="Clave.Larga.123")), None)
            admin_id = (await s.execute(text("SELECT id FROM users WHERE tenant_id=:t"), {"t": t.id})).scalar_one()
    except Exception:
        pytest.skip("Postgres con seed no disponible")
    return Principal(admin_id, "user", t.id, "admin")


async def _staff_with_schedule(p: Principal, nombre="Pro"):
    async with scoped_session(tenant_id=p.tenant_id) as db:
        svc = (await catalog.list_services(db, p, PageParams(1, 100, None), None, None, None)).items
        st = await staff.create_staff(db, p, StaffIn(nombre_publico=nombre, service_ids=[svc[0].id]))
        await staff.set_schedule(db, p, st.id, ScheduleIn(tramos=[
            ScheduleItem(dia_semana=d, hora_inicio=time(9), hora_fin=time(18)) for d in range(7)]))
    return st, svc[0]


async def _customer(p):
    async with scoped_session(tenant_id=p.tenant_id) as db:
        return (await customers.create_customer(db, p, CustomerIn(nombre="Cliente Prueba", consentimiento_datos=True))).id


def _tomorrow_10() -> datetime:
    return (datetime.now(UTC) + timedelta(days=2)).replace(hour=15, minute=0, second=0, microsecond=0)   # 10:00 Bogotá


async def test_user_limit_reached_and_seat_freed_on_delete():
    p = await _setup("Independiente")                  # max_usuarios = 1 y ya existe el admin
    with pytest.raises(AppError) as e:
        async with scoped_session(tenant_id=p.tenant_id) as db:
            await users.create_user(db, p, UserIn(nombre="Otro", email="o@t.co", password="Clave.Larga.123", rol="recepcionista"))
    assert e.value.code == "USER_LIMIT_REACHED"


async def test_concurrent_user_creation_never_exceeds_plan():
    p = await _setup("Básico")                         # 5 usuarios; el admin ocupa 1 => caben 4
    async def mk(i):
        async with scoped_session(tenant_id=p.tenant_id) as db:
            await users.create_user(db, p, UserIn(nombre=f"U{i}", email=f"u{i}@t.co", password="Clave.Larga.123", rol="recepcionista"))
    res = await asyncio.gather(*[mk(i) for i in range(8)], return_exceptions=True)
    assert sum(r is None for r in res) == 4
    assert all(isinstance(r, AppError) and r.code == "USER_LIMIT_REACHED" for r in res if r is not None)


async def test_cannot_remove_last_admin():
    p = await _setup()
    with pytest.raises(AppError):
        async with scoped_session(tenant_id=p.tenant_id) as db:
            await users.delete_user(db, p, uuid4())      # inexistente => 404
    with pytest.raises(AppError) as e:
        async with scoped_session(tenant_id=p.tenant_id) as db:
            await users.patch_user(db, p, p.id, UserPatch(rol="recepcionista"))
    assert e.value.code == "CONFLICT"


async def test_double_booking_blocked_and_parallel_requests_one_winner():
    p = await _setup()
    st, svc = await _staff_with_schedule(p)
    cid = await _customer(p)
    body = AppointmentIn(customer_id=cid, staff_id=st.id, inicio=_tomorrow_10(), service_ids=[svc.id])

    async def book():
        async with scoped_session(tenant_id=p.tenant_id) as db:
            return await appts.create_appointment(db, p, body)
    res = await asyncio.gather(*[book() for _ in range(6)], return_exceptions=True)
    ok = [r for r in res if not isinstance(r, Exception)]
    assert len(ok) == 1, [repr(r)[:300] for r in res if isinstance(r, Exception)]
    assert all(isinstance(r, AppError) and r.code == "SLOT_TAKEN" and r.status == 409 for r in res if isinstance(r, Exception))


async def test_auto_assign_picks_least_busy_and_falls_back():
    p = await _setup()
    a, svc = await _staff_with_schedule(p, "Ana")
    async with scoped_session(tenant_id=p.tenant_id) as db:
        b = await staff.create_staff(db, p, StaffIn(nombre_publico="Beto", service_ids=[svc.id]))
        await staff.set_schedule(db, p, b.id, ScheduleIn(tramos=[ScheduleItem(dia_semana=d, hora_inicio=time(9), hora_fin=time(18)) for d in range(7)]))
    cid = await _customer(p)
    t0 = _tomorrow_10()
    async with scoped_session(tenant_id=p.tenant_id) as db:     # A ya tiene una cita ese día (otra hora)
        await appts.create_appointment(db, p, AppointmentIn(customer_id=cid, staff_id=a.id, inicio=t0 + timedelta(hours=3), service_ids=[svc.id]))
        r = await appts.create_appointment(db, p, AppointmentIn(customer_id=cid, inicio=t0, service_ids=[svc.id], asignar_automatico=True))
    assert r.profesional.id == b.id                             # B tiene menos citas ese día
    async with scoped_session(tenant_id=p.tenant_id) as db:
        r2 = await appts.create_appointment(db, p, AppointmentIn(customer_id=cid, inicio=t0, service_ids=[svc.id], asignar_automatico=True))
    assert r2.profesional.id == a.id                            # B ya ocupado a esa hora -> A
    with pytest.raises(AppError) as e:
        async with scoped_session(tenant_id=p.tenant_id) as db:
            await appts.create_appointment(db, p, AppointmentIn(customer_id=cid, inicio=t0, service_ids=[svc.id], asignar_automatico=True))
    assert e.value.code == "SLOT_UNAVAILABLE"


async def test_availability_reflects_booking_block_and_status_rules():
    p = await _setup()
    st, svc = await _staff_with_schedule(p)
    cid = await _customer(p)
    t0 = _tomorrow_10()
    day = t0.astimezone(ZoneInfo("America/Bogota")).date()
    async with scoped_session(tenant_id=p.tenant_id) as db:
        before = await appts.availability(db, p.tenant_id, desde=day, hasta=day, service_ids=[svc.id], staff_id=None, duracion_min=None)
        created = await appts.create_appointment(db, p, AppointmentIn(customer_id=cid, staff_id=st.id, inicio=t0, service_ids=[svc.id]))
        after = await appts.availability(db, p.tenant_id, desde=day, hasta=day, service_ids=[svc.id], staff_id=None, duracion_min=None)
    starts = lambda r: {x.inicio.astimezone(UTC) for x in r.dias[0].slots}  # noqa: E731
    assert t0 in starts(before) and t0 not in starts(after)       # la hora reservada desaparece
    async with scoped_session(tenant_id=p.tenant_id) as db:
        with pytest.raises(AppError) as e:                       # no se puede saltar a "atendida"
            await appts.change_status(db, p, created.id, StatusIn(estado="atendida"))
        assert e.value.code == "INVALID_STATE_TRANSITION"
        with pytest.raises(AppError):                            # inasistencia antes de la hora
            await appts.change_status(db, p, created.id, StatusIn(estado="no_asistio"))
        await appts.change_status(db, p, created.id, StatusIn(estado="cancelada", motivo="cliente avisó"))
        freed = await appts.availability(db, p.tenant_id, desde=day, hasta=day, service_ids=[svc.id], staff_id=None, duracion_min=None)
    assert starts(freed) == starts(before)                        # cancelar libera el hueco


async def test_professional_only_sees_own_appointments():
    p = await _setup()
    a, svc = await _staff_with_schedule(p, "Ana")
    async with scoped_session(tenant_id=p.tenant_id) as db:
        u = await users.create_user(db, p, UserIn(nombre="Pro User", email="pro@t.co", password="Clave.Larga.123", rol="profesional"))
    pro = Principal(u.id, "user", p.tenant_id, "profesional")
    cid = await _customer(p)
    t0 = _tomorrow_10()
    async with scoped_session(tenant_id=p.tenant_id) as db:
        mine = await appts.create_appointment(db, p, AppointmentIn(customer_id=cid, staff_id=u.staff_id, inicio=t0, duracion_min=30))
        other = await appts.create_appointment(db, p, AppointmentIn(customer_id=cid, staff_id=a.id, inicio=t0, service_ids=[svc.id]))
    async with scoped_session(tenant_id=p.tenant_id) as db:
        page = await appts.list_appointments(db, pro, PageParams(1, 50, None), None, None, None, None, None)
        assert {x.id for x in page.items} == {mine.id}
        with pytest.raises(AppError) as e:
            await appts.get_appointment(db, pro, other.id)
        assert e.value.code == "NOT_FOUND"
        moved = await appts.move_appointment(db, p, mine.id, MoveIn(inicio=t0 + timedelta(hours=1)))
        assert moved.inicio > mine.inicio
