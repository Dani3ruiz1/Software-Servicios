"""Fase 8 sin BD: reglas de facturación (NIT/DV, resolución, impuestos), estados y reintentos, proveedor simulado y su contrato."""
import re
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.errors import AppError
from app.core.rbac import PERMISSIONS as P
from app.main import app
from app.modules.integrations.einvoice import rules
from app.modules.integrations.einvoice.provider import (
    PROVIDERS,
    ProviderConfigError,
    ProviderResult,
    ProviderTransient,
    SandboxProvider,
    get_provider,
)
from app.modules.integrations.einvoice.schemas import AdquirenteIn, CreditNoteIn, SettingsIn
from app.modules.integrations.einvoice.service import _apply

D = Decimal
HOY = date(2026, 10, 20)


# ---------------------------------------------------------------- NIT
@pytest.mark.parametrize("nit,dv", [("800197268", 4), ("890903938", 8), ("899999068", 1)])      # DIAN, Bancolombia, Ecopetrol
def test_nit_check_digit_known_companies(nit, dv):
    assert rules.nit_dv(nit) == dv and rules.check_nit(nit, dv) == dv and rules.check_nit(nit, None) == dv


def test_nit_wrong_digit_or_format_is_rejected_with_the_expected_value():
    with pytest.raises(AppError) as e:
        rules.check_nit("800197268", 9)
    assert e.value.code == "VALIDATION_ERROR" and e.value.details["esperado"] == 4
    for bad in ("800.197.268", "800197268-4", "abc", "12", ""):
        with pytest.raises(AppError):
            rules.nit_dv(bad)


# ---------------------------------------------------------------- resolución y numeración
RES = rules.Resolution("FE", 1000, 1002, date(2027, 6, 30), "18760000001")


def test_numbering_starts_at_range_start_continues_and_never_goes_below():
    assert rules.next_number(None, 1000) == 1000 and rules.next_number(1000, 1000) == 1001 and rules.next_number(5, 1000) == 1000
    assert rules.full_number("FE", 1001) == "FE1001" and rules.full_number("", 7) == "7" and rules.full_number(None, 7) == "7"


def test_resolution_exhausted_expired_and_remaining():
    rules.check_resolution(RES, 1002, HOY)                                              # el último número del rango es válido
    with pytest.raises(AppError) as e:
        rules.check_resolution(RES, 1003, HOY)
    assert e.value.code == "RESOLUTION_EXHAUSTED" and e.value.status == 409
    with pytest.raises(AppError) as e2:
        rules.check_resolution(RES, 1000, date(2027, 7, 1))
    assert e2.value.code == "RESOLUTION_EXPIRED"
    rules.check_resolution(RES, 1000, date(2027, 6, 30))                                # el último día de vigencia aún sirve
    assert [rules.remaining(last, RES) for last in (None, 1000, 1002)] == [3, 2, 0]


# ---------------------------------------------------------------- impuestos
@pytest.mark.parametrize("base,iva,esperado", [("33333", "6333.27", "19"), ("100000", "19000", "19"), ("100", "5", "5"), ("100", "8", "8"),
                                               ("100", "0", "0"), ("0", "0", "0"), ("100", "12.5", "12.50")])
def test_effective_tax_rate_snaps_to_known_rates(base, iva, esperado):
    assert rules.iva_pct(D(base), D(iva)) == D(esperado)


def _items():
    return [{"id": uuid4(), "descripcion": "Corte", "precio_cobrado": D("30000"), "iva": D("0")},
            {"id": uuid4(), "descripcion": "Keratina", "precio_cobrado": D("100000"), "iva": D("19000.00")},
            {"id": uuid4(), "descripcion": "Gel", "precio_cobrado": D("33333"), "iva": D("6333.27")}]


def test_lines_taxes_and_totals_are_consistent():
    lines = rules.build_lines(_items())
    tot = rules.totals(lines)
    assert tot == {"subtotal": "163333", "iva": "25333.27", "total": "188666.27"}
    assert sum(D(x["total"]) for x in lines) == D(tot["total"])
    imp = rules.group_taxes(lines)
    assert [(i["porcentaje"], i["base"], i["valor"]) for i in imp] == [("0", "30000", "0"), ("19", "133333", "25333.27")]
    assert sum(D(i["valor"]) for i in imp) == D(tot["iva"]) and sum(D(i["base"]) for i in imp) == D(tot["subtotal"])
    assert [x["n"] for x in lines] == [1, 2, 3]


def test_payment_methods_map_to_dian_codes_and_group():
    out = rules.medios_pago([{"metodo": "efectivo", "monto": D("20000")}, {"metodo": "datafono", "monto": D("5000")},
                             {"metodo": "otro", "monto": D("1000")}, {"metodo": "transferencia", "monto": D("12000")}])
    assert {m["codigo"]: m["monto"] for m in out} == {"10": "20000", "1": "6000", "47": "12000"}


# ---------------------------------------------------------------- adquirente
def test_buyer_priority_override_then_customer_then_final_consumer():
    ov = {"tipo_documento": "31", "documento": "900123456", "nombre": "Empresa SAS", "email": "f@e.co"}
    assert rules.adquirente_for({"documento": "123", "nombre": "X"}, ov)["documento"] == "900123456"
    c = rules.adquirente_for({"documento": " 1020304050 ", "nombre": "Carlos", "email": "c@x.co"}, None)
    assert (c["tipo_documento"], c["documento"], c["nombre"]) == ("13", "1020304050", "Carlos")
    for cust in (None, {"documento": "  ", "nombre": "Ana"}, {"documento": None, "nombre": "Ana"}):
        assert rules.adquirente_for(cust, None)["documento"] == "222222222222"                    # consumidor final
    with pytest.raises(AppError):
        rules.adquirente_for(None, {**ov, "tipo_documento": "99"})


# ---------------------------------------------------------------- CUFE de referencia
def _cufe(**kw):
    base = dict(numero="FE1001", fecha=datetime(2026, 10, 20, 10, 30, 5), valor_factura=D("100000"), iva=D("19000"), total=D("119000"),
                nit_emisor="800197268", doc_adquirente="222222222222", clave_tecnica="clave", ambiente="habilitacion")
    return rules.cufe_reference(**{**base, **kw})


def test_reference_cufe_is_sha384_deterministic_and_sensitive_to_every_field():
    c = _cufe()
    assert re.fullmatch(r"[0-9a-f]{96}", c) and c == _cufe()
    for change in ({"numero": "FE1002"}, {"total": D("119001")}, {"doc_adquirente": "1"}, {"clave_tecnica": "otra"}, {"ambiente": "produccion"},
                   {"fecha": datetime(2026, 10, 20, 10, 30, 6)}, {"nit_emisor": "800197269"}):
        assert _cufe(**change) != c, change


# ---------------------------------------------------------------- reintentos y notas crédito
def test_backoff_and_retry_rules():
    assert [rules.backoff_minutes(n) for n in range(1, 9)] == [1, 2, 5, 15, 30, 60, 120, 240] and rules.backoff_minutes(99) == 240 and rules.backoff_minutes(0) == 1
    assert rules.MAX_ATTEMPTS == 8 and rules.can_retry("rechazada") and rules.can_retry("error")
    assert not any(rules.can_retry(e) for e in ("emitida", "enviada", "aceptada"))


def test_credit_note_cannot_exceed_the_invoice():
    rules.credit_note_room(D("100"), D("0"), D("100"))
    rules.credit_note_room(D("100"), D("40"), D("60"))
    for ya, pide in ((D("40"), D("60.01")), (D("100"), D("1"))):
        with pytest.raises(AppError) as e:
            rules.credit_note_room(D("100"), ya, pide)
        assert e.value.code == "INVOICE_NOT_ALLOWED"
    with pytest.raises(AppError):
        rules.credit_note_room(D("100"), D("0"), D("0"))
    assert set(rules.MOTIVOS_NC) == {1, 2, 3, 4, 5, 6}


# ---------------------------------------------------------------- máquina de estados (resultado del proveedor -> factura)
def _inv(intentos=0):
    return SimpleNamespace(estado="emitida", intentos=intentos, cufe=None, xml_url=None, pdf_url=None, referencia_proveedor=None, respuesta_proveedor=None,
                           proximo_intento=datetime.now(UTC), ultimo_error="viejo")


def test_accepted_stores_cufe_and_clears_retry_state():
    i = _inv()
    assert _apply(i, ProviderResult("aceptada", cufe="C" * 96, referencia="r1", pdf_url="https://p/x.pdf", xml_url="https://p/x.xml")) == "aceptada"
    assert (i.estado, i.cufe, i.referencia_proveedor, i.proximo_intento, i.ultimo_error) == ("aceptada", "C" * 96, "r1", None, None)


def test_rejected_keeps_the_errors_and_stops_retrying():
    i = _inv()
    assert _apply(i, ProviderResult("rechazada", errores=["FAK24: NIT inválido", "FAJ43: nombre"])) == "rechazada"
    assert i.estado == "rechazada" and i.proximo_intento is None and "FAK24" in i.ultimo_error and "FAJ43" in i.ultimo_error


def test_pending_polls_with_growing_waits_then_gives_up():
    i = _inv()
    waits = []
    for _ in range(rules.MAX_ATTEMPTS - 1):
        assert _apply(i, ProviderResult("pendiente", referencia="r")) == "enviada"
        waits.append(round((i.proximo_intento - datetime.now(UTC)).total_seconds() / 60))
    assert i.estado == "enviada" and waits == sorted(waits) and waits[0] in (1, 2)
    assert _apply(i, ProviderResult("pendiente")) == "error" and i.estado == "error" and i.proximo_intento is None


# ---------------------------------------------------------------- proveedor simulado y contrato de proveedores
def _doc(documento="222222222222", ref=None):
    return {"referencia": ref or str(uuid4()), "tipo": "factura", "ambiente": "habilitacion", "numero": 1001, "numero_completo": "FE1001",
            "fecha_emision": datetime(2026, 10, 20, 10, 30, tzinfo=UTC).isoformat(), "emisor": {"nit": "800197268", "dv": 4},
            "adquirente": {"tipo_documento": "13", "documento": documento, "nombre": "Carlos"},
            "totales": {"subtotal": "100000", "iva": "19000", "total": "119000"}}


async def assert_provider_contract(provider, ok_doc, rejected_doc):
    """Prueba que TODO adaptador de proveedor real debe pasar (se reutiliza cuando se conecte uno)."""
    await provider.validate()
    r1, r2 = await provider.emit(ok_doc), await provider.emit(ok_doc)
    assert r1.estado == "aceptada" and r1.cufe and re.fullmatch(r"[0-9a-f]{64,128}", r1.cufe) and r1.referencia
    assert (r1.cufe, r1.referencia) == (r2.cufe, r2.referencia)                  # IDEMPOTENTE por `referencia`
    st = await provider.status(r1.referencia, ok_doc)
    assert st.estado in ("aceptada", "pendiente") and (st.estado != "aceptada" or st.cufe == r1.cufe)
    rej = await provider.emit(rejected_doc)
    assert rej.estado == "rechazada" and rej.errores                              # un rechazo es un RESULTADO, no una excepción


async def test_sandbox_provider_passes_the_provider_contract():
    await assert_provider_contract(SandboxProvider(None, "habilitacion"), _doc(), _doc("9999999999"))


async def test_sandbox_simulates_outages_and_labels_itself():
    p = SandboxProvider({"clave_tecnica": "k"}, "habilitacion")
    with pytest.raises(ProviderTransient):
        await p.emit(_doc("8888888888"))
    r = await p.emit(_doc())
    assert r.raw["aviso"] == "SIN VALIDEZ FISCAL" and r.pdf_url.endswith("/pdf") and r.raw["simulado"] is True


def test_sandbox_refuses_production_and_unknown_providers_are_config_errors():
    with pytest.raises(ProviderConfigError):
        SandboxProvider(None, "produccion")
    with pytest.raises(ProviderConfigError):
        get_provider("sandbox", None, "produccion")
    with pytest.raises(ProviderConfigError):
        get_provider("inexistente", None, "habilitacion")
    assert "sandbox" in PROVIDERS and get_provider("sandbox", None, "habilitacion").nombre == "sandbox"


# ---------------------------------------------------------------- esquemas y contrato
def _settings(**kw):
    base = dict(nit="800197268", razon_social="Barbería Don Pepe SAS", resolucion="18760000001", vigencia_hasta=date(2027, 6, 30),
                prefijo="FE", rango_desde=1000, rango_hasta=5000)
    return SettingsIn(**{**base, **kw})


def test_settings_validation():
    assert _settings().prefijo_nota_credito == "NC" and _settings().ambiente == "habilitacion"
    for bad in ({"nit": "800.197.268-4"}, {"rango_hasta": 999}, {"prefijo": "fe"}, {"prefijo": "TOOLONG"}, {"prefijo": "NC"},
                {"razon_social": "ab"}, {"rango_desde": 0}, {"ambiente": "pruebas"}, {"dv": 10}):
        with pytest.raises(ValidationError):
            _settings(**bad)
    assert _settings(prefijo="").prefijo == ""                                      # sin prefijo es válido


def test_buyer_and_credit_note_schemas():
    assert AdquirenteIn(documento="1020304050", nombre="Carlos").tipo_documento == "13"
    with pytest.raises(ValidationError):
        AdquirenteIn(documento="10 20", nombre="Carlos")
    with pytest.raises(ValidationError):
        AdquirenteIn(documento="1020304050", nombre="Carlos", email="no-es-correo")
    assert CreditNoteIn(motivo="El cliente devolvió el producto").codigo_motivo == 2
    for bad in ({"motivo": "no"}, {"codigo_motivo": 7}, {"codigo_motivo": 0}):
        with pytest.raises(ValidationError):
            CreditNoteIn(**{"motivo": "motivo válido", **bad})


def test_permissions_and_routes():
    assert "invoices:manage" in P["admin"] and "invoices:manage" in P["recepcionista"] and "invoices:manage" not in P["profesional"]
    paths = app.openapi()["paths"]
    for route in ("/api/v1/einvoice/settings", "/api/v1/einvoice/provider", "/api/v1/einvoice/provider/validate", "/api/v1/sales/{sale_id}/invoice",
                  "/api/v1/invoices", "/api/v1/invoices/{invoice_id}", "/api/v1/invoices/{invoice_id}/retry",
                  "/api/v1/invoices/{invoice_id}/credit-note", "/api/v1/invoices/{invoice_id}/pdf"):
        assert route in paths, route
    schema = app.openapi()["components"]["schemas"]
    assert "credenciales" not in schema["ProviderStatus"]["properties"]              # las credenciales jamás se devuelven

