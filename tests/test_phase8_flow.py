"""Fase 8 contra Postgres real. El proveedor es el SIMULADO (sin red, sin validez fiscal): se prueba todo lo nuestro de punta a punta."""
import asyncio
import json
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import text
from test_phase4_flow import _setup
from test_phase5_flow import EF, _open_cash, _sell, _svc

from app.core.db import scoped_session
from app.core.errors import AppError
from app.core.security import create_access_token
from app.core.tenancy import Principal
from app.main import app
from app.modules.integrations.einvoice import rules
from app.modules.integrations.einvoice import service as es
from app.modules.integrations.einvoice.schemas import (
    AdquirenteIn,
    CreditNoteIn,
    ProviderIn,
    SettingsIn,
)
from app.modules.reports import service as reports
from app.modules.sales.schemas import SaleIn, SaleItemIn

D = Decimal
TZ = ZoneInfo("America/Bogota")


@pytest.fixture(autouse=True)
async def _needs_db():
    try:
        async with scoped_session(platform=True) as db:
            await db.execute(text("SELECT 1 FROM plans LIMIT 1"))
            # Otros tests pudieron dejar facturas pendientes: el procesamiento de Celery es GLOBAL, se aíslan antes de cada prueba.
            await db.execute(text("UPDATE invoices SET proximo_intento = NULL WHERE tipo <> 'interna' AND estado IN ('emitida', 'enviada')"))
    except Exception:                                                              # noqa: BLE001
        pytest.skip("Postgres no disponible")


def _settings(**kw):
    base = dict(nit="800197268", razon_social="Barbería Don Pepe SAS", resolucion="18760000001", vigencia_hasta=date.today() + timedelta(days=365),
                prefijo="FE", rango_desde=1000, rango_hasta=1002)
    return SettingsIn(**{**base, **kw})


async def _fe(plan="Premium", **kw) -> Principal:
    kw = {("rango_" + k if k in ("desde", "hasta") else k): v for k, v in kw.items()}
    p = await _setup(plan)
    async with scoped_session(tenant_id=p.tenant_id) as db:
        await es.put_provider(db, p, ProviderIn(proveedor="sandbox"))
        await es.put_settings(db, p, _settings(**kw))
    await _open_cash(p)
    return p


async def _paid(p: Principal, *, iva_item=True):
    a = await _svc(p, f"Corte {uuid4().hex[:4]}", 30000)
    items = [SaleItemIn(service_id=a)]
    total = 30000
    if iva_item:
        b = await _svc(p, f"Keratina {uuid4().hex[:4]}", 100000, iva=True)
        items.append(SaleItemIn(service_id=b))
        total += 119000
    return await _sell(p, SaleIn(items=items, pagos=[EF(total)]))


async def _emit(p, sale_id, adq=None):
    async with scoped_session(tenant_id=p.tenant_id) as db:
        return await es.emit_invoice(db, p, sale_id, adq)


async def _send(p, inv_id):
    await es.run_dispatch(p.tenant_id, inv_id)
    async with scoped_session(tenant_id=p.tenant_id) as db:
        return await es.get_invoice(db, p, inv_id)


async def _sql(sql: str, **prm):
    async with scoped_session(platform=True) as db:
        return (await db.execute(text(sql), prm)).all()


# ================================================================== configuración
async def test_settings_validate_nit_and_never_allow_the_simulator_in_production():
    p = await _setup("Premium")
    async with scoped_session(tenant_id=p.tenant_id) as db:
        assert await es.settings_out(db, p.tenant_id) is None
        with pytest.raises(AppError) as e:
            await es.put_settings(db, p, _settings(dv=9))                                           # DV que no corresponde al NIT
        assert e.value.details["esperado"] == 4
    async with scoped_session(tenant_id=p.tenant_id) as db:
        prov = await es.put_provider(db, p, ProviderIn(proveedor="sandbox", credenciales={"clave_tecnica": "secreta-123"}))
        assert prov.configurado and prov.tiene_credenciales and "sandbox" in prov.proveedores_disponibles
        assert "secreta-123" not in json.dumps(prov.model_dump(mode="json"))
        with pytest.raises(AppError) as e:
            await es.put_provider(db, p, ProviderIn(proveedor="inventado"))
        assert e.value.code == "VALIDATION_ERROR"
        with pytest.raises(AppError) as e:                                                         # producción + simulador
            await es.put_settings(db, p, _settings(ambiente="produccion"))
        assert e.value.code == "CONFLICT"
        out = await es.put_settings(db, p, _settings())
        assert out.dv == 4 and out.restantes == 3 and out.ultimo_numero is None and out.resolucion_vigente
        v = await es.validate_provider(db, p)
        assert v.valida and (await es.provider_status(db, p)).estado == "activa"
    raw = (await _sql("SELECT credenciales_cifradas FROM integrations WHERE tenant_id = :t AND tipo = 'einvoice'", t=p.tenant_id))[0].credenciales_cifradas
    assert "secreta-123" not in raw                                                                # cifradas en la BD
    async with scoped_session(platform=True) as db:                                                # y al revés: pasar a producción y luego elegir simulador
        await db.execute(text("UPDATE invoice_settings SET ambiente = 'produccion' WHERE tenant_id = :t"), {"t": p.tenant_id})
    with pytest.raises(AppError) as e:
        async with scoped_session(tenant_id=p.tenant_id) as db:
            await es.put_provider(db, p, ProviderIn(proveedor="sandbox"))
    assert e.value.code == "CONFLICT"


# ================================================================== emisión
async def test_emit_snapshot_numbering_acceptance_and_pdf():
    p = await _fe()
    sale = await _paid(p)
    inv = await _emit(p, sale.id)
    assert inv.estado == "emitida" and inv.numero_completo == "FE1000" and inv.total == D("149000.00") and inv.subtotal == D("130000") and inv.iva == D("19000.00")
    d = await _send(p, inv.id)
    assert d.estado == "aceptada" and re.fullmatch(r"[0-9a-f]{96}", d.cufe) and d.pdf_url.endswith(f"/invoices/{inv.id}/pdf") and d.ultimo_error is None
    doc = d.datos
    assert doc["emisor"]["dv"] == 4 and doc["emisor"]["resolucion"] == "18760000001" and doc["adquirente"]["documento"] == "222222222222"        # consumidor final
    assert [(i["porcentaje"], i["valor"]) for i in doc["impuestos"]] == [("0", "0.00"), ("19", "19000.00")] and doc["medios_pago"] == [{"codigo": "10", "monto": "149000.00"}]
    assert "propina" in doc["nota"]
    async with scoped_session(tenant_id=p.tenant_id) as db:
        pdf = await es.sandbox_pdf(db, p, inv.id)
    assert pdf.startswith(b"%PDF") and len(pdf) > 1200
    ev = {r.accion: r.actor_tipo for r in await _sql("SELECT accion, actor_tipo FROM audit_logs WHERE tenant_id = :t AND entidad = 'invoice'", t=p.tenant_id)}
    assert ev["factura_emitida"] == "user" and ev["factura_aceptada"] == "system"


async def test_only_paid_sales_one_live_invoice_per_sale_and_customer_data_used():
    p = await _fe()
    tab = await _sell(p, SaleIn(items=[SaleItemIn(service_id=await _svc(p, "Corte", 30000))]))            # venta abierta
    with pytest.raises(AppError) as e:
        await _emit(p, tab.id)
    assert e.value.code == "INVOICE_NOT_ALLOWED"
    async with scoped_session(tenant_id=p.tenant_id) as db:
        from app.modules.customers import service as cs
        from app.modules.customers.schemas import CustomerIn
        cid = (await cs.create_customer(db, p, CustomerIn(nombre="Carlos Pérez", documento="1020304050", email="c@x.co", consentimiento_datos=True))).id
    sale = await _sell(p, SaleIn(customer_id=cid, items=[SaleItemIn(service_id=await _svc(p, "Corte 2", 30000))], pagos=[EF(30000)]))
    inv = await _emit(p, sale.id)
    assert (await _send(p, inv.id)).datos["adquirente"] == {"tipo_documento": "13", "documento": "1020304050", "nombre": "Carlos Pérez", "email": "c@x.co"}
    with pytest.raises(AppError) as e:
        await _emit(p, sale.id)
    assert e.value.code == "CONFLICT" and e.value.details["factura_id"] == str(inv.id)
    with pytest.raises(AppError) as e:                                                                    # venta inexistente / de otro negocio
        await _emit(p, uuid4())
    assert e.value.code == "NOT_FOUND"


async def test_numbers_stay_inside_the_resolution_range_without_gaps_and_exhaustion_burns_nothing():
    p = await _fe(desde=1000, hasta=1002)
    sales = [await _paid(p, iva_item=False) for _ in range(4)]
    nums = []
    for s in sales[:3]:
        nums.append((await _emit(p, s.id)).numero_completo)
    assert nums == ["FE1000", "FE1001", "FE1002"]
    for _ in range(2):                                                                                    # agotado: igual de claro cada vez y sin "quemar" números
        with pytest.raises(AppError) as e:
            await _emit(p, sales[3].id)
        assert e.value.code == "RESOLUTION_EXHAUSTED" and e.value.status == 409
    async with scoped_session(tenant_id=p.tenant_id) as db:
        out = await es.settings_out(db, p.tenant_id)
        assert out.restantes == 0 and out.ultimo_numero == 1002
        await es.put_settings(db, p, _settings(rango_desde=1000, rango_hasta=1010))                       # nueva resolución ampliada: sigue donde iba
    assert (await _emit(p, sales[3].id)).numero_completo == "FE1003"
    async with scoped_session(platform=True) as db:                                                       # resolución vencida
        await db.execute(text("UPDATE invoice_settings SET vigencia_hasta = current_date - 1 WHERE tenant_id = :t"), {"t": p.tenant_id})
    with pytest.raises(AppError) as e:
        await _emit(p, (await _paid(p, iva_item=False)).id)
    assert e.value.code == "RESOLUTION_EXPIRED"


async def test_parallel_emissions_never_duplicate_an_invoice_or_a_number():
    p = await _fe(desde=2000, hasta=2100)
    one = await _paid(p, iva_item=False)
    res = await asyncio.gather(*[_emit(p, one.id) for _ in range(5)], return_exceptions=True)
    assert sum(not isinstance(r, Exception) for r in res) == 1
    assert all(isinstance(r, AppError) and r.code == "CONFLICT" for r in res if isinstance(r, Exception))
    sales = [await _paid(p, iva_item=False) for _ in range(5)]
    outs = await asyncio.gather(*[_emit(p, s.id) for s in sales])
    assert sorted(o.numero_completo for o in outs) == [f"FE{n}" for n in range(2001, 2006)]               # contiguos: 2000 fue la venta de arriba


# ================================================================== rechazo, reintentos y corrección
async def test_rejection_keeps_the_number_and_a_corrected_retry_is_accepted():
    p = await _fe()
    inv = await _emit(p, (await _paid(p, iva_item=False)).id, AdquirenteIn(documento="9999999999", nombre="Cliente Dudoso"))
    d = await _send(p, inv.id)
    assert d.estado == "rechazada" and "FAK24" in d.ultimo_error and d.proximo_intento is None and d.cufe is None
    async with scoped_session(tenant_id=p.tenant_id) as db:
        fixed = await es.retry_invoice(db, p, inv.id, AdquirenteIn(documento="1020304050", nombre="Carlos Pérez"))
        assert fixed.estado == "emitida" and fixed.numero_completo == d.numero_completo           # MISMO número
    d2 = await _send(p, inv.id)
    assert d2.estado == "aceptada" and d2.datos["adquirente"]["documento"] == "1020304050" and d2.cufe
    with pytest.raises(AppError) as e:                                                              # una aceptada no se reenvía
        async with scoped_session(tenant_id=p.tenant_id) as db:
            await es.retry_invoice(db, p, inv.id, None)
    assert e.value.code == "INVOICE_NOT_ALLOWED"


async def test_transient_provider_failures_retry_with_the_same_number_then_stop_at_error():
    p = await _fe()
    inv = await _emit(p, (await _paid(p, iva_item=False)).id, AdquirenteIn(documento="8888888888", nombre="Caída Simulada"))
    d = await _send(p, inv.id)
    assert d.estado == "emitida" and d.intentos == 1 and d.proximo_intento > datetime.now(UTC) and "caída" in d.ultimo_error
    async with scoped_session(platform=True) as db:
        assert await es.process_pending(db) == {}                                                   # aún no toca: no se reintenta antes de tiempo
    seen = []
    for _ in range(rules.MAX_ATTEMPTS - 1):
        async with scoped_session(platform=True) as db:
            await db.execute(text("UPDATE invoices SET proximo_intento = now() WHERE id = :i"), {"i": inv.id})
            seen.append(await es.process_pending(db))
    assert seen[:-1] == [{"reintento": 1}] * (rules.MAX_ATTEMPTS - 2) and seen[-1] == {"error": 1}
    async with scoped_session(tenant_id=p.tenant_id) as db:
        e = await es.get_invoice(db, p, inv.id)
        assert e.estado == "error" and e.intentos == rules.MAX_ATTEMPTS and e.proximo_intento is None and e.numero_completo == d.numero_completo
        await es.retry_invoice(db, p, inv.id, AdquirenteIn(documento="1020304050", nombre="Carlos Pérez"))     # el administrador lo corrige
    assert (await _send(p, inv.id)).estado == "aceptada"


async def test_missing_provider_config_fails_fast_without_retries():
    p = await _fe()
    inv = await _emit(p, (await _paid(p, iva_item=False)).id)
    async with scoped_session(tenant_id=p.tenant_id) as db:
        await integ_delete(db, p)
    d = await _send(p, inv.id)
    assert d.estado == "error" and d.intentos == 0 and "Falta configurar" in d.ultimo_error and d.proximo_intento is None


async def integ_delete(db, p):
    from app.modules.integrations import service as integ
    await integ.delete_integration(db, p, "einvoice")


# ================================================================== notas crédito
async def _accepted(p, **kw):
    inv = await _emit(p, (await _paid(p, **kw)).id)
    return await _send(p, inv.id)


async def test_credit_notes_partial_then_remainder_never_exceed_the_invoice():
    p = await _fe(desde=3000, hasta=3100)
    fac = await _accepted(p)
    items = {ln["descripcion"].split()[0]: ln["item_id"] for ln in fac.datos["lineas"]}
    async with scoped_session(tenant_id=p.tenant_id) as db:
        nc1 = await es.create_credit_note(db, p, fac.id, CreditNoteIn(motivo="Devolvió la keratina", codigo_motivo=1, item_ids=[UUID(items["Keratina"])]))
    assert nc1.numero_completo == "NC1" and nc1.tipo == "nota_credito" and nc1.total == D("119000.00") and nc1.factura_origen_id == fac.id
    d1 = await _send(p, nc1.id)
    assert d1.estado == "aceptada" and d1.datos["referencia_factura"]["cufe"] == fac.cufe and d1.datos["motivo_dian"].startswith("Devolución parcial")
    with pytest.raises(AppError) as e:                                                                  # el mismo ítem otra vez
        async with scoped_session(tenant_id=p.tenant_id) as db:
            await es.create_credit_note(db, p, fac.id, CreditNoteIn(motivo="otra vez", item_ids=[UUID(items["Keratina"])]))
    assert e.value.code == "INVOICE_NOT_ALLOWED"
    async with scoped_session(tenant_id=p.tenant_id) as db:
        nc2 = await es.create_credit_note(db, p, fac.id, CreditNoteIn(motivo="Anulación del resto"))      # sin item_ids: lo que falte
    assert nc2.numero_completo == "NC2" and nc2.total == D("30000")
    with pytest.raises(AppError) as e:                                                                  # ya no queda nada por acreditar
        async with scoped_session(tenant_id=p.tenant_id) as db:
            await es.create_credit_note(db, p, fac.id, CreditNoteIn(motivo="no queda nada"))
    assert e.value.code == "VALIDATION_ERROR"
    with pytest.raises(AppError) as e:
        async with scoped_session(tenant_id=p.tenant_id) as db:
            await es.create_credit_note(db, p, fac.id, CreditNoteIn(motivo="ítem ajeno", item_ids=[uuid4()]))
    assert e.value.code == "NOT_FOUND"


async def test_only_accepted_invoices_can_be_credited_and_other_businesses_cannot_see_them():
    p = await _fe()
    rej = await _emit(p, (await _paid(p, iva_item=False)).id, AdquirenteIn(documento="9999999999", nombre="Dudoso"))
    await _send(p, rej.id)
    with pytest.raises(AppError) as e:
        async with scoped_session(tenant_id=p.tenant_id) as db:
            await es.create_credit_note(db, p, rej.id, CreditNoteIn(motivo="no aplica"))
    assert e.value.code == "INVOICE_NOT_ALLOWED"
    ok = await _accepted(p, iva_item=False)
    other = await _fe()
    for fn in (lambda db: es.create_credit_note(db, other, ok.id, CreditNoteIn(motivo="intruso")), lambda db: es.get_invoice(db, other, ok.id),
               lambda db: es.sandbox_pdf(db, other, ok.id)):
        with pytest.raises(AppError) as e:
            async with scoped_session(tenant_id=other.tenant_id) as db:
                await fn(db)
        assert e.value.code == "NOT_FOUND"
    async with scoped_session(tenant_id=other.tenant_id) as db:
        assert (await db.execute(text("SELECT count(*) FROM invoices WHERE tipo <> 'interna'"))).scalar() == 0


async def test_sales_report_subtracts_credit_notes_in_the_period_they_are_issued():
    p = await _fe(desde=4000, hasta=4100)
    fac = await _accepted(p)
    async with scoped_session(tenant_id=p.tenant_id) as db:
        nc = await es.create_credit_note(db, p, fac.id, CreditNoteIn(motivo="Anulación total"))
    hoy = datetime.now(TZ).date()

    async def ventas():
        async with scoped_session(tenant_id=p.tenant_id) as db:
            return await reports.run(db, p, "ventas", hoy - timedelta(days=1), hoy + timedelta(days=1), "dia", None)
    antes = await ventas()
    assert antes.totales["notas_credito"] == 0 and antes.totales["neto"] == antes.totales["total"] == D("149000.00")        # emitida pero no aceptada: no resta
    await _send(p, nc.id)
    despues = await ventas()
    assert despues.totales["total"] == D("149000.00") and despues.totales["notas_credito"] == D("149000.00") and despues.totales["neto"] == 0
    assert {"notas_credito", "neto"} <= {c.key for c in despues.columnas} and despues.filas[0]["neto"] == 0


# ================================================================== HTTP: módulo del plan y flujo completo
def _hdr(p: Principal) -> dict[str, str]:
    tok, _ = create_access_token(sub=p.id, kind="user", tenant_id=p.tenant_id, rol=p.rol)
    return {"Authorization": f"Bearer {tok}"}


async def test_http_flow_and_plan_gating():
    basic = await _setup("Básico")                                           # sin módulo einvoice: sigue con comprobante interno
    p = await _fe()
    sale = await _paid(p, iva_item=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/einvoice/settings", headers=_hdr(basic))
        assert r.status_code == 403 and r.json()["code"] == "MODULE_NOT_ENABLED"
        assert (await c.post(f"/api/v1/sales/{sale.id}/invoice", json={}, headers=_hdr(basic))).json()["code"] == "MODULE_NOT_ENABLED"
        rec = Principal(uuid4(), "user", p.tenant_id, "profesional")
        assert (await c.get("/api/v1/invoices", headers=_hdr(rec))).status_code == 403                  # el profesional no factura
        r = await c.post(f"/api/v1/sales/{sale.id}/invoice", json={}, headers=_hdr(p))
        assert r.status_code == 201 and r.json()["estado"] == "emitida" and r.json()["numero_completo"] == "FE1000"
        got = (await c.get(f"/api/v1/invoices/{r.json()['id']}", headers=_hdr(p))).json()               # el envío en segundo plano ya corrió
        assert got["estado"] == "aceptada" and got["cufe"] and got["datos"]["adquirente"]["nombre"] == "Consumidor final"
        pdf = await c.get(got["pdf_url"], headers=_hdr(p))
        assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf" and pdf.content.startswith(b"%PDF")
        lst = (await c.get("/api/v1/invoices?estado=aceptada&tipo=electronica", headers=_hdr(p))).json()
        assert lst["total"] == 1 and lst["items"][0]["id"] == got["id"]
        cn = await c.post(f"/api/v1/invoices/{got['id']}/credit-note", json={"motivo": "Devolución total", "codigo_motivo": 2}, headers=_hdr(p))
        assert cn.status_code == 201 and cn.json()["numero_completo"] == "NC1"
        assert (await c.get(f"/api/v1/invoices/{cn.json()['id']}", headers=_hdr(p))).json()["estado"] == "aceptada"
        assert (await c.post(f"/api/v1/invoices/{got['id']}/retry", json={}, headers=_hdr(p))).json()["code"] == "INVOICE_NOT_ALLOWED"
        s = (await c.get("/api/v1/einvoice/settings", headers=_hdr(p))).json()
        assert s["dv"] == 4 and s["restantes"] == 2 and "credenciales" not in json.dumps(s)
    internal = await _sql("SELECT count(*) AS n FROM invoices WHERE tenant_id = :t AND tipo = 'interna'", t=p.tenant_id)
    assert internal[0].n >= 1                                                                            # el comprobante interno de Fase 5 sigue existiendo

