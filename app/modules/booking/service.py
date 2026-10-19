"""Motor de reservas: administración (autenticada) y flujo público (sin login, por slug)."""
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import scoped_session
from app.core.errors import AppError
from app.core.repository import get_or_404, scoped
from app.core.tenancy import Principal, get_access_info
from app.modules.appointments import availability as av
from app.modules.appointments import service as apps
from app.modules.appointments.models import Appointment, AppointmentService
from app.modules.appointments.schemas import AppointmentIn
from app.modules.audit import service as audit
from app.modules.booking import rules
from app.modules.booking.models import AvailabilityRule, BookingFormField, BookingSettings
from app.modules.booking.schemas import (
    BookedService,
    BookingIn,
    BookingOut,
    BookingView,
    CancelIn,
    FieldIn,
    FieldOut,
    FieldPatch,
    PublicAvailability,
    PublicBrand,
    PublicConfig,
    PublicDay,
    PublicField,
    PublicService,
    PublicStaff,
    RescheduleIn,
    RuleIn,
    RuleOut,
    SettingsIn,
    SettingsOut,
)
from app.modules.catalog.models import Service, ServiceCategory
from app.modules.customers.models import Customer
from app.modules.staff.models import StaffProfile, StaffService

PUBLIC_MAX_DAYS = 14
NIL = UUID(int=0)


# ================================================================== administración
def _aud(db: AsyncSession, p: Principal, accion: str, ent: str, eid: UUID | None, despues: dict | None = None) -> None:  # type: ignore[type-arg]
    audit.record(db, actor_tipo="user", actor_id=p.id, tenant_id=p.tid, accion=accion, entidad=ent, entidad_id=eid, despues=despues)


async def get_admin_settings(db: AsyncSession, p: Principal) -> SettingsOut:
    s = (await db.execute(select(BookingSettings).where(BookingSettings.tenant_id == p.tid))).scalar_one_or_none()
    if s is None:                                             # todavía sin configurar: valores por defecto
        slug = (await db.execute(text("SELECT slug FROM tenants WHERE id = :t"), {"t": p.tid})).scalar_one()
        return SettingsOut(**SettingsIn().model_dump(exclude={"slug"}), slug=str(slug), configurado=False)
    return SettingsOut.model_validate(s)


async def put_admin_settings(db: AsyncSession, p: Principal, d: SettingsIn) -> SettingsOut:
    s = (await db.execute(select(BookingSettings).where(BookingSettings.tenant_id == p.tid))).scalar_one_or_none()
    data = d.model_dump(exclude={"slug"})
    slug = d.slug or (s.slug if s else str((await db.execute(text("SELECT slug FROM tenants WHERE id = :t"), {"t": p.tid})).scalar_one()))
    if s is None:
        s = BookingSettings(tenant_id=p.tid, slug=slug, **data)
        db.add(s)
    else:
        s.slug = slug
        for k, v in data.items():
            setattr(s, k, v)
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError as e:
        raise AppError("CONFLICT", "Esa dirección ya está en uso por otro negocio") from e
    _aud(db, p, "reservas_configuradas", "booking_settings", s.id, {"slug": slug, "activo": d.activo})
    return SettingsOut.model_validate(s)


async def list_fields(db: AsyncSession, p: Principal) -> list[FieldOut]:
    rows = (await db.execute(select(BookingFormField).where(BookingFormField.tenant_id == p.tid)
                             .order_by(BookingFormField.orden, BookingFormField.etiqueta))).scalars()
    return [FieldOut.model_validate(r) for r in rows]


async def create_field(db: AsyncSession, p: Principal, d: FieldIn) -> FieldOut:
    n = (await db.execute(select(func.count()).select_from(BookingFormField).where(BookingFormField.tenant_id == p.tid))).scalar_one()
    if n >= 20:
        raise AppError("CONFLICT", "Máximo 20 campos personalizados")
    f = BookingFormField(tenant_id=p.tid, **d.model_dump())
    db.add(f)
    await db.flush()
    _aud(db, p, "campo_creado", "booking_form_field", f.id, {"etiqueta": d.etiqueta})
    return FieldOut.model_validate(f)


async def _field(db: AsyncSession, p: Principal, fid: UUID) -> BookingFormField:
    f = (await db.execute(select(BookingFormField).where(BookingFormField.tenant_id == p.tid, BookingFormField.id == fid))).scalar_one_or_none()
    if f is None:
        raise AppError("NOT_FOUND", "Campo no encontrado")
    return f


async def patch_field(db: AsyncSession, p: Principal, fid: UUID, d: FieldPatch) -> FieldOut:
    f = await _field(db, p, fid)
    ch = d.model_dump(exclude_unset=True)
    if "opciones" in ch and f.tipo != "seleccion":
        raise AppError("VALIDATION_ERROR", "Solo los campos de selección llevan opciones")
    if f.tipo == "seleccion" and "opciones" in ch and not ch["opciones"]:
        raise AppError("VALIDATION_ERROR", "Un campo de selección necesita opciones")
    for k, v in ch.items():
        setattr(f, k, v)
    await db.flush()
    return FieldOut.model_validate(f)


async def delete_field(db: AsyncSession, p: Principal, fid: UUID) -> None:
    f = await _field(db, p, fid)
    await db.delete(f)
    _aud(db, p, "campo_eliminado", "booking_form_field", fid)


async def list_rules(db: AsyncSession, p: Principal) -> list[RuleOut]:
    rows = (await db.execute(select(AvailabilityRule).where(AvailabilityRule.tenant_id == p.tid))).scalars()
    return [RuleOut.model_validate(r) for r in rows]


async def create_rule(db: AsyncSession, p: Principal, d: RuleIn) -> RuleOut:
    if d.service_id:
        await get_or_404(db, Service, d.service_id, p.tid, "Servicio")
    r = AvailabilityRule(tenant_id=p.tid, **d.model_dump())
    db.add(r)
    await db.flush()
    _aud(db, p, "regla_cupos_creada", "availability_rule", r.id, d.model_dump(mode="json"))
    return RuleOut.model_validate(r)


async def delete_rule(db: AsyncSession, p: Principal, rid: UUID) -> None:
    r = (await db.execute(select(AvailabilityRule).where(AvailabilityRule.tenant_id == p.tid, AvailabilityRule.id == rid))).scalar_one_or_none()
    if r is None:
        raise AppError("NOT_FOUND", "Regla no encontrada")
    await db.delete(r)
    _aud(db, p, "regla_cupos_eliminada", "availability_rule", rid)


# ================================================================== público
@dataclass(frozen=True)
class PublicCtx:
    tenant_id: UUID
    negocio: str
    moneda: str
    zona_horaria: str
    s: BookingSettings           # copia desvinculada de la sesión
    tipo: str = "barberia"       # tipo de negocio: el frontend lo usa para el tema visual


def public_principal(tenant_id: UUID) -> Principal:
    return Principal(NIL, "public", tenant_id, None)


def _unavailable() -> AppError:
    # Misma respuesta para slug inexistente, desactivado, sin módulo o negocio bloqueado: no se filtra información.
    return AppError("NOT_FOUND", "Las reservas en línea no están disponibles")


async def resolve(slug: str, *, require_active: bool = True) -> PublicCtx:
    async with scoped_session(platform=True) as db:       # aún no se conoce el tenant: modo plataforma, solo lectura
        s = (await db.execute(select(BookingSettings).where(BookingSettings.slug == slug.lower()))).scalar_one_or_none()
        if s is None or (require_active and not s.activo):
            raise _unavailable()
        t = (await db.execute(text("SELECT nombre, moneda, zona_horaria, business_type FROM tenants WHERE id = :t AND deleted_at IS NULL"),
                              {"t": s.tenant_id})).one_or_none()
        modulo = (await db.execute(text(
            "SELECT 1 FROM tenant_modules tm JOIN modules m ON m.id = tm.module_id "
            "WHERE tm.tenant_id = :t AND m.codigo = 'booking' AND tm.activo"), {"t": s.tenant_id})).first()
        if t is None or modulo is None or (await get_access_info(db, s.tenant_id)).bloqueado:
            raise _unavailable()
        db.expunge(s)
        return PublicCtx(s.tenant_id, t.nombre, t.moneda, t.zona_horaria, s, t.business_type)


def default_settings(tenant_id: UUID) -> BookingSettings:
    """Valores por defecto explícitos (un objeto sin guardar no aplica los defaults de columna)."""
    return BookingSettings(tenant_id=tenant_id, slug="", activo=False, color_primario="#2F5D50", color_secundario="#E8B86D",
                           color_fondo="#FBF7F0", logo_url=None, textos={}, anticipacion_min_horas=1, anticipacion_max_dias=60,
                           buffer_min=0, cancelacion_min_horas=2, modo_asignacion="cliente_elige", pedir_servicio=True)


async def load_ctx(db: AsyncSession, tenant_id: UUID) -> PublicCtx:
    """Contexto de reservas de un negocio ya identificado (WhatsApp/IA): usa su configuración, o los valores por defecto
    si nunca configuró la página pública. No exige página activa: el canal de WhatsApp tiene sus propios interruptores."""
    s = (await db.execute(select(BookingSettings).where(BookingSettings.tenant_id == tenant_id))).scalar_one_or_none()         or default_settings(tenant_id)
    t = (await db.execute(text("SELECT nombre, moneda, zona_horaria, business_type FROM tenants WHERE id = :t AND deleted_at IS NULL"),
                          {"t": tenant_id})).one()
    return PublicCtx(tenant_id, t.nombre, t.moneda, t.zona_horaria, s, t.business_type)


async def public_config(db: AsyncSession, c: PublicCtx) -> PublicConfig:
    tid = c.tenant_id
    svc_rows = (await db.execute(
        select(Service, ServiceCategory.nombre)
        .outerjoin(ServiceCategory, (ServiceCategory.tenant_id == Service.tenant_id) & (ServiceCategory.id == Service.category_id))
        .where(Service.tenant_id == tid, Service.deleted_at.is_(None), Service.activo.is_(True))
        .order_by(ServiceCategory.orden, Service.nombre))).all()
    escoger = c.s.modo_asignacion == "cliente_elige"
    staff: list[PublicStaff] = []
    if escoger:
        links: dict[UUID, list[UUID]] = {}
        for sid, svc in (await db.execute(select(StaffService.staff_id, StaffService.service_id).where(StaffService.tenant_id == tid))).all():
            links.setdefault(sid, []).append(svc)
        for st in (await db.execute(scoped(StaffProfile, tid).where(StaffProfile.activo.is_(True)).order_by(StaffProfile.nombre_publico))).scalars():
            staff.append(PublicStaff(id=st.id, nombre_publico=st.nombre_publico, especialidad=st.especialidad, foto_url=st.foto_url,
                                     service_ids=links.get(st.id, [])))
    fields = (await db.execute(select(BookingFormField).where(BookingFormField.tenant_id == tid)
                               .order_by(BookingFormField.orden, BookingFormField.etiqueta))).scalars()
    t = c.s.textos or {}
    return PublicConfig(
        marca=PublicBrand(nombre=c.negocio, tipo=c.tipo, color_primario=c.s.color_primario, color_secundario=c.s.color_secundario,
                          color_fondo=c.s.color_fondo, logo_url=c.s.logo_url,
                          textos={k: v for k, v in t.items() if k != "consentimiento"}),
        zona_horaria=c.zona_horaria, moneda=c.moneda, pedir_servicio=c.s.pedir_servicio, modo_asignacion=c.s.modo_asignacion,
        escoger_profesional=escoger, anticipacion_min_horas=c.s.anticipacion_min_horas, anticipacion_max_dias=c.s.anticipacion_max_dias,
        cancelacion_min_horas=c.s.cancelacion_min_horas, consentimiento_texto=t.get("consentimiento") or rules.DEFAULT_CONSENT,
        servicios=[PublicService(id=s.id, nombre=s.nombre, categoria=cat, duracion_min=s.duracion_min, precio=s.precio) for s, cat in svc_rows],
        profesionales=staff,
        campos=[PublicField(id=f.id, etiqueta=f.etiqueta, tipo=f.tipo, obligatorio=f.obligatorio, opciones=f.opciones) for f in fields])


async def _default_duration(db: AsyncSession, tid: UUID) -> int:
    d = (await db.execute(text("SELECT min(duracion_turno_min) FROM availability_rules WHERE tenant_id = :t AND service_id IS NULL"),
                          {"t": tid})).scalar()
    return int(d or 30)


async def public_availability(db: AsyncSession, c: PublicCtx, desde: date, hasta: date, service_ids: list[UUID],
                              staff_id: UUID | None) -> PublicAvailability:
    if (hasta - desde).days >= PUBLIC_MAX_DAYS:
        raise AppError("VALIDATION_ERROR", f"Consulta máximo {PUBLIC_MAX_DAYS} días a la vez")
    if c.s.pedir_servicio and not service_ids:
        raise AppError("VALIDATION_ERROR", "Elige al menos un servicio")
    escoger = c.s.modo_asignacion == "cliente_elige"
    av_ = await apps.availability(
        db, c.tenant_id, desde=desde, hasta=hasta, service_ids=service_ids, staff_id=staff_id if escoger else None,
        duracion_min=None if service_ids else await _default_duration(db, c.tenant_id))
    return PublicAvailability(zona_horaria=av_.zona_horaria, duracion_min=av_.duracion_min, dias=[
        PublicDay(fecha=d.fecha, horarios=[s.inicio for s in d.slots],
                  profesionales_por_horario={s.inicio.isoformat(): s.staff_ids for s in d.slots} if escoger else {})
        for d in av_.dias])


# ---------------------------------------------------------------- crear reserva
async def _customer_for(db: AsyncSession, c: PublicCtx, d: BookingIn, phone: str, origen: str = "reserva_web") -> Customer:
    # Serializa por (negocio, teléfono): dos solicitudes simultáneas no crean al cliente dos veces.
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"cust:{c.tenant_id}:{phone}"})
    row = (await db.execute(text(
        "SELECT id FROM customers WHERE tenant_id = :t AND deleted_at IS NULL "
        "AND regexp_replace(coalesce(telefono, ''), '\\D', '', 'g') IN (:d, :d57) ORDER BY created_at LIMIT 1"),
        {"t": c.tenant_id, "d": phone, "d57": "57" + phone})).first()
    now = datetime.now(UTC)
    if row:
        cust = await db.get(Customer, row.id)
        assert cust is not None
        if not cust.consentimiento_datos:                    # nunca se pisan datos existentes con los de un formulario público
            cust.consentimiento_datos, cust.consentimiento_fecha = True, now
            cust.consentimiento_version = get_settings().privacy_policy_version
        return cust
    cust = Customer(tenant_id=c.tenant_id, nombre=rules.clean(d.cliente.nombre, 120), telefono=phone, email=d.cliente.email,
                    origen=origen, consentimiento_datos=True, consentimiento_fecha=now,
                    consentimiento_version=get_settings().privacy_policy_version)
    db.add(cust)
    await db.flush()
    return cust


async def _enqueue_confirmation(db: AsyncSession, c: PublicCtx, d: BookingIn, phone: str, appt_id: UUID) -> None:
    wa = (await db.execute(text(
        "SELECT 1 FROM tenant_modules tm JOIN modules m ON m.id = tm.module_id WHERE tm.tenant_id = :t AND m.codigo = 'whatsapp' AND tm.activo"),
        {"t": c.tenant_id})).first()
    canal, dest = ("whatsapp", ("57" + phone) if len(phone) == 10 else phone) if wa else ("email", d.cliente.email)
    if not dest:
        return
    # El enlace (token) se genera al enviar: no se guarda un secreto en la cola.
    await db.execute(text("INSERT INTO notifications (tenant_id, canal, destinatario, plantilla, payload) "
                          "VALUES (:t, :c, :d, 'reserva_confirmada', CAST(:p AS jsonb))"),
                     {"t": c.tenant_id, "c": canal, "d": str(dest), "p": json.dumps({"appointment_id": str(appt_id)})})


def _booking_out(c: PublicCtx, out, token: str) -> BookingOut:  # type: ignore[no-untyped-def]
    return BookingOut(
        token=token, estado=out.estado, negocio=c.negocio, inicio=out.inicio, fin=out.fin,
        profesional=out.profesional.nombre_publico if out.profesional else None,
        servicios=[BookedService(nombre=s.nombre, duracion_min=s.duracion_min, precio=s.precio_pactado) for s in out.servicios],
        total=out.total, puede_cancelar_hasta=out.inicio - timedelta(hours=c.s.cancelacion_min_horas))


async def create_booking(db: AsyncSession, c: PublicCtx, d: BookingIn, *, origen: str = "reserva_web") -> BookingOut:
    if d.website.strip():                                     # los humanos no ven este campo: solo lo llenan los bots
        raise AppError("VALIDATION_ERROR", "Solicitud inválida")
    if not d.consentimiento_datos:
        raise AppError("VALIDATION_ERROR", "Debes autorizar el tratamiento de tus datos para reservar", {"campo": "consentimiento_datos"})
    if c.s.pedir_servicio and not d.service_ids:
        raise AppError("VALIDATION_ERROR", "Elige al menos un servicio", {"campo": "service_ids"})
    defs = [rules.FieldDef(f.id, f.etiqueta, f.tipo, f.obligatorio, f.opciones) for f in (await db.execute(
        select(BookingFormField).where(BookingFormField.tenant_id == c.tenant_id).order_by(BookingFormField.orden))).scalars()]
    answers = rules.validate_answers(defs, d.respuestas)
    phone = rules.normalize_phone(d.cliente.telefono)

    cust = await _customer_for(db, c, d, phone, origen)
    activas = (await db.execute(select(func.count()).select_from(Appointment).where(
        Appointment.tenant_id == c.tenant_id, Appointment.customer_id == cust.id, Appointment.deleted_at.is_(None),
        Appointment.estado.in_(("pendiente", "confirmada")), Appointment.inicio > datetime.now(UTC)))).scalar_one()
    if activas >= rules.MAX_ACTIVE_PER_PHONE:
        raise AppError("TOO_MANY_BOOKINGS", f"Ya tienes {activas} reservas activas. Cancela una o espera a que pase para reservar otra.",
                       {"maximo": rules.MAX_ACTIVE_PER_PHONE})

    modo = c.s.modo_asignacion
    staff_id = d.staff_id if modo == "cliente_elige" else None            # fuera de ese modo se ignora lo que mande el cliente
    inp = AppointmentIn(customer_id=cust.id, staff_id=staff_id, inicio=d.inicio, service_ids=d.service_ids,
                        duracion_min=None if d.service_ids else await _default_duration(db, c.tenant_id),
                        notas=rules.notes_from_answers(answers), asignar_automatico=modo != "en_recepcion" and staff_id is None)
    out = await apps.create_appointment(db, public_principal(c.tenant_id), inp, strict=True, origen=origen)
    if origen == "reserva_web":                                  # por WhatsApp la confirmación es la propia conversación
        await _enqueue_confirmation(db, c, d, phone, out.id)
    return _booking_out(c, out, rules.make_token(out.id, c.tenant_id, out.inicio))


# ---------------------------------------------------------------- ver / cancelar / reprogramar con token
async def _by_token(db: AsyncSession, c: PublicCtx, token: str) -> Appointment:
    claims = rules.read_token(token)
    if claims.tenant_id != c.tenant_id:                       # un enlace de otro negocio no sirve aquí
        raise AppError("UNAUTHORIZED", "Enlace inválido")
    a: Appointment | None = (await db.execute(scoped(Appointment, c.tenant_id).where(Appointment.id == claims.appointment_id).with_for_update())).scalar_one_or_none()
    if a is None:
        raise AppError("UNAUTHORIZED", "Enlace inválido")
    if int(a.inicio.timestamp()) != claims.inicio_ts:
        raise AppError("UNAUTHORIZED", "Este enlace ya no es válido: la cita fue reprogramada. Usa el enlace más reciente.")
    return a


def _can_modify(c: PublicCtx, a: Appointment, now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    return a.estado in ("pendiente", "confirmada") and now <= a.inicio - timedelta(hours=c.s.cancelacion_min_horas)


def _assert_modifiable(c: PublicCtx, a: Appointment) -> None:
    if a.estado not in ("pendiente", "confirmada"):
        raise AppError("INVALID_STATE_TRANSITION", f"La cita está {a.estado}")
    if not _can_modify(c, a):
        raise AppError("CANCELLATION_WINDOW_CLOSED", f"Solo puedes cancelar o reprogramar hasta {c.s.cancelacion_min_horas} h antes. "
                       "Comunícate directamente con el negocio.", {"horas_minimas": c.s.cancelacion_min_horas})


async def _out_for(db: AsyncSession, c: PublicCtx, a: Appointment):  # type: ignore[no-untyped-def]
    return (await apps._outs(db, c.tenant_id, [a], (await apps.get_ctx(db, c.tenant_id)).tz))[0]


async def view_booking(db: AsyncSession, c: PublicCtx, token: str) -> BookingView:
    a = await _by_token(db, c, token)
    o = await _out_for(db, c, a)
    return BookingView(estado=o.estado, negocio=c.negocio, cliente=o.cliente.nombre.split(" ")[0], inicio=o.inicio, fin=o.fin,
                       profesional=o.profesional.nombre_publico if o.profesional else None,
                       servicios=[BookedService(nombre=s.nombre, duracion_min=s.duracion_min, precio=s.precio_pactado) for s in o.servicios],
                       total=o.total, puede_modificar=_can_modify(c, a),
                       puede_cancelar_hasta=o.inicio - timedelta(hours=c.s.cancelacion_min_horas))


async def cancel_appointment_for_customer(db: AsyncSession, c: PublicCtx, a: Appointment, motivo: str | None) -> None:
    """Cancela una cita a pedido del cliente (enlace o WhatsApp), respetando la ventana mínima del negocio."""
    _assert_modifiable(c, a)
    a.estado = "cancelada"
    if motivo:
        nota = f"[Cancelada por el cliente] {rules.clean(motivo, 300)}"
        a.notas = f"{a.notas}\n{nota}" if a.notas else nota
    await db.flush()
    audit.record(db, actor_tipo="system", actor_id=None, tenant_id=c.tenant_id, accion="cita_cancelada_por_cliente",
                 entidad="appointment", entidad_id=a.id, antes={"estado": "pendiente/confirmada"}, despues={"estado": "cancelada"})


async def cancel_booking(db: AsyncSession, c: PublicCtx, token: str, d: CancelIn) -> BookingView:
    a = await _by_token(db, c, token)
    await cancel_appointment_for_customer(db, c, a, d.motivo)
    return await view_booking(db, c, token)


async def reschedule_booking(db: AsyncSession, c: PublicCtx, token: str, d: RescheduleIn) -> BookingOut:
    a = await _by_token(db, c, token)
    _assert_modifiable(c, a)
    tid = c.tenant_id
    apctx = await apps.get_ctx(db, tid)
    new = d.inicio.astimezone(UTC)
    minutes = int((a.fin - a.inicio).total_seconds() // 60)
    svc_ids = list((await db.execute(select(AppointmentService.service_id).where(
        AppointmentService.tenant_id == tid, AppointmentService.appointment_id == a.id))).scalars())

    async def libres(staff: UUID | None) -> list[UUID]:
        return await apps.free_staff_at(db, tid, apctx, new, minutes, svc_ids, staff, ignore_lead=False, exclude=a.id)

    candidatos: list[UUID | None] = []
    if a.staff_id:
        if await libres(a.staff_id):
            candidatos = [a.staff_id]
        elif apctx.modo == "automatica":                      # el negocio asigna: puede pasarla a otro profesional libre
            candidatos = list(av.rank_staff(await libres(None), await apps._citas_del_dia(db, tid, apctx, new)))
    elif await libres(None):
        candidatos = [None]
    if not candidatos:
        raise AppError("SLOT_UNAVAILABLE", "Ese horario no está disponible")
    old = a.inicio
    for sid in candidatos:
        a.inicio, a.fin, a.staff_id = new, new + timedelta(minutes=minutes), sid
        try:
            async with db.begin_nested():
                await db.flush()
            break
        except DBAPIError as e:
            if not apps.is_slot_conflict(e):
                raise
    else:
        raise AppError("SLOT_TAKEN", "Ese horario acaba de ser ocupado. Elige otro.", status=409)
    audit.record(db, actor_tipo="system", actor_id=None, tenant_id=tid, accion="cita_reprogramada_por_cliente",
                 entidad="appointment", entidad_id=a.id, antes={"inicio": old.isoformat()}, despues={"inicio": new.isoformat()})
    o = await _out_for(db, c, a)
    return _booking_out(c, o, rules.make_token(a.id, tid, a.inicio))        # enlace nuevo; el anterior queda inválido
