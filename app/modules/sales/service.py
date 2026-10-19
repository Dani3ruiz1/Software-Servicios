"""Ventas: ítems, IVA, precio editable (regla 6), pagos divididos, cobro, anulación y comprobante."""
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.pagination import Page, PageParams, parse_sort
from app.core.repository import get_or_404, scoped
from app.core.tenancy import Principal
from app.modules.appointments.models import Appointment, AppointmentService
from app.modules.audit import service as audit
from app.modules.cash import service as cash
from app.modules.catalog.models import Service
from app.modules.customers.models import Customer
from app.modules.sales.models import Invoice, Payment, Sale, SaleItem
from app.modules.sales.schemas import (
    ItemOut,
    PayIn,
    PaymentIn,
    PaymentOut,
    Receipt,
    SaleIn,
    SaleItemIn,
    SaleListItem,
    SaleOut,
    SendIn,
    SendOut,
    VoidIn,
)
from app.modules.staff.models import StaffProfile

CENT = Decimal("0.01")
SERIE_INTERNA = "CI"


# ------------------------------------------------------------------ reglas puras (testeables)
def money(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


def item_iva(precio_cobrado: Decimal, aplica_iva: bool, porcentaje: Decimal) -> Decimal:
    """El precio de lista NO incluye IVA: se suma encima (total = subtotal + iva + propina)."""
    return money(precio_cobrado * porcentaje / Decimal(100)) if aplica_iva else Decimal("0.00")


def resolve_price(original: Decimal, solicitado: Decimal | None, editable: bool) -> Decimal:
    """Regla 6: solo se cambia el precio si el servicio es de precio editable. Siempre se conserva el original."""
    if solicitado is None or solicitado == original:
        return original
    if not editable:
        raise AppError("PRICE_NOT_EDITABLE", "Este servicio no permite cambiar el precio",
                       {"precio_original": str(original), "precio_cobrado": str(solicitado)})
    return solicitado


def validate_payments(total: Decimal, pagos: list[PaymentIn]) -> None:
    """Pago dividido: la suma debe ser EXACTAMENTE el total. Transferencia exige banco (regla 9)."""
    for p in pagos:
        if p.metodo == "transferencia" and not (p.entidad_bancaria or "").strip():
            raise AppError("BANK_ENTITY_REQUIRED", "La transferencia exige la entidad bancaria")
    pagado = sum((p.monto for p in pagos), Decimal("0"))
    if pagado != total:
        raise AppError("PAYMENT_MISMATCH", "Los pagos no suman el total de la venta",
                       {"total": str(total), "pagado": str(pagado), "diferencia": str(total - pagado)})


@dataclass
class Line:
    service_id: UUID | None
    descripcion: str
    original: Decimal
    cobrado: Decimal
    iva: Decimal


def totals(lines: list[Line], propina: Decimal) -> tuple[Decimal, Decimal, Decimal]:
    sub = sum((ln.cobrado for ln in lines), Decimal("0"))
    iva = sum((ln.iva for ln in lines), Decimal("0"))
    return sub, iva, sub + iva + propina


def _aud(db: AsyncSession, p: Principal, accion: str, sid: UUID, antes: dict | None = None,  # type: ignore[type-arg]
         despues: dict | None = None) -> None:  # type: ignore[type-arg]
    audit.record(db, actor_tipo="user", actor_id=p.id, tenant_id=p.tid, accion=accion, entidad="sale",
                 entidad_id=sid, antes=antes, despues=despues)


# ------------------------------------------------------------------ armado de ítems
async def _lines_from_items(db: AsyncSession, tid: UUID, items: list[SaleItemIn]) -> list[Line]:
    ids = {i.service_id for i in items if i.service_id}
    svc = {s.id: s for s in (await db.execute(scoped(Service, tid).where(Service.id.in_(ids)))).scalars()} if ids else {}
    out: list[Line] = []
    for it in items:
        if it.service_id is None:                       # ítem libre (producto, etc.): sin precio de lista que proteger
            out.append(Line(None, it.descripcion or "", it.precio_cobrado or Decimal(0), it.precio_cobrado or Decimal(0), Decimal("0.00")))
            continue
        s = svc.get(it.service_id)
        if s is None:
            raise AppError("NOT_FOUND", "Servicio no encontrado", {"service_id": str(it.service_id)})
        cobrado = resolve_price(s.precio, it.precio_cobrado, s.precio_editable)
        out.append(Line(s.id, s.nombre, s.precio, cobrado, item_iva(cobrado, s.aplica_iva, s.porcentaje_iva)))
    return out


async def _lines_from_appointment(db: AsyncSession, tid: UUID, appt: Appointment, overrides: dict[UUID, Decimal | None]) -> list[Line]:
    """Precio original = el pactado al agendar (lo que se le cotizó al cliente)."""
    rows = (await db.execute(
        select(AppointmentService, Service).join(Service, Service.id == AppointmentService.service_id)
        .where(AppointmentService.tenant_id == tid, AppointmentService.appointment_id == appt.id))).all()
    out = []
    for aps, s in rows:
        cobrado = resolve_price(aps.precio_pactado, overrides.get(s.id), s.precio_editable)
        out.append(Line(s.id, s.nombre, aps.precio_pactado, cobrado, item_iva(cobrado, s.aplica_iva, s.porcentaje_iva)))
    return out


# ------------------------------------------------------------------ crear / cobrar
async def create_sale(db: AsyncSession, p: Principal, d: SaleIn) -> SaleOut:
    tid = p.tid
    customer_id, staff_id, appt = d.customer_id, d.staff_id, None
    if d.appointment_id:
        appt = await get_or_404(db, Appointment, d.appointment_id, tid, "Cita")
        if appt.estado in ("cancelada", "no_asistio"):
            raise AppError("INVALID_STATE_TRANSITION", f"No se puede cobrar una cita {appt.estado}")
        if (await db.execute(select(Sale.id).where(Sale.tenant_id == tid, Sale.appointment_id == appt.id,
                                                    Sale.estado != "anulada"))).first():
            raise AppError("CONFLICT", "Esa cita ya tiene una venta")
        customer_id, staff_id = customer_id or appt.customer_id, staff_id or appt.staff_id
    if customer_id:
        await get_or_404(db, Customer, customer_id, tid, "Cliente")
    if staff_id:
        await get_or_404(db, StaffProfile, staff_id, tid, "Profesional")

    if d.items:
        lines = await _lines_from_items(db, tid, d.items)
    else:
        assert appt is not None
        lines = await _lines_from_appointment(db, tid, appt, {})
        if not lines:
            raise AppError("VALIDATION_ERROR", "La cita no tiene servicios: envía items")
    sub, iva, total = totals(lines, d.propina)

    sale = Sale(tenant_id=tid, customer_id=customer_id, staff_id=staff_id, appointment_id=d.appointment_id, subtotal=sub,
                iva=iva, propina=d.propina, total=total, estado="abierta", created_by=p.id)
    db.add(sale)
    await db.flush()
    for ln in lines:
        db.add(SaleItem(tenant_id=tid, sale_id=sale.id, service_id=ln.service_id, descripcion=ln.descripcion,
                        precio_original=ln.original, precio_cobrado=ln.cobrado, iva=ln.iva))
    await db.flush()
    _aud(db, p, "venta_creada", sale.id, despues={"total": str(total), "items": len(lines)})
    if d.pagos:
        await _collect(db, p, sale, d.pagos)
    return await get_sale(db, p, sale.id)


async def _next_number(db: AsyncSession, tid: UUID) -> int:
    """Consecutivo sin huecos por negocio: el UPSERT bloquea la fila y se revierte con la transacción."""
    return int((await db.execute(text(
        "INSERT INTO document_sequences (tenant_id, serie, ultimo_numero) VALUES (:t, :s, 1) "
        "ON CONFLICT (tenant_id, serie) DO UPDATE SET ultimo_numero = document_sequences.ultimo_numero + 1 "
        "RETURNING ultimo_numero"), {"t": tid, "s": SERIE_INTERNA})).scalar_one())


async def _collect(db: AsyncSession, p: Principal, sale: Sale, pagos: list[PaymentIn]) -> None:
    if sale.estado != "abierta":
        raise AppError("CONFLICT", f"La venta está {sale.estado}")
    validate_payments(sale.total, pagos)
    sesion = await cash.require_open_session(db, p.tid)      # regla 9 (configurable por negocio)
    sale.cash_session_id = sesion.id if sesion else None            # la venta cae en la caja donde se COBRÓ
    for pg in pagos:
        db.add(Payment(tenant_id=p.tid, sale_id=sale.id, **pg.model_dump()))
    n = await _next_number(db, p.tid)
    sale.comprobante_numero = f"{SERIE_INTERNA}-{n:06d}"
    db.add(Invoice(tenant_id=p.tid, sale_id=sale.id, tipo="interna", prefijo=SERIE_INTERNA, numero=n))
    sale.estado, sale.pagada_en = "pagada", datetime.now(UTC)
    if sale.appointment_id:
        appt = await get_or_404(db, Appointment, sale.appointment_id, p.tid, "Cita")
        if appt.estado in ("pendiente", "confirmada", "en_atencion"):
            appt.estado = "atendida"                                # cobrar = el servicio se prestó
    await db.flush()
    _aud(db, p, "venta_cobrada", sale.id, despues={"comprobante": sale.comprobante_numero, "total": str(sale.total),
                                                    "metodos": [pg.metodo for pg in pagos]})


async def _locked_sale(db: AsyncSession, p: Principal, sid: UUID) -> Sale:
    s: Sale | None = (await db.execute(scoped(Sale, p.tid).where(Sale.id == sid).with_for_update())).scalar_one_or_none()
    if s is None:
        raise AppError("NOT_FOUND", "Venta no encontrada")
    return s


async def pay_sale(db: AsyncSession, p: Principal, sid: UUID, d: PayIn) -> SaleOut:
    sale = await _locked_sale(db, p, sid)                           # candado: dos cobros simultáneos no duplican
    if d.propina is not None and sale.estado == "abierta":
        sale.propina = d.propina
        sale.total = sale.subtotal + sale.iva + d.propina
    await _collect(db, p, sale, d.pagos)
    return await get_sale(db, p, sid)


async def void_sale(db: AsyncSession, p: Principal, sid: UUID, d: VoidIn) -> SaleOut:
    sale = await _locked_sale(db, p, sid)
    if sale.estado == "anulada":
        raise AppError("CONFLICT", "La venta ya está anulada")
    if sale.estado == "pagada" and sale.cash_session_id:
        abierta = await cash.get_open_session(db, p.tid, lock="share")
        if abierta is None or abierta.id != sale.cash_session_id:
            raise AppError("CONFLICT", "La caja de esta venta ya fue cerrada: no se puede anular. Usa una nota crédito.")
    antes = sale.estado
    sale.estado, sale.anulada_en, sale.anulada_motivo = "anulada", datetime.now(UTC), d.motivo
    if sale.appointment_id:
        appt = await get_or_404(db, Appointment, sale.appointment_id, p.tid, "Cita")
        if appt.estado == "atendida":
            appt.estado = "en_atencion"                              # queda lista para cobrarse de nuevo
    await db.flush()
    _aud(db, p, "venta_anulada", sale.id, {"estado": antes}, {"motivo": d.motivo})
    return await get_sale(db, p, sid)


# ------------------------------------------------------------------ lectura
async def _sale_out(db: AsyncSession, tid: UUID, s: Sale) -> SaleOut:
    items = (await db.execute(select(SaleItem).where(SaleItem.tenant_id == tid, SaleItem.sale_id == s.id)
                              .order_by(SaleItem.descripcion))).scalars().all()
    pagos = (await db.execute(select(Payment).where(Payment.tenant_id == tid, Payment.sale_id == s.id)
                              .order_by(Payment.created_at))).scalars().all()
    cliente = (await db.execute(select(Customer.nombre).where(Customer.tenant_id == tid, Customer.id == s.customer_id))).scalar() \
        if s.customer_id else None
    prof = (await db.execute(select(StaffProfile.nombre_publico).where(StaffProfile.tenant_id == tid, StaffProfile.id == s.staff_id))).scalar() \
        if s.staff_id else None
    return SaleOut(
        id=s.id, estado=s.estado, customer_id=s.customer_id, cliente=cliente, staff_id=s.staff_id, profesional=prof,
        appointment_id=s.appointment_id, cash_session_id=s.cash_session_id, subtotal=s.subtotal, iva=s.iva, propina=s.propina,
        total=s.total, comprobante_numero=s.comprobante_numero, created_at=s.created_at, pagada_en=s.pagada_en,
        anulada_en=s.anulada_en, anulada_motivo=s.anulada_motivo,
        items=[ItemOut(id=i.id, service_id=i.service_id, descripcion=i.descripcion, precio_original=i.precio_original,
                       precio_cobrado=i.precio_cobrado, descuento=i.precio_original - i.precio_cobrado, iva=i.iva) for i in items],
        pagos=[PaymentOut(id=x.id, metodo=x.metodo, monto=x.monto, entidad_bancaria=x.entidad_bancaria,
                          referencia=x.referencia) for x in pagos])


async def get_sale(db: AsyncSession, p: Principal, sid: UUID) -> SaleOut:
    return await _sale_out(db, p.tid, await get_or_404(db, Sale, sid, p.tid, "Venta"))


async def list_sales(db: AsyncSession, p: Principal, pp: PageParams, desde: datetime | None, hasta: datetime | None,
                     estado: str | None, customer_id: UUID | None, staff_id: UUID | None,
                     cash_session_id: UUID | None) -> Page[SaleListItem]:
    stmt = (select(Sale, Customer.nombre, StaffProfile.nombre_publico)
            .outerjoin(Customer, (Customer.tenant_id == Sale.tenant_id) & (Customer.id == Sale.customer_id))
            .outerjoin(StaffProfile, (StaffProfile.tenant_id == Sale.tenant_id) & (StaffProfile.id == Sale.staff_id))
            .where(Sale.tenant_id == p.tid, Sale.deleted_at.is_(None)))
    for col, val in ((Sale.estado, estado), (Sale.customer_id, customer_id), (Sale.staff_id, staff_id),
                     (Sale.cash_session_id, cash_session_id)):
        if val is not None:
            stmt = stmt.where(col == val)
    if desde:
        stmt = stmt.where(Sale.created_at >= desde)
    if hasta:
        stmt = stmt.where(Sale.created_at < hasta)
    total = (await db.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))).scalar_one()
    order_col, desc = parse_sort(pp.sort, {"created_at": Sale.created_at, "total": Sale.total, "pagada_en": Sale.pagada_en}, "-created_at")
    rows = (await db.execute(stmt.order_by(order_col.desc() if desc else order_col.asc()).offset(pp.offset).limit(pp.page_size))).all()  # type: ignore[attr-defined]
    return Page[SaleListItem](page=pp.page, page_size=pp.page_size, total=total, items=[
        SaleListItem(id=s.id, estado=s.estado, cliente=c, profesional=n, total=s.total, comprobante_numero=s.comprobante_numero,
                     created_at=s.created_at, pagada_en=s.pagada_en) for s, c, n in rows])


async def receipt(db: AsyncSession, p: Principal, sid: UUID) -> Receipt:
    s = await get_sale(db, p, sid)
    t = (await db.execute(text("SELECT t.nombre, t.nit, t.moneda, i.razon_social FROM tenants t "
                               "LEFT JOIN invoice_settings i ON i.tenant_id = t.id WHERE t.id = :t"), {"t": p.tid})).one()
    return Receipt(numero=s.comprobante_numero, estado=s.estado, negocio={"nombre": t.razon_social or t.nombre, "nit": t.nit},
                   fecha=s.pagada_en or s.created_at, cliente=s.cliente, profesional=s.profesional, items=s.items, pagos=s.pagos,
                   subtotal=s.subtotal, iva=s.iva, propina=s.propina, total=s.total, moneda=t.moneda)


async def send_receipt(db: AsyncSession, p: Principal, sid: UUID, d: SendIn) -> SendOut:
    sale = await get_or_404(db, Sale, sid, p.tid, "Venta")
    if sale.estado != "pagada":
        raise AppError("CONFLICT", "Solo se envían comprobantes de ventas pagadas")
    dest = d.destinatario
    if not dest and sale.customer_id:
        c = await get_or_404(db, Customer, sale.customer_id, p.tid, "Cliente")
        dest = c.telefono if d.canal == "whatsapp" else c.email
    if not dest:
        raise AppError("VALIDATION_ERROR", "No hay destinatario: el cliente no tiene ese dato; envía `destinatario`")
    nid = (await db.execute(text(
        "INSERT INTO notifications (tenant_id, canal, destinatario, plantilla, payload) "
        "VALUES (:t, :c, :d, 'comprobante', CAST(:p AS jsonb)) RETURNING id"),
        {"t": p.tid, "c": d.canal, "d": dest, "p": json.dumps({"sale_id": str(sale.id), "comprobante": sale.comprobante_numero})})).scalar_one()
    _aud(db, p, "comprobante_enviado", sale.id, despues={"canal": d.canal})
    # El envío real lo hace el worker de notificaciones (Fase 7); aquí queda en cola.
    return SendOut(notificacion_id=nid, estado="pendiente", canal=d.canal, destinatario=dest)

