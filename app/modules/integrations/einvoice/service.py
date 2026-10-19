"""Factura electrónica (plan Premium, opcional por venta). Sin este módulo el negocio sigue con su comprobante interno.

Flujo: POST emite -> queda `emitida` con su número ya asignado (consecutivo sin huecos dentro del rango de la resolución)
-> se envía al proveedor en segundo plano -> `aceptada` | `rechazada` | `enviada` (esperando validación) | `error` (hay que
revisar). Los fallos temporales se reintentan con espera creciente usando el MISMO número: un documento ya numerado
nunca se descarta ni se duplica (el proveedor es idempotente por `referencia`).
"""
import json
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from io import BytesIO
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import scoped_session
from app.core.errors import AppError
from app.core.pagination import Page, PageParams
from app.core.repository import paginate
from app.core.security import encrypt_secret
from app.core.tenancy import Principal
from app.modules.audit import service as audit
from app.modules.customers.models import Customer
from app.modules.integrations import service as integ
from app.modules.integrations.einvoice import rules
from app.modules.integrations.einvoice.models import InvoiceSettings
from app.modules.integrations.einvoice.provider import (
    PROVIDERS,
    EInvoiceProvider,
    ProviderConfigError,
    ProviderResult,
    ProviderTransient,
    get_provider,
)
from app.modules.integrations.einvoice.schemas import (
    AdquirenteIn,
    CreditNoteIn,
    InvoiceDetail,
    InvoiceOut,
    ProviderIn,
    ProviderStatus,
    SettingsIn,
    SettingsOut,
    ValidationOut,
)
from app.modules.sales.models import Invoice, Payment, Sale, SaleItem

log = logging.getLogger("app.einvoice")
TIPO = "einvoice"


def _aud(db: AsyncSession, p: Principal | None, tid: UUID, accion: str, inv_id: UUID | None, despues: dict[str, Any] | None = None) -> None:
    audit.record(db, actor_tipo="user" if p else "system", actor_id=p.id if p else None, tenant_id=tid, accion=accion,
                 entidad="invoice", entidad_id=inv_id, despues=despues)


async def _tz(db: AsyncSession, tid: UUID) -> ZoneInfo:
    return ZoneInfo((await db.execute(text("SELECT zona_horaria FROM tenants WHERE id = :t"), {"t": tid})).scalar_one())


# ================================================================== configuración
async def _settings(db: AsyncSession, tid: UUID) -> InvoiceSettings | None:
    return (await db.execute(select(InvoiceSettings).where(InvoiceSettings.tenant_id == tid))).scalar_one_or_none()


def _res(s: InvoiceSettings) -> rules.Resolution:
    return rules.Resolution(s.prefijo or "", s.rango_desde or 1, s.rango_hasta or 0, s.vigencia_hasta, s.resolucion)


async def _last_number(db: AsyncSession, tid: UUID, serie: str) -> int | None:
    return (await db.execute(text("SELECT ultimo_numero FROM document_sequences WHERE tenant_id = :t AND serie = :s"), {"t": tid, "s": serie})).scalar()


async def settings_out(db: AsyncSession, tid: UUID) -> SettingsOut | None:
    s = await _settings(db, tid)
    if s is None:
        return None
    res, hoy = _res(s), datetime.now(await _tz(db, tid)).date()
    last = await _last_number(db, tid, f"FE:{s.prefijo or ''}")
    return SettingsOut(nit=s.nit, dv=s.dv if s.dv is not None else rules.nit_dv(s.nit), razon_social=s.razon_social, resolucion=s.resolucion or "",
                       vigencia_hasta=s.vigencia_hasta or hoy, prefijo=s.prefijo or "", prefijo_nota_credito=s.prefijo_nota_credito,
                       rango_desde=res.desde, rango_hasta=res.hasta, ambiente=s.ambiente,
                       ultimo_numero=last, restantes=rules.remaining(last, res), resolucion_vigente=not (res.vigencia_hasta and hoy > res.vigencia_hasta))


async def put_settings(db: AsyncSession, p: Principal, d: SettingsIn) -> SettingsOut:
    dv = rules.check_nit(d.nit, d.dv)
    integ_row = await integ.get_integration(db, p.tid, TIPO)
    if d.ambiente == "produccion" and integ_row and (integ_row.config or {}).get("proveedor") == "sandbox":
        raise AppError("CONFLICT", "No se puede pasar a producción con el proveedor simulado: configura primero un proveedor real")
    s = await _settings(db, p.tid)
    if s is None:
        s = InvoiceSettings(tenant_id=p.tid)
        db.add(s)
    for k, v in d.model_dump(exclude={"dv"}).items():
        setattr(s, k, v)
    s.dv = dv
    await db.flush()
    _aud(db, p, p.tid, "facturacion_configurada", None, {"nit": d.nit, "prefijo": d.prefijo, "rango": [d.rango_desde, d.rango_hasta], "ambiente": d.ambiente})
    out = await settings_out(db, p.tid)
    assert out is not None
    return out


async def provider_status(db: AsyncSession, p: Principal) -> ProviderStatus:
    i = await integ.get_integration(db, p.tid, TIPO)
    base = ProviderStatus(configurado=False, proveedores_disponibles=sorted(PROVIDERS))
    if i is None:
        return base
    return base.model_copy(update={"configurado": True, "proveedor": (i.config or {}).get("proveedor"), "estado": i.estado,
                                   "ultima_verificacion": i.ultima_verificacion, "tiene_credenciales": bool(i.credenciales_cifradas)})


async def put_provider(db: AsyncSession, p: Principal, d: ProviderIn) -> ProviderStatus:
    if d.proveedor not in PROVIDERS:
        raise AppError("VALIDATION_ERROR", "Proveedor no disponible", {"disponibles": sorted(PROVIDERS)})
    s = await _settings(db, p.tid)
    if d.proveedor == "sandbox" and s and s.ambiente == "produccion":
        raise AppError("CONFLICT", "El proveedor simulado no puede usarse en producción")
    i = await integ._upsert(db, p.tid, TIPO)
    i.config = {"proveedor": d.proveedor}
    i.credenciales_cifradas = encrypt_secret(json.dumps(d.credenciales)) if d.credenciales else None
    i.estado = "pendiente"
    await db.flush()
    _aud(db, p, p.tid, "proveedor_facturacion_configurado", None, {"proveedor": d.proveedor})
    return await provider_status(db, p)


async def _provider(db: AsyncSession, tid: UUID) -> tuple[EInvoiceProvider, str]:
    s, i = await _settings(db, tid), await integ.get_integration(db, tid, TIPO)
    if s is None or i is None:
        raise ProviderConfigError("Falta configurar la facturación electrónica (datos de la resolución y proveedor)")
    return get_provider((i.config or {}).get("proveedor", ""), integ.creds_of(i), s.ambiente), s.ambiente


async def validate_provider(db: AsyncSession, p: Principal) -> ValidationOut:
    i = await integ.get_integration(db, p.tid, TIPO)
    try:
        prov, _ = await _provider(db, p.tid)
        await prov.validate()
    except (ProviderConfigError, ProviderTransient) as e:
        if i:
            i.estado = "error"
        return ValidationOut(valida=False, detalle=str(e))
    if i:
        i.estado, i.ultima_verificacion = "activa", datetime.now(UTC)
    return ValidationOut(valida=True)


# ================================================================== armado del documento
async def _take_number(db: AsyncSession, tid: UUID, serie: str, desde: int) -> int:
    """Consecutivo sin huecos: el UPSERT bloquea la fila y se revierte si algo falla antes del commit."""
    return int((await db.execute(text(
        "INSERT INTO document_sequences (tenant_id, serie, ultimo_numero) VALUES (:t, :s, :d) "
        "ON CONFLICT (tenant_id, serie) DO UPDATE SET ultimo_numero = GREATEST(document_sequences.ultimo_numero + 1, :d) "
        "RETURNING ultimo_numero"), {"t": tid, "s": serie, "d": desde})).scalar_one())


def _emisor(s: InvoiceSettings) -> dict[str, Any]:
    return {"nit": s.nit, "dv": s.dv if s.dv is not None else rules.nit_dv(s.nit), "razon_social": s.razon_social, "resolucion": s.resolucion,
            "prefijo": s.prefijo or "", "rango_desde": s.rango_desde, "rango_hasta": s.rango_hasta,
            "vigencia_hasta": s.vigencia_hasta.isoformat() if s.vigencia_hasta else None}


def _out(inv: Invoice) -> InvoiceOut:
    d, t = inv.datos or {}, (inv.datos or {}).get("totales", {})
    return InvoiceOut(id=inv.id, tipo=inv.tipo, estado=inv.estado, numero_completo=d.get("numero_completo") or rules.full_number(inv.prefijo, inv.numero),
                      sale_id=inv.sale_id, factura_origen_id=inv.factura_origen_id, fecha_emision=inv.fecha_emision,
                      subtotal=Decimal(t.get("subtotal", "0")), iva=Decimal(t.get("iva", "0")), total=Decimal(t.get("total", "0")), cufe=inv.cufe,
                      xml_url=inv.xml_url, pdf_url=inv.pdf_url, intentos=inv.intentos, ultimo_error=inv.ultimo_error, proximo_intento=inv.proximo_intento,
                      ambiente=d.get("ambiente"), proveedor=d.get("proveedor"))


async def emit_invoice(db: AsyncSession, p: Principal, sale_id: UUID, adq: AdquirenteIn | None) -> InvoiceOut:
    tid = p.tid
    s = await _settings(db, tid)
    i = await integ.get_integration(db, tid, TIPO)
    if s is None or i is None or not (i.config or {}).get("proveedor"):
        raise AppError("INTEGRATION_NOT_CONFIGURED", "Configura la resolución y el proveedor de facturación antes de emitir")
    sale = (await db.execute(select(Sale).where(Sale.tenant_id == tid, Sale.id == sale_id, Sale.deleted_at.is_(None)).with_for_update())).scalar_one_or_none()
    if sale is None:
        raise AppError("NOT_FOUND", "Venta no encontrada")
    if sale.estado != "pagada":
        raise AppError("INVOICE_NOT_ALLOWED", f"Solo se factura una venta pagada (esta está {sale.estado})")
    vivo = (await db.execute(select(Invoice.id).where(Invoice.tenant_id == tid, Invoice.sale_id == sale_id, Invoice.tipo == "electronica",
                                                      Invoice.estado.in_(rules.ACTIVE_STATES)))).scalar()
    if vivo:
        raise AppError("CONFLICT", "Esta venta ya tiene una factura electrónica", {"factura_id": str(vivo)})

    items = [{"id": x.id, "descripcion": x.descripcion, "precio_cobrado": x.precio_cobrado, "iva": x.iva}
             for x in (await db.execute(select(SaleItem).where(SaleItem.tenant_id == tid, SaleItem.sale_id == sale_id).order_by(SaleItem.descripcion))).scalars()]
    lines = rules.build_lines(items)
    tot = rules.totals(lines)
    if Decimal(tot["total"]) <= 0:
        raise AppError("INVOICE_NOT_ALLOWED", "No se factura una venta de valor cero")
    cust = await db.get(Customer, sale.customer_id) if sale.customer_id else None
    adquirente = rules.adquirente_for({"documento": cust.documento, "nombre": cust.nombre, "email": cust.email} if cust else None,
                                      adq.model_dump() if adq else None)
    pagos = [{"metodo": x.metodo, "monto": x.monto} for x in (await db.execute(select(Payment).where(Payment.tenant_id == tid, Payment.sale_id == sale_id))).scalars()]

    tz = await _tz(db, tid)
    now = datetime.now(tz)
    res = _res(s)
    numero = await _take_number(db, tid, f"FE:{res.prefijo}", res.desde)
    rules.check_resolution(res, numero, now.date())                  # fuera de rango/vencida: error y el consecutivo se revierte
    inv_id = uuid4()
    doc = {"referencia": str(inv_id), "tipo": "factura", "ambiente": s.ambiente, "proveedor": (i.config or {})["proveedor"], "sale_id": str(sale_id),
           "numero": numero, "prefijo": res.prefijo, "numero_completo": rules.full_number(res.prefijo, numero), "fecha_emision": now.isoformat(),
           "moneda": "COP", "emisor": _emisor(s), "adquirente": adquirente, "lineas": lines, "impuestos": rules.group_taxes(lines), "totales": tot,
           "medios_pago": rules.medios_pago(pagos), "forma_pago": "1",
           "nota": "La propina voluntaria no hace parte de la factura."}
    inv = Invoice(id=inv_id, tenant_id=tid, sale_id=sale_id, tipo="electronica", prefijo=res.prefijo, numero=numero, estado="emitida",
                  fecha_emision=now, datos=doc, proximo_intento=datetime.now(UTC))
    db.add(inv)
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError as e:
        raise AppError("CONFLICT", "Esta venta ya tiene una factura electrónica en curso") from e
    _aud(db, p, tid, "factura_emitida", inv.id, {"numero": doc["numero_completo"], "total": tot["total"]})
    return _out(inv)


# ================================================================== envío al proveedor y reintentos
def _apply(inv: Invoice, r: ProviderResult) -> str:
    if r.referencia:
        inv.referencia_proveedor = r.referencia
    inv.respuesta_proveedor = {**r.raw, "errores": r.errores} if (r.raw or r.errores) else None
    if r.estado == "aceptada":
        inv.estado, inv.cufe, inv.xml_url, inv.pdf_url = "aceptada", r.cufe, r.xml_url, r.pdf_url
        inv.proximo_intento, inv.ultimo_error = None, None
        return "aceptada"
    if r.estado == "rechazada":
        inv.estado, inv.proximo_intento = "rechazada", None
        inv.ultimo_error = "; ".join(r.errores) or "Rechazada por la DIAN"
        return "rechazada"
    inv.estado = "enviada"                                           # el proveedor la recibió; falta la validación de la DIAN
    inv.intentos += 1
    if inv.intentos >= rules.MAX_ATTEMPTS:
        inv.estado, inv.proximo_intento, inv.ultimo_error = "error", None, "El proveedor no confirmó la validación a tiempo"
        return "error"
    inv.proximo_intento = datetime.now(UTC) + timedelta(minutes=rules.backoff_minutes(inv.intentos))
    return "enviada"


async def dispatch(db: AsyncSession, inv: Invoice) -> str:
    """Un paso: enviar (si está `emitida`) o consultar (si está `enviada`). La fila debe venir bloqueada por quien llama."""
    try:
        prov, _ = await _provider(db, inv.tenant_id)
        r = await (prov.emit(inv.datos) if inv.estado == "emitida" else prov.status(inv.referencia_proveedor or inv.datos["referencia"], inv.datos))
    except ProviderConfigError as e:
        inv.estado, inv.proximo_intento, inv.ultimo_error = "error", None, str(e)[:500]
        _aud(db, None, inv.tenant_id, "factura_error_configuracion", inv.id, {"error": str(e)[:200]})
        return "error"
    except ProviderTransient as e:
        inv.intentos += 1
        inv.ultimo_error = str(e)[:500]
        if inv.intentos >= rules.MAX_ATTEMPTS:
            inv.estado, inv.proximo_intento = "error", None
            _aud(db, None, inv.tenant_id, "factura_agoto_reintentos", inv.id)
            return "error"
        inv.proximo_intento = datetime.now(UTC) + timedelta(minutes=rules.backoff_minutes(inv.intentos))
        return "reintento"
    out = _apply(inv, r)
    _aud(db, None, inv.tenant_id, f"factura_{out}", inv.id, {"numero": inv.datos.get("numero_completo"), "errores": r.errores})
    return out


async def run_dispatch(tenant_id: UUID, invoice_id: UUID) -> None:
    """Tarea de fondo tras emitir (fuera de la petición). Si falla, el barrido de Celery la retoma."""
    try:
        async with scoped_session(tenant_id=tenant_id) as db:
            inv = (await db.execute(select(Invoice).where(Invoice.tenant_id == tenant_id, Invoice.id == invoice_id, Invoice.estado == "emitida")
                                    .with_for_update(skip_locked=True))).scalar_one_or_none()
            if inv is not None:
                await dispatch(db, inv)
    except Exception:                                                # noqa: BLE001 - nunca romper la tarea de fondo
        log.exception("fallo_enviando_factura", extra={"tenant": str(tenant_id), "factura": str(invoice_id)})


async def process_pending(db: AsyncSession, limit: int = 30) -> dict[str, int]:
    """Sesión de plataforma (Celery). SKIP LOCKED: varios workers no procesan la misma factura."""
    rows = (await db.execute(select(Invoice).where(Invoice.tipo != "interna", Invoice.estado.in_(("emitida", "enviada")),
                                                   Invoice.proximo_intento <= datetime.now(UTC)).order_by(Invoice.proximo_intento)
                             .limit(limit).with_for_update(skip_locked=True))).scalars().all()
    out: dict[str, int] = {}
    for inv in rows:
        r = await dispatch(db, inv)
        out[r] = out.get(r, 0) + 1
    return out


async def retry_invoice(db: AsyncSession, p: Principal, inv_id: UUID, adq: AdquirenteIn | None) -> InvoiceOut:
    inv = (await db.execute(select(Invoice).where(Invoice.tenant_id == p.tid, Invoice.id == inv_id, Invoice.tipo != "interna").with_for_update())).scalar_one_or_none()
    if inv is None:
        raise AppError("NOT_FOUND", "Factura no encontrada")
    if not rules.can_retry(inv.estado):
        raise AppError("INVOICE_NOT_ALLOWED", f"Una factura {inv.estado} no se reenvía")
    datos = dict(inv.datos)
    if adq:
        if inv.tipo != "electronica":
            raise AppError("VALIDATION_ERROR", "Solo se corrige el adquirente de una factura")
        datos["adquirente"] = rules.adquirente_for(None, adq.model_dump())
    inv.datos = datos
    inv.estado, inv.intentos, inv.ultimo_error, inv.proximo_intento = "emitida", 0, None, datetime.now(UTC)    # MISMO número
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError as e:
        raise AppError("CONFLICT", "La venta ya tiene otra factura electrónica vigente") from e
    _aud(db, p, p.tid, "factura_reenviada", inv.id)
    return _out(inv)


# ================================================================== notas crédito
async def create_credit_note(db: AsyncSession, p: Principal, inv_id: UUID, d: CreditNoteIn) -> InvoiceOut:
    tid = p.tid
    fac = (await db.execute(select(Invoice).where(Invoice.tenant_id == tid, Invoice.id == inv_id).with_for_update())).scalar_one_or_none()
    if fac is None or fac.tipo != "electronica":
        raise AppError("NOT_FOUND", "Factura electrónica no encontrada")
    if fac.estado != "aceptada":
        raise AppError("INVOICE_NOT_ALLOWED", "Solo se acredita una factura ACEPTADA por la DIAN")
    s = await _settings(db, tid)
    if s is None:
        raise AppError("INTEGRATION_NOT_CONFIGURED", "Falta la configuración de facturación")
    previas = (await db.execute(select(Invoice).where(Invoice.tenant_id == tid, Invoice.factura_origen_id == fac.id, Invoice.tipo == "nota_credito",
                                                      Invoice.estado.in_(rules.ACTIVE_STATES)))).scalars().all()
    ya_items = {ln["item_id"] for nc in previas for ln in nc.datos.get("lineas", [])}
    ya_total = sum((Decimal(nc.datos["totales"]["total"]) for nc in previas), Decimal("0"))
    todas = {ln["item_id"]: ln for ln in fac.datos["lineas"]}
    pedidos = {str(x) for x in d.item_ids} if d.item_ids else set(todas) - ya_items
    desconocidos = pedidos - set(todas)
    if desconocidos:
        raise AppError("NOT_FOUND", "Hay ítems que no pertenecen a esa factura", {"items": sorted(desconocidos)})
    repetidos = pedidos & ya_items
    if repetidos:
        raise AppError("INVOICE_NOT_ALLOWED", "Algunos ítems ya fueron acreditados", {"items": sorted(repetidos)})
    lines = [todas[i] for i in todas if i in pedidos]
    tot = rules.totals(lines)
    rules.credit_note_room(Decimal(fac.datos["totales"]["total"]), ya_total, Decimal(tot["total"]))

    tz = await _tz(db, tid)
    now = datetime.now(tz)
    prefijo = s.prefijo_nota_credito
    numero = await _take_number(db, tid, f"NC:{prefijo}", 1)
    nc_id = uuid4()
    doc = {"referencia": str(nc_id), "tipo": "nota_credito", "ambiente": fac.datos["ambiente"], "proveedor": fac.datos["proveedor"], "sale_id": str(fac.sale_id),
           "numero": numero, "prefijo": prefijo, "numero_completo": rules.full_number(prefijo, numero), "fecha_emision": now.isoformat(), "moneda": "COP",
           "emisor": fac.datos["emisor"], "adquirente": fac.datos["adquirente"], "lineas": lines, "impuestos": rules.group_taxes(lines), "totales": tot,
           "medios_pago": fac.datos.get("medios_pago", []), "forma_pago": "1",
           "referencia_factura": {"numero_completo": fac.datos["numero_completo"], "cufe": fac.cufe, "fecha_emision": fac.datos["fecha_emision"], "id": str(fac.id)},
           "motivo_codigo": d.codigo_motivo, "motivo": d.motivo, "motivo_dian": rules.MOTIVOS_NC[d.codigo_motivo]}
    nc = Invoice(id=nc_id, tenant_id=tid, sale_id=fac.sale_id, tipo="nota_credito", factura_origen_id=fac.id, prefijo=prefijo, numero=numero, estado="emitida",
                 fecha_emision=now, datos=doc, motivo=d.motivo, proximo_intento=datetime.now(UTC))
    db.add(nc)
    await db.flush()
    _aud(db, p, tid, "nota_credito_emitida", nc.id, {"numero": doc["numero_completo"], "factura": fac.datos["numero_completo"], "total": tot["total"]})
    return _out(nc)


# ================================================================== consulta
async def list_invoices(db: AsyncSession, p: Principal, pp: PageParams, estado: str | None, tipo: str | None, sale_id: UUID | None,
                        desde: datetime | None, hasta: datetime | None) -> Page[InvoiceOut]:
    stmt = select(Invoice).where(Invoice.tenant_id == p.tid, Invoice.tipo != "interna")
    for col, val in ((Invoice.estado, estado), (Invoice.tipo, tipo), (Invoice.sale_id, sale_id)):
        if val is not None:
            stmt = stmt.where(col == val)
    if desde:
        stmt = stmt.where(Invoice.fecha_emision >= desde)
    if hasta:
        stmt = stmt.where(Invoice.fecha_emision < hasta)
    rows, total = await paginate(db, stmt, pp, {"fecha_emision": Invoice.fecha_emision, "numero": Invoice.numero}, "-fecha_emision")
    return Page[InvoiceOut](items=[_out(r) for r in rows], page=pp.page, page_size=pp.page_size, total=total)


async def _one(db: AsyncSession, p: Principal, inv_id: UUID) -> Invoice:
    inv = (await db.execute(select(Invoice).where(Invoice.tenant_id == p.tid, Invoice.id == inv_id, Invoice.tipo != "interna"))).scalar_one_or_none()
    if inv is None:
        raise AppError("NOT_FOUND", "Factura no encontrada")
    return inv


async def get_invoice(db: AsyncSession, p: Principal, inv_id: UUID) -> InvoiceDetail:
    inv = await _one(db, p, inv_id)
    return InvoiceDetail(**_out(inv).model_dump(), datos=inv.datos, respuesta_proveedor=inv.respuesta_proveedor)


async def sandbox_pdf(db: AsyncSession, p: Principal, inv_id: UUID) -> bytes:
    """Representación gráfica del proveedor SIMULADO. Los proveedores reales entregan su propio PDF/XML en `pdf_url`/`xml_url`."""
    inv = await _one(db, p, inv_id)
    d = inv.datos
    if d.get("proveedor") != "sandbox":
        raise AppError("NOT_FOUND", "Este documento tiene su PDF en el proveedor (pdf_url)")
    buf, st = BytesIO(), getSampleStyleSheet()
    doc = SimpleDocTemplate(buf, pagesize=A4, title=d["numero_completo"])
    es_nc = d["tipo"] == "nota_credito"
    def fila(ln: dict[str, Any]) -> list[str]:
        return [ln["descripcion"], f"${Decimal(ln['base']):,.0f}", f"{ln['iva_porcentaje']}%", f"${Decimal(ln['total']):,.0f}"]
    t = Table([["Descripción", "Base", "IVA", "Total"], *[fila(x) for x in d["lineas"]]])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.25, (0.7, 0.7, 0.7)), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("ALIGN", (1, 0), (-1, -1), "RIGHT")]))
    tot = d["totales"]
    story = [Paragraph("DOCUMENTO SIMULADO — SIN VALIDEZ FISCAL", st["Heading1"]),
             Paragraph(f"{'Nota crédito' if es_nc else 'Factura electrónica de venta'} {d['numero_completo']} · {d['ambiente']}", st["Heading2"]),
             Paragraph(f"{d['emisor']['razon_social']} · NIT {d['emisor']['nit']}-{d['emisor']['dv']}", st["Normal"]),
             Paragraph(f"Adquirente: {d['adquirente']['nombre']} ({d['adquirente']['documento']})", st["Normal"]),
             Paragraph(f"Fecha: {d['fecha_emision']} · Estado: {inv.estado}", st["Normal"])]
    if es_nc:
        story.append(Paragraph(f"Afecta la factura {d['referencia_factura']['numero_completo']} · Motivo: {d['motivo']}", st["Normal"]))
    story += [Spacer(1, 12), t, Spacer(1, 12),
              Paragraph(f"Subtotal ${Decimal(tot['subtotal']):,.0f} · IVA ${Decimal(tot['iva']):,.0f} · <b>Total ${Decimal(tot['total']):,.0f}</b>", st["Normal"]),
              Paragraph(f"CUFE/CUDE (referencial, no oficial): {inv.cufe or 'pendiente'}", st["Normal"])]
    doc.build(story)
    return buf.getvalue()

