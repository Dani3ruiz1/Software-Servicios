"""Flujo de plataforma contra Postgres real (BD desechable con schema+rls+seed, rol agenda_app)."""
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.core.db import scoped_session
from app.core.errors import AppError
from app.core.tenancy import Principal, ensure_tenant_active
from app.modules.platform import service
from app.modules.platform.schemas import AdminIn, PaymentIn, TenantCreate, TenantModuleIn


async def _sa() -> Principal:
    try:
        async with scoped_session(platform=True) as s:
            sid = (await s.execute(text("SELECT id FROM platform_users WHERE email='superadmin@agenda-saas.co'"))).scalar_one()
    except Exception:
        pytest.skip("Postgres con seed no disponible")
    return Principal(sid, "platform_user", None, "super_admin")


async def _new_tenant(sa: Principal, plan: str = "Básico"):
    async with scoped_session(platform=True) as s:
        plan_id = (await s.execute(text("SELECT id FROM plans WHERE nombre=:n"), {"n": plan})).scalar_one()
        slug = f"neg-{uuid4().hex[:10]}"
        d = TenantCreate(nombre="Barbería Prueba", slug=slug, business_type="barberia", plan_id=plan_id,
                         admin=AdminIn(nombre="Admin", email=f"{slug}@test.co", password="Clave.Larga.123"))
        return await service.create_tenant(s, sa, d, "127.0.0.1")


async def _set_fin(tid, days_from_today: int):
    async with scoped_session(platform=True) as s:
        await s.execute(text(
            "UPDATE tenant_subscriptions SET fecha_inicio = LEAST(fecha_inicio, v.f), fecha_fin = v.f "
            "FROM (SELECT (now() AT TIME ZONE 'America/Bogota')::date + CAST(:d AS int) AS f) v "
            "WHERE tenant_id=:t AND estado='vigente'"), {"d": days_from_today, "t": tid})


async def _blocked(tid) -> bool:
    try:
        async with scoped_session(tenant_id=tid) as s:
            await ensure_tenant_active(s, tid)
        return False
    except AppError as e:
        assert e.code == "TENANT_BLOCKED"
        return True


async def test_create_tenant_seeds_everything():
    sa = await _sa()
    t = await _new_tenant(sa)
    assert t.estado == "activa" and t.usuarios_activos == 1 and t.modo == "empresa"
    assert {m.codigo for m in t.modulos} == {"calendar", "reception", "cash", "booking"}
    async with scoped_session(tenant_id=t.id) as s:
        assert (await s.execute(text("SELECT count(*) FROM services"))).scalar() >= 4   # servicios de ejemplo
    assert t.facturacion.total_mensual == Decimal("79000")


async def test_expiry_blocks_and_payment_unblocks_instantly():
    sa = await _sa()
    t = await _new_tenant(sa)
    assert not await _blocked(t.id)
    await _set_fin(t.id, -1)
    assert await _blocked(t.id)                       # bloqueado aunque el job aún no corrió
    async with scoped_session(platform=True) as s:
        hoy = (await s.execute(text("SELECT (now() AT TIME ZONE 'America/Bogota')::date"))).scalar_one()
        r = await service.register_payment(s, sa, t.id, PaymentIn(
            monto=Decimal("79000"), fecha_pago=hoy, periodo_desde=hoy, periodo_hasta=hoy + timedelta(days=30), metodo="transferencia"), None)
    assert r.estado_negocio == "activa"
    assert not await _blocked(t.id)                   # desbloqueo inmediato


async def test_manual_suspension_survives_payment_and_job_until_activated():
    sa = await _sa()
    t = await _new_tenant(sa)
    async with scoped_session(platform=True) as s:
        await service.set_estado(s, sa, t.id, "suspender", None)
    assert await _blocked(t.id)
    async with scoped_session(platform=True) as s:
        await service.sync_subscription_states(s)
        d = await service.get_tenant(s, t.id)
    assert d.estado == "suspendida" and await _blocked(t.id)
    async with scoped_session(platform=True) as s:
        d = await service.set_estado(s, sa, t.id, "activar", None)
    assert d.estado == "activa" and not await _blocked(t.id)


async def test_additional_module_manual_price_in_breakdown():
    sa = await _sa()
    t = await _new_tenant(sa)
    async with scoped_session(platform=True) as s:
        d = await service.set_tenant_module(s, sa, t.id, "ai", TenantModuleIn(precio_override=Decimal("35000")), None)
    assert d.facturacion.total_mensual == Decimal("114000")
    with pytest.raises(AppError) as e:               # reception ya viene en el plan
        async with scoped_session(platform=True) as s:
            await service.set_tenant_module(s, sa, t.id, "reception", TenantModuleIn(), None)
    assert e.value.code == "CONFLICT"


async def test_daily_job_states_and_idempotent_reminders():
    sa = await _sa()
    t = await _new_tenant(sa)
    await _set_fin(t.id, 3)
    for _ in range(2):                               # dos corridas: un solo aviso
        async with scoped_session(platform=True) as s:
            await service.sync_subscription_states(s)
            await service.queue_expiry_reminders(s)
    async with scoped_session(platform=True) as s:
        assert (await service.get_tenant(s, t.id)).estado == "por_vencer"
        n = (await s.execute(text("SELECT count(*) FROM notifications WHERE tenant_id=:t AND plantilla='suscripcion_por_vencer'"),
                             {"t": t.id})).scalar()
    assert n == 1
    await _set_fin(t.id, -2)
    async with scoped_session(platform=True) as s:
        await service.sync_subscription_states(s)
        assert (await service.get_tenant(s, t.id)).estado == "bloqueada"


async def test_tenant_cannot_see_other_tenant_subscription():
    sa = await _sa()
    a, b = await _new_tenant(sa), await _new_tenant(sa)
    async with scoped_session(tenant_id=a.id) as s:
        ids = {r[0] for r in (await s.execute(text("SELECT tenant_id FROM tenant_subscriptions"))).all()}
        pagos = (await s.execute(text("SELECT count(*) FROM subscription_payments WHERE tenant_id=:b"), {"b": b.id})).scalar()
    assert ids == {a.id} and pagos == 0
