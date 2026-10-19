"""Inventario: reglas de stock, concurrencia, aislamiento entre negocios, libro de movimientos intocable y acceso según el plan."""
import asyncio
from decimal import Decimal

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_phase4_flow import _setup
from test_phase9_flow import PASSWORD, _needs_db, _sql, _user  # noqa: F401  (_needs_db: omite si no hay Postgres)

from app.core.db import scoped_session
from app.core.errors import AppError
from app.core.pagination import PageParams
from app.main import app
from app.modules.inventory import service as inv
from app.modules.inventory.schemas import MovementIn, ProductIn, ProductPatch

PP = PageParams(1, 50, None)


# ------------------------------------------------------------------ validación (no necesita base de datos)
def test_movement_rules():
    assert MovementIn(tipo="entrada", cantidad=3).cantidad == 3
    for malo in ({"tipo": "salida", "cantidad": 0}, {"tipo": "entrada", "cantidad": 0},
                 {"tipo": "ajuste", "cantidad": 2}, {"tipo": "ajuste", "cantidad": 2, "motivo": "  "},
                 {"tipo": "salida", "cantidad": 1, "costo_unitario": "100"}, {"tipo": "entrada", "cantidad": -1}):
        with pytest.raises(ValidationError):
            MovementIn(**malo)
    assert MovementIn(tipo="ajuste", cantidad=0, motivo="Se acabó").cantidad == 0           # un conteo en cero es válido


def test_product_in_rejects_negatives_and_short_names():
    for malo in ({"nombre": "x"}, {"nombre": "Cera", "costo": "-1"}, {"nombre": "Cera", "stock_inicial": -1}):
        with pytest.raises(ValidationError):
            ProductIn(**malo)


# ------------------------------------------------------------------ reglas de stock
async def test_stock_flow_ledger_and_alerts():
    p = await _setup("Profesional")
    async with scoped_session(tenant_id=p.tenant_id) as db:
        c = await inv.create_product(db, p, ProductIn(nombre="Cera para barba", sku="CERA-01", costo=Decimal("18000"), precio_venta=Decimal("35000"), stock_inicial=10, stock_minimo=3))
        assert (c.stock_actual, c.bajo_stock) == (10, False)
        hist = await inv.list_movements(db, p, c.id, PP)
        assert [(m.tipo, m.delta, m.stock_resultante, m.motivo) for m in hist.items] == [("entrada", 10, 10, "Stock inicial")]   # el stock siempre tiene explicación
        assert (await inv.add_movement(db, p, c.id, MovementIn(tipo="salida", cantidad=4, motivo="Venta"))).stock_resultante == 6
        with pytest.raises(AppError) as e:
            await inv.add_movement(db, p, c.id, MovementIn(tipo="salida", cantidad=10))
        assert e.value.code == "STOCK_INSUFFICIENT" and e.value.status == 409 and e.value.details == {"disponible": 6, "solicitado": 10}
        a = await inv.add_movement(db, p, c.id, MovementIn(tipo="ajuste", cantidad=2, motivo="Conteo físico"))
        assert (a.delta, a.stock_resultante) == (-4, 2)                                                                  # el ajuste fija el stock al conteo
        with pytest.raises(AppError) as e:
            await inv.add_movement(db, p, c.id, MovementIn(tipo="ajuste", cantidad=2, motivo="Otra vez"))
        assert e.value.code == "VALIDATION_ERROR"
        assert (await inv.get_product(db, p, c.id)).bajo_stock is True                                                  # 2 <= mínimo 3
        await inv.add_movement(db, p, c.id, MovementIn(tipo="entrada", cantidad=10, costo_unitario=Decimal("20000")))
        prod = await inv.get_product(db, p, c.id)
        assert (prod.stock_actual, prod.costo, prod.bajo_stock) == (12, Decimal("20000.00"), False)                   # la entrada actualiza el último costo
        s = await inv.summary(db, p)
        assert (s.productos, s.bajo_stock, s.valor_inventario) == (1, 0, Decimal("240000.00"))
        # el PATCH no toca el stock aunque se lo manden
        assert (await inv.patch_product(db, p, c.id, ProductPatch.model_validate({"stock_actual": 999, "nombre": "Cera barba"}))).stock_actual == 12


async def test_sku_unique_per_business_case_insensitive_and_reusable_after_delete():
    p, q = await _setup("Profesional"), await _setup("Profesional")
    async with scoped_session(tenant_id=p.tenant_id) as db:
        a = await inv.create_product(db, p, ProductIn(nombre="Shampoo", sku="Sh-1"))
        with pytest.raises(AppError) as e:
            await inv.create_product(db, p, ProductIn(nombre="Otro shampoo", sku="sh-1"))
        assert e.value.code == "CONFLICT"
        b = await inv.create_product(db, p, ProductIn(nombre="Sin código"))
        with pytest.raises(AppError):
            await inv.patch_product(db, p, b.id, ProductPatch(sku="SH-1"))
        await inv.delete_product(db, p, a.id)
        assert (await inv.list_products(db, p, PP, None, None, None)).total == 1                                           # el borrado lógico lo oculta
        await inv.create_product(db, p, ProductIn(nombre="Shampoo nuevo", sku="SH-1"))                                   # y libera el código
    async with scoped_session(tenant_id=q.tenant_id) as db:
        await inv.create_product(db, q, ProductIn(nombre="Shampoo", sku="SH-1"))                                         # otro negocio puede usar el mismo código


async def test_filters_and_search():
    p = await _setup("Profesional")
    async with scoped_session(tenant_id=p.tenant_id) as db:
        await inv.create_product(db, p, ProductIn(nombre="Gel fijador", sku="GEL", stock_inicial=2, stock_minimo=5))
        await inv.create_product(db, p, ProductIn(nombre="Navajas", sku="NAV", stock_inicial=50, stock_minimo=10))
        await inv.create_product(db, p, ProductIn(nombre="Toallas", stock_inicial=1, stock_minimo=0, activo=False))
        assert [x.nombre for x in (await inv.list_products(db, p, PP, None, None, True)).items] == ["Gel fijador"]      # bajo stock (mínimo 0 = sin alerta)
        assert (await inv.list_products(db, p, PP, "nav", None, None)).total == 1
        assert (await inv.list_products(db, p, PP, None, True, None)).total == 2
        assert (await inv.list_products(db, p, PageParams(1, 50, "-stock_actual"), None, None, None)).items[0].nombre == "Navajas"


# ------------------------------------------------------------------ concurrencia: el stock nunca queda negativo
async def test_concurrent_exits_never_oversell():
    p = await _setup("Profesional")
    async with scoped_session(tenant_id=p.tenant_id) as db:
        c = await inv.create_product(db, p, ProductIn(nombre="Cera", stock_inicial=5))

    async def salida() -> str:
        try:
            async with scoped_session(tenant_id=p.tenant_id) as db:
                await inv.add_movement(db, p, c.id, MovementIn(tipo="salida", cantidad=1))
            return "ok"
        except AppError as e:
            return e.code

    res = await asyncio.gather(*[salida() for _ in range(12)])
    assert res.count("ok") == 5 and res.count("STOCK_INSUFFICIENT") == 7
    async with scoped_session(tenant_id=p.tenant_id) as db:
        assert (await inv.get_product(db, p, c.id)).stock_actual == 0
        h = await inv.list_movements(db, p, c.id, PP)
        assert h.total == 6 and sum(m.delta for m in h.items) == 0                                                      # el libro suma exactamente el stock


# ------------------------------------------------------------------ aislamiento y libro intocable
async def test_other_business_cannot_see_or_move_my_products_and_ledger_is_append_only():
    a, b = await _setup("Profesional"), await _setup("Profesional")
    async with scoped_session(tenant_id=a.tenant_id) as db:
        c = await inv.create_product(db, a, ProductIn(nombre="Secreto", stock_inicial=3))
    async with scoped_session(tenant_id=b.tenant_id) as db:
        assert (await inv.list_products(db, b, PP, None, None, None)).total == 0
        for coro in (inv.get_product(db, b, c.id), inv.add_movement(db, b, c.id, MovementIn(tipo="entrada", cantidad=1)), inv.list_movements(db, b, c.id, PP)):
            with pytest.raises(AppError) as e:
                await coro
            assert e.value.code == "NOT_FOUND"
    rows = await _sql("SELECT count(*) AS n FROM stock_movements WHERE product_id = :p", p=c.id)                          # con RLS de plataforma sí se ven
    assert rows[0].n == 1
    for sql in ("UPDATE stock_movements SET delta = 99", "DELETE FROM stock_movements", "TRUNCATE TABLE stock_movements"):
        with pytest.raises(DBAPIError):                                                                                # permiso denegado: el historial no se reescribe
            async with scoped_session(platform=True) as db:
                await db.execute(text(sql))


async def test_stock_cannot_go_negative_even_if_the_application_has_a_bug():
    p = await _setup("Profesional")
    async with scoped_session(tenant_id=p.tenant_id) as db:
        c = await inv.create_product(db, p, ProductIn(nombre="Cera", stock_inicial=1))
    with pytest.raises(DBAPIError):                                                                                    # la restricción CHECK de la base es la última defensa
        async with scoped_session(platform=True) as db:
            await db.execute(text("UPDATE products SET stock_actual = -1 WHERE id = :i"), {"i": c.id})


# ------------------------------------------------------------------ por HTTP: acceso según el plan y permisos
async def test_http_module_gating_and_flow():
    _, slug_b, email_b = await _user("Básico")                  # el plan Básico no incluye inventario
    _, slug_p, email_p = await _user("Profesional")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        async def entrar(slug: str, email: str) -> dict[str, str]:
            r = await c.post("/api/v1/auth/login", json={"tenant_slug": slug, "email": email, "password": PASSWORD})
            assert r.status_code == 200, r.text
            return {"Authorization": f"Bearer {r.json()['access_token']}"}

        basico = await entrar(slug_b, email_b)
        r = await c.get("/api/v1/inventory/products", headers=basico)
        assert r.status_code == 403 and r.json()["code"] == "MODULE_NOT_ENABLED"
        assert (await c.post("/api/v1/inventory/products", headers=basico, json={"nombre": "Cera"})).status_code == 403
        assert (await c.get("/api/v1/inventory/products")).status_code == 401                                         # sin sesión

        pro = await entrar(slug_p, email_p)
        r = await c.post("/api/v1/inventory/products", headers=pro, json={"nombre": "Cera para barba", "sku": "CERA", "costo": "18000", "precio_venta": "35000", "stock_inicial": 5, "stock_minimo": 2})
        assert r.status_code == 201, r.text
        prod = r.json()
        assert prod["stock_actual"] == 5 and prod["bajo_stock"] is False and prod["costo"] == "18000.00"
        r = await c.post(f"/api/v1/inventory/products/{prod['id']}/movements", headers=pro, json={"tipo": "salida", "cantidad": 9})
        assert r.status_code == 409 and r.json()["code"] == "STOCK_INSUFFICIENT" and r.json()["details"]["disponible"] == 5
        r = await c.post(f"/api/v1/inventory/products/{prod['id']}/movements", headers=pro, json={"tipo": "salida", "cantidad": 4, "motivo": "Venta"})
        assert r.status_code == 201 and r.json()["stock_resultante"] == 1
        r = await c.get("/api/v1/inventory/products?bajo_stock=true", headers=pro)
        assert r.status_code == 200 and r.json()["total"] == 1 and r.json()["items"][0]["bajo_stock"] is True
        r = await c.get("/api/v1/inventory/summary", headers=pro)
        assert r.json()["productos"] == 1 and r.json()["bajo_stock"] == 1
        r = await c.post(f"/api/v1/inventory/products/{prod['id']}/movements", headers=pro, json={"tipo": "ajuste", "cantidad": 3})
        assert r.status_code == 422 and r.json()["code"] == "VALIDATION_ERROR"                                         # el ajuste exige motivo
        assert (await c.get(f"/api/v1/inventory/products/{prod['id']}", headers=basico)).status_code == 403
