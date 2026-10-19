"""Aislamiento multi-tenant, RLS, FK compuestas y anti doble reserva.

Requiere un Postgres 16 con schema.sql + rls.sql + seed.sql y conectado como agenda_app
(DATABASE_URL). Usar una BD DESECHABLE: las pruebas crean tenants y no los borran.
Se omiten solas si no hay base disponible.
"""
import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.core.db import scoped_session


async def _db_up() -> bool:
    try:
        async with scoped_session(platform=True) as s:
            await s.execute(text("SELECT 1 FROM business_types LIMIT 1"))
        return True
    except Exception:
        return False


@pytest.fixture
async def two_tenants():
    if not await _db_up():
        pytest.skip("Postgres no disponible")
    ids: list[UUID] = []
    async with scoped_session(platform=True) as s:
        for n in ("a", "b"):
            tid = uuid4()
            await s.execute(text("INSERT INTO tenants (id,nombre,slug,business_type) VALUES (:i,:n,:s,'barberia')"),
                            {"i": tid, "n": f"Neg {n}", "s": f"t-{n}-{tid.hex[:10]}"})
            ids.append(tid)
    return ids


async def _customer(tid: UUID) -> UUID:
    cid = uuid4()
    async with scoped_session(tenant_id=tid) as s:
        await s.execute(text("INSERT INTO customers (id,tenant_id,nombre) VALUES (:i,:t,'Cliente')"), {"i": cid, "t": tid})
    return cid


async def test_tenant_cannot_read_other_tenant(two_tenants):
    a, b = two_tenants
    cid = await _customer(a)
    async with scoped_session(tenant_id=b) as s:
        assert (await s.execute(text("SELECT count(*) FROM customers WHERE id=:i"), {"i": cid})).scalar() == 0
    async with scoped_session(tenant_id=a) as s:
        assert (await s.execute(text("SELECT count(*) FROM customers WHERE id=:i"), {"i": cid})).scalar() == 1


async def test_no_context_sees_nothing(two_tenants):
    await _customer(two_tenants[0])
    async with scoped_session() as s:
        assert (await s.execute(text("SELECT count(*) FROM customers"))).scalar() == 0


async def test_tenant_cannot_write_into_other_tenant(two_tenants):
    a, b = two_tenants
    with pytest.raises(DBAPIError):          # viola WITH CHECK de RLS
        async with scoped_session(tenant_id=b) as s:
            await s.execute(text("INSERT INTO customers (tenant_id,nombre) VALUES (:t,'intruso')"), {"t": a})


async def test_tenant_cannot_update_or_delete_other_tenant(two_tenants):
    a, b = two_tenants
    cid = await _customer(a)
    async with scoped_session(tenant_id=b) as s:
        assert (await s.execute(text("UPDATE customers SET nombre='x' WHERE id=:i"), {"i": cid})).rowcount == 0
        assert (await s.execute(text("DELETE FROM customers WHERE id=:i"), {"i": cid})).rowcount == 0


async def test_composite_fk_blocks_cross_tenant_link_even_for_platform(two_tenants):
    a, b = two_tenants
    cid_a = await _customer(a)
    with pytest.raises(IntegrityError):      # cita del tenant B apuntando a cliente del tenant A
        async with scoped_session(platform=True) as s:
            await s.execute(text("INSERT INTO appointments (tenant_id,customer_id,inicio,fin) "
                                 "VALUES (:t,:c,now(),now()+interval '30 min')"), {"t": b, "c": cid_a})


async def _staff(tid: UUID) -> UUID:
    sid = uuid4()
    async with scoped_session(tenant_id=tid) as s:
        await s.execute(text("INSERT INTO staff_profiles (id,tenant_id,nombre_publico) VALUES (:i,:t,'Pro')"), {"i": sid, "t": tid})
    return sid


async def _book(tid: UUID, cid: UUID, sid: UUID, start: datetime, minutes: int = 30) -> None:
    async with scoped_session(tenant_id=tid) as s:
        await s.execute(text("INSERT INTO appointments (tenant_id,customer_id,staff_id,inicio,fin) "
                             "VALUES (:t,:c,:s,:i,:f)"),
                        {"t": tid, "c": cid, "s": sid, "i": start, "f": start + timedelta(minutes=minutes)})


async def test_overlap_rejected_adjacent_allowed(two_tenants):
    a, _ = two_tenants
    cid, sid = await _customer(a), await _staff(a)
    t0 = datetime.now(UTC).replace(microsecond=0) + timedelta(days=1)
    await _book(a, cid, sid, t0)
    with pytest.raises(DBAPIError) as e:
        await _book(a, cid, sid, t0 + timedelta(minutes=15))
    assert "ex_appointments_no_overlap" in str(e.value)
    await _book(a, cid, sid, t0 + timedelta(minutes=30))     # consecutiva: permitida


async def test_concurrent_booking_only_one_wins(two_tenants):
    a, _ = two_tenants
    cid, sid = await _customer(a), await _staff(a)
    t0 = datetime.now(UTC).replace(microsecond=0) + timedelta(days=2)
    res = await asyncio.gather(*[_book(a, cid, sid, t0) for _ in range(10)], return_exceptions=True)
    assert sum(r is None for r in res) == 1
    assert all(isinstance(r, DBAPIError) for r in res if r is not None)
