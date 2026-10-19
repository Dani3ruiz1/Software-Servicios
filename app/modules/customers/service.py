from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.pagination import Page, PageParams
from app.core.repository import get_or_404, paginate, scoped
from app.core.tenancy import Principal
from app.modules.appointments.models import Appointment, AppointmentService
from app.modules.audit import service as audit
from app.modules.booking.rules import normalize_phone
from app.modules.catalog.models import Service
from app.modules.customers.models import Customer
from app.modules.customers.schemas import (
    CustomerHistory,
    CustomerIn,
    CustomerOut,
    CustomerPatch,
    HistoryItem,
    HistoryServiceItem,
)
from app.modules.staff.models import StaffProfile


def _aud(db: AsyncSession, p: Principal, accion: str, c: Customer, despues: dict | None = None) -> None:  # type: ignore[type-arg]
    audit.record(db, actor_tipo="user", actor_id=p.id, tenant_id=p.tid, accion=accion,
                 entidad="customer", entidad_id=c.id, despues=despues)


def _grant_consent(c: Customer) -> None:
    c.consentimiento_datos = True
    c.consentimiento_fecha = datetime.now(UTC)
    c.consentimiento_version = get_settings().privacy_policy_version


async def list_customers(db: AsyncSession, p: Principal, pp: PageParams, q: str | None) -> Page[CustomerOut]:
    stmt = scoped(Customer, p.tid)
    if q:
        like = f"%{q.strip()}%"
        digits = "".join(ch for ch in q if ch.isdigit())        # "310 555" también encuentra a quien se guardó como 3105550142
        cond = [Customer.nombre.ilike(like), Customer.telefono.ilike(like), Customer.email.ilike(like), Customer.documento.ilike(like)]
        if len(digits) >= 3:
            cond.append(Customer.telefono.ilike(f"%{digits}%"))
        stmt = stmt.where(or_(*cond))
    rows, total = await paginate(db, stmt, pp, {"nombre": Customer.nombre, "created_at": Customer.created_at}, "nombre")
    return Page[CustomerOut](items=[CustomerOut.model_validate(r) for r in rows], page=pp.page,
                             page_size=pp.page_size, total=total)


async def get_customer(db: AsyncSession, p: Principal, cid: UUID) -> CustomerOut:
    return CustomerOut.model_validate(await get_or_404(db, Customer, cid, p.tid, "Cliente"))


def _phone(raw: str | None) -> str | None:
    """Mismo formato que la reserva en línea (solo dígitos, sin el 57): así un cliente no queda duplicado según por dónde llegue."""
    return normalize_phone(raw) if raw and raw.strip() else None


async def create_customer(db: AsyncSession, p: Principal, d: CustomerIn) -> CustomerOut:
    c = Customer(tenant_id=p.tid, **{**d.model_dump(exclude={"consentimiento_datos"}), "telefono": _phone(d.telefono)})
    if d.consentimiento_datos:
        _grant_consent(c)
    db.add(c)
    await db.flush()
    _aud(db, p, "cliente_creado", c, {"consentimiento": d.consentimiento_datos})
    return CustomerOut.model_validate(c)


async def patch_customer(db: AsyncSession, p: Principal, cid: UUID, d: CustomerPatch) -> CustomerOut:
    c = await get_or_404(db, Customer, cid, p.tid, "Cliente")
    ch = d.model_dump(exclude_unset=True)
    consent = ch.pop("consentimiento_datos", None)
    if "telefono" in ch:
        ch["telefono"] = _phone(ch["telefono"])
    for k, v in ch.items():
        setattr(c, k, v)
    if consent is True and not c.consentimiento_datos:
        _grant_consent(c)
        _aud(db, p, "consentimiento_otorgado", c)
    elif consent is False and c.consentimiento_datos:
        c.consentimiento_datos = False                    # revocación: se conserva la fecha del último consentimiento
        _aud(db, p, "consentimiento_revocado", c)
    await db.flush()
    return CustomerOut.model_validate(c)


async def delete_customer(db: AsyncSession, p: Principal, cid: UUID) -> None:
    c = await get_or_404(db, Customer, cid, p.tid, "Cliente")
    c.deleted_at = datetime.now(UTC)      # borrado lógico: el historial de citas/ventas se conserva
    _aud(db, p, "cliente_eliminado", c)


async def history(db: AsyncSession, p: Principal, cid: UUID, pp: PageParams) -> CustomerHistory:
    c = await get_or_404(db, Customer, cid, p.tid, "Cliente")
    base = select(Appointment).where(Appointment.tenant_id == p.tid, Appointment.customer_id == cid,
                                     Appointment.deleted_at.is_(None))
    counts = dict((await db.execute(select(Appointment.estado, func.count()).where(
        Appointment.tenant_id == p.tid, Appointment.customer_id == cid, Appointment.deleted_at.is_(None))
        .group_by(Appointment.estado))).all())
    last = (await db.execute(select(func.max(Appointment.inicio)).where(
        Appointment.tenant_id == p.tid, Appointment.customer_id == cid, Appointment.estado == "atendida"))).scalar()
    appts = list((await db.execute(base.order_by(Appointment.inicio.desc()).offset(pp.offset).limit(pp.page_size))).scalars())
    svc: dict[UUID, list[HistoryServiceItem]] = {}
    if appts:
        rows = await db.execute(select(AppointmentService.appointment_id, Service.nombre, AppointmentService.precio_pactado)
                                .join(Service, Service.id == AppointmentService.service_id)
                                .where(AppointmentService.tenant_id == p.tid,
                                       AppointmentService.appointment_id.in_([a.id for a in appts])))
        for aid, nombre, precio in rows:
            svc.setdefault(aid, []).append(HistoryServiceItem(nombre=nombre, precio_pactado=precio))
    names = {sid: n for sid, n in (await db.execute(select(StaffProfile.id, StaffProfile.nombre_publico).where(
        StaffProfile.tenant_id == p.tid, StaffProfile.id.in_([a.staff_id for a in appts if a.staff_id])))).all()}
    # Las ventas del cliente se añaden en la Fase 5 (módulo sales).
    return CustomerHistory(
        cliente=CustomerOut.model_validate(c), total_citas=sum(counts.values()), atendidas=counts.get("atendida", 0),
        canceladas=counts.get("cancelada", 0), no_asistio=counts.get("no_asistio", 0), ultima_visita=last,
        citas=[HistoryItem(appointment_id=a.id, inicio=a.inicio, estado=a.estado, profesional=names.get(a.staff_id) if a.staff_id else None,
                           servicios=svc.get(a.id, [])) for a in appts])
