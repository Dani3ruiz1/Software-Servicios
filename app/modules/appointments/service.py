"""Agenda: citas, estados, mover, vistas por rango y disponibilidad (carga datos y delega en availability.py)."""
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import distinct, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.pagination import Page, PageParams
from app.core.rbac import permissions_for
from app.core.repository import get_or_404, paginate, scoped
from app.core.tenancy import Principal
from app.modules.appointments import availability as av
from app.modules.appointments.models import Appointment, AppointmentService
from app.modules.appointments.schemas import (
    AppointmentIn,
    AppointmentOut,
    AppointmentPatch,
    AvailabilityOut,
    BlockBrief,
    CalendarOut,
    CustomerBrief,
    DaySlots,
    MoveIn,
    ServiceLine,
    SlotOut,
    StaffBrief,
    StatsOut,
    StatusIn,
    Vista,
)
from app.modules.audit import service as audit
from app.modules.catalog.models import Service
from app.modules.catalog.service import services_by_ids
from app.modules.customers.models import Customer
from app.modules.staff.models import ScheduleBlock, StaffProfile, StaffSchedule, StaffService

MAX_RANGE_DAYS = 31
OVERLAP_CONSTRAINT = "ex_appointments_no_overlap"


def is_slot_conflict(e: DBAPIError) -> bool:
    """Choque de horario: la restricción de exclusión, o el deadlock que Postgres puede provocar cuando
    dos inserciones simultáneas se pisan (aborta a una; para el usuario significa lo mismo: horario ocupado)."""
    msg = str(e.orig)
    return OVERLAP_CONSTRAINT in msg or "deadlock detected" in msg


# ------------------------------------------------------------------ contexto del tenant
@dataclass
class Ctx:
    tz_name: str
    buffer_min: int = 0
    min_horas: int = 0
    max_dias: int = 60
    modo: str = "cliente_elige"

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.tz_name)


async def get_ctx(db: AsyncSession, tenant_id: UUID) -> Ctx:
    r = (await db.execute(text(
        "SELECT t.zona_horaria, coalesce(b.buffer_min, 0) AS buf, coalesce(b.anticipacion_min_horas, 0) AS mn, "
        "coalesce(b.anticipacion_max_dias, 60) AS mx, coalesce(b.modo_asignacion, 'cliente_elige') AS modo "
        "FROM tenants t LEFT JOIN booking_settings b ON b.tenant_id = t.id WHERE t.id = :t"), {"t": tenant_id})).one()
    return Ctx(r.zona_horaria, r.buf, r.mn, r.mx, r.modo)


def _aud(db: AsyncSession, p: Principal, accion: str, aid: UUID, antes: dict | None = None,  # type: ignore[type-arg]
         despues: dict | None = None) -> None:  # type: ignore[type-arg]
    publico = p.kind == "public"          # reservas en línea: no hay usuario, el actor es el sistema
    audit.record(db, actor_tipo="system" if publico else "user", actor_id=None if publico else p.id, tenant_id=p.tid,
                 accion=accion, entidad="appointment", entidad_id=aid, antes=antes, despues=despues)


# ------------------------------------------------------------------ carga de datos para el motor
@dataclass
class Loaded:
    staff_ids: list[UUID]
    duration: int
    step: int
    sched: dict[tuple[UUID, int], list[tuple[time, time]]] = field(default_factory=dict)
    blocks: dict[UUID, list[av.Interval]] = field(default_factory=dict)
    by_staff: dict[UUID, list[av.Interval]] = field(default_factory=dict)
    unassigned: list[av.Interval] = field(default_factory=list)
    caps: list[av.Cap] = field(default_factory=list)

    def staff_day(self, d: date) -> list[av.StaffDay]:
        return [av.StaffDay(s, self.sched.get((s, d.weekday()), []), self.blocks.get(s, []), self.by_staff.get(s, []))
                for s in self.staff_ids]


async def _qualified_staff(db: AsyncSession, tid: UUID, service_ids: list[UUID], staff_id: UUID | None) -> list[UUID]:
    q = scoped(StaffProfile, tid).where(StaffProfile.activo.is_(True))
    if staff_id:
        q = q.where(StaffProfile.id == staff_id)
    ids = set(service_ids)
    if ids:
        ok = (select(StaffService.staff_id).where(StaffService.tenant_id == tid, StaffService.service_id.in_(ids))
              .group_by(StaffService.staff_id).having(func.count(distinct(StaffService.service_id)) == len(ids)))
        q = q.where(StaffProfile.id.in_(ok))
    return [s.id for s in (await db.execute(q.order_by(StaffProfile.nombre_publico))).scalars()]


async def _load(db: AsyncSession, tid: UUID, utc_from: datetime, utc_to: datetime, service_ids: list[UUID],
                staff_id: UUID | None, duration: int, exclude: UUID | None = None) -> Loaded:
    staff_ids = await _qualified_staff(db, tid, service_ids, staff_id)
    rules = (await db.execute(text(
        "SELECT service_id, staff_id, cupos_simultaneos, duracion_turno_min FROM availability_rules WHERE tenant_id = :t"),
        {"t": tid})).all()
    wanted = set(service_ids)
    # Alcance de las reglas: solo las del negocio entero (staff_id NULL) y de los servicios pedidos (o todos)
    rules = [r for r in rules if r.staff_id is None and (r.service_id is None or r.service_id in wanted)]
    step_rule = next((r.duracion_turno_min for r in sorted(rules, key=lambda r: r.service_id is None)
                      if r.duracion_turno_min), None)
    ld = Loaded(staff_ids, duration, step_rule or (30 if duration >= 30 else max(duration, 5)))
    if not staff_ids:
        return ld
    for r in (await db.execute(select(StaffSchedule).where(StaffSchedule.tenant_id == tid,
                                                           StaffSchedule.staff_id.in_(staff_ids)))).scalars():
        ld.sched.setdefault((r.staff_id, r.dia_semana), []).append((r.hora_inicio, r.hora_fin))
    for b in (await db.execute(select(ScheduleBlock).where(
            ScheduleBlock.tenant_id == tid, ScheduleBlock.staff_id.in_(staff_ids),
            ScheduleBlock.inicio < utc_to, ScheduleBlock.fin > utc_from))).scalars():
        ld.blocks.setdefault(b.staff_id, []).append(av.Interval(b.inicio, b.fin))
    q = scoped(Appointment, tid).where(Appointment.estado.in_(av.ESTADOS_QUE_OCUPAN), Appointment.inicio < utc_to,
                                       Appointment.fin > utc_from)
    if exclude:                                    # al reprogramar, la cita no debe chocar consigo misma
        q = q.where(Appointment.id != exclude)
    appts = list((await db.execute(q)).scalars())
    for a in appts:
        iv = av.Interval(a.inicio, a.fin)
        if a.staff_id is None:
            ld.unassigned.append(iv)
        else:
            ld.by_staff.setdefault(a.staff_id, []).append(iv)
    by_service: dict[UUID, list[av.Interval]] = {}
    if any(rule.service_id for rule in rules) and appts:
        ivs = {a.id: av.Interval(a.inicio, a.fin) for a in appts}
        for aid, sid in (await db.execute(select(AppointmentService.appointment_id, AppointmentService.service_id).where(
                AppointmentService.tenant_id == tid, AppointmentService.appointment_id.in_(list(ivs))))).all():
            by_service.setdefault(sid, []).append(ivs[aid])
    for rule in rules:
        scope = [av.Interval(a.inicio, a.fin) for a in appts] if rule.service_id is None else by_service.get(rule.service_id, [])
        ld.caps.append(av.Cap(rule.cupos_simultaneos, scope))
    return ld


def _lead(ctx: Ctx, ignore_lead: bool) -> tuple[timedelta, timedelta]:
    return (timedelta(0), timedelta(days=3650)) if ignore_lead else (timedelta(hours=ctx.min_horas), timedelta(days=ctx.max_dias))


def _duration(services: list[Service], duracion_min: int | None) -> int:
    d = sum(s.duracion_min for s in services) if services else duracion_min
    if not d:
        raise AppError("VALIDATION_ERROR", "Indica servicios o duracion_min")
    return d


async def availability(db: AsyncSession, tenant_id: UUID, *, desde: date, hasta: date, service_ids: list[UUID],
                       staff_id: UUID | None, duracion_min: int | None, ignore_lead: bool = False,
                       now: datetime | None = None, ctx: Ctx | None = None) -> AvailabilityOut:
    ctx = ctx or await get_ctx(db, tenant_id)
    if hasta < desde or (hasta - desde).days >= MAX_RANGE_DAYS:
        raise AppError("VALIDATION_ERROR", f"El rango debe ser de 1 a {MAX_RANGE_DAYS} días")
    if staff_id:
        await get_or_404(db, StaffProfile, staff_id, tenant_id, "Profesional")
    services = await services_by_ids(db, tenant_id, service_ids) if service_ids else []
    duration = _duration(services, duracion_min)
    tz = ctx.tz
    utc_from = datetime.combine(desde, time.min, tzinfo=tz).astimezone(UTC)
    utc_to = datetime.combine(hasta + timedelta(days=1), time.min, tzinfo=tz).astimezone(UTC)
    ld = await _load(db, tenant_id, utc_from, utc_to, service_ids, staff_id, duration)
    mn, mx = _lead(ctx, ignore_lead)
    now = now or datetime.now(UTC)
    dias: list[DaySlots] = []
    for i in range((hasta - desde).days + 1):
        d = desde + timedelta(days=i)
        sl = av.compute_slots(day=d, tz=tz, duration_min=duration, step_min=ld.step, buffer_min=ctx.buffer_min, now=now,
                              min_ahead=mn, max_ahead=mx, staff=ld.staff_day(d), unassigned=ld.unassigned, caps=ld.caps)
        dias.append(DaySlots(fecha=d, slots=[SlotOut(inicio=s.inicio.astimezone(tz), fin=s.fin.astimezone(tz),
                                                     staff_ids=list(s.staff_ids)) for s in sl]))
    return AvailabilityOut(zona_horaria=ctx.tz_name, duracion_min=duration, dias=dias)


async def free_staff_at(db: AsyncSession, tenant_id: UUID, ctx: Ctx, inicio: datetime, duration: int,
                        service_ids: list[UUID], staff_id: UUID | None, *, ignore_lead: bool,
                        exclude: UUID | None = None) -> list[UUID]:
    """Profesionales que pueden tomar ESTA hora exacta (usado al crear/validar citas)."""
    fin = inicio + timedelta(minutes=duration)
    if not ignore_lead:
        now = datetime.now(UTC)
        mn, mx = _lead(ctx, False)
        if inicio < now + mn or inicio > now + mx:
            return []
    ld = await _load(db, tenant_id, inicio - timedelta(days=1), fin + timedelta(days=1), service_ids, staff_id, duration,
                     exclude)
    return av.free_staff_exact(tz=ctx.tz, start=inicio, end=fin, buffer_min=ctx.buffer_min, staff=ld.staff_day(
        inicio.astimezone(ctx.tz).date()), unassigned=ld.unassigned, caps=ld.caps)


async def _citas_del_dia(db: AsyncSession, tid: UUID, ctx: Ctx, inicio: datetime) -> dict[UUID, int]:
    d = inicio.astimezone(ctx.tz).date()
    a = datetime.combine(d, time.min, tzinfo=ctx.tz).astimezone(UTC)
    rows = await db.execute(select(Appointment.staff_id, func.count()).where(
        Appointment.tenant_id == tid, Appointment.deleted_at.is_(None), Appointment.staff_id.is_not(None),
        Appointment.estado.in_(av.ESTADOS_QUE_OCUPAN), Appointment.inicio >= a, Appointment.inicio < a + timedelta(days=1))
        .group_by(Appointment.staff_id))
    return {sid: n for sid, n in rows if sid is not None}


# ------------------------------------------------------------------ salida
async def _outs(db: AsyncSession, tid: UUID, appts: list[Appointment], tz: ZoneInfo) -> list[AppointmentOut]:
    if not appts:
        return []
    ids = [a.id for a in appts]
    cust = {c.id: c for c in (await db.execute(select(Customer).where(
        Customer.tenant_id == tid, Customer.id.in_({a.customer_id for a in appts})))).scalars()}
    staff = {s.id: s for s in (await db.execute(select(StaffProfile).where(
        StaffProfile.tenant_id == tid, StaffProfile.id.in_({a.staff_id for a in appts if a.staff_id})))).scalars()}
    lines: dict[UUID, list[ServiceLine]] = {}
    for aid, sid, nombre, precio, dur in (await db.execute(
            select(AppointmentService.appointment_id, AppointmentService.service_id, Service.nombre,
                   AppointmentService.precio_pactado, AppointmentService.duracion_min)
            .join(Service, Service.id == AppointmentService.service_id)
            .where(AppointmentService.tenant_id == tid, AppointmentService.appointment_id.in_(ids)))).all():
        lines.setdefault(aid, []).append(ServiceLine(service_id=sid, nombre=nombre, precio_pactado=precio, duracion_min=dur))
    out = []
    for a in appts:
        c, s = cust[a.customer_id], staff.get(a.staff_id) if a.staff_id else None
        sv = lines.get(a.id, [])
        out.append(AppointmentOut(
            id=a.id, cliente=CustomerBrief(id=c.id, nombre=c.nombre, telefono=c.telefono),
            profesional=StaffBrief(id=s.id, nombre_publico=s.nombre_publico, color_calendario=s.color_calendario) if s else None,
            inicio=a.inicio.astimezone(tz), fin=a.fin.astimezone(tz), estado=a.estado, origen=a.origen, notas=a.notas,
            servicios=sv, total=sum((x.precio_pactado for x in sv), start=0)))
    return out


# ------------------------------------------------------------------ permisos por rol
async def _my_staff_id(db: AsyncSession, p: Principal) -> UUID | None:
    return (await db.execute(select(StaffProfile.id).where(
        StaffProfile.tenant_id == p.tid, StaffProfile.user_id == p.id, StaffProfile.deleted_at.is_(None)))).scalar_one_or_none()


def _sees_all(p: Principal) -> bool:
    return "appointments:read_all" in permissions_for(p.rol or "")


async def _get_visible(db: AsyncSession, p: Principal, aid: UUID) -> Appointment:
    a = await get_or_404(db, Appointment, aid, p.tid, "Cita")
    if not _sees_all(p) and a.staff_id != await _my_staff_id(db, p):
        raise AppError("NOT_FOUND", "Cita no encontrada")          # no revelar que existe
    return a


async def get_appointment(db: AsyncSession, p: Principal, aid: UUID) -> AppointmentOut:
    ctx = await get_ctx(db, p.tid)
    return (await _outs(db, p.tid, [await _get_visible(db, p, aid)], ctx.tz))[0]


async def list_appointments(db: AsyncSession, p: Principal, pp: PageParams, desde: datetime | None, hasta: datetime | None,
                            staff_id: UUID | None, estado: str | None, customer_id: UUID | None) -> Page[AppointmentOut]:
    ctx = await get_ctx(db, p.tid)
    stmt = scoped(Appointment, p.tid)
    if not _sees_all(p):
        mine = await _my_staff_id(db, p)
        if mine is None:
            return Page[AppointmentOut](items=[], page=pp.page, page_size=pp.page_size, total=0)
        staff_id = mine                                              # el rol profesional solo ve lo suyo
    if staff_id:
        stmt = stmt.where(Appointment.staff_id == staff_id)
    if estado:
        stmt = stmt.where(Appointment.estado == estado)
    if customer_id:
        stmt = stmt.where(Appointment.customer_id == customer_id)
    if desde:
        stmt = stmt.where(Appointment.fin > desde)
    if hasta:
        stmt = stmt.where(Appointment.inicio < hasta)
    rows, total = await paginate(db, stmt, pp, {"inicio": Appointment.inicio, "created_at": Appointment.created_at}, "inicio")
    return Page[AppointmentOut](items=await _outs(db, p.tid, rows, ctx.tz), page=pp.page, page_size=pp.page_size, total=total)


def range_for(vista: Vista, fecha: date) -> tuple[date, date]:
    """[desde, hasta) en fechas locales."""
    if vista == "dia":
        return fecha, fecha + timedelta(days=1)
    if vista == "semana":
        lunes = fecha - timedelta(days=fecha.weekday())
        return lunes, lunes + timedelta(days=7)
    primero = fecha.replace(day=1)
    siguiente = (primero + timedelta(days=32)).replace(day=1)
    return primero, siguiente


async def calendar(db: AsyncSession, p: Principal, vista: Vista, fecha: date, staff_id: UUID | None) -> CalendarOut:
    ctx = await get_ctx(db, p.tid)
    if not _sees_all(p):
        staff_id = await _my_staff_id(db, p)
        if staff_id is None:
            d0, d1 = range_for(vista, fecha)
            a0, a1 = (datetime.combine(d, time.min, tzinfo=ctx.tz) for d in (d0, d1))
            return CalendarOut(vista=vista, desde=a0, hasta=a1, zona_horaria=ctx.tz_name, citas=[], bloqueos=[])
    d0, d1 = range_for(vista, fecha)
    u0, u1 = (datetime.combine(d, time.min, tzinfo=ctx.tz).astimezone(UTC) for d in (d0, d1))
    q = scoped(Appointment, p.tid).where(Appointment.inicio < u1, Appointment.fin > u0)
    bq = select(ScheduleBlock).where(ScheduleBlock.tenant_id == p.tid, ScheduleBlock.inicio < u1, ScheduleBlock.fin > u0)
    if staff_id:
        q, bq = q.where(Appointment.staff_id == staff_id), bq.where(ScheduleBlock.staff_id == staff_id)
    appts = list((await db.execute(q.order_by(Appointment.inicio).limit(2000))).scalars())
    blocks = (await db.execute(bq.order_by(ScheduleBlock.inicio))).scalars()
    return CalendarOut(
        vista=vista, desde=u0.astimezone(ctx.tz), hasta=u1.astimezone(ctx.tz), zona_horaria=ctx.tz_name,
        citas=await _outs(db, p.tid, appts, ctx.tz),
        bloqueos=[BlockBrief(id=b.id, staff_id=b.staff_id, inicio=b.inicio.astimezone(ctx.tz), fin=b.fin.astimezone(ctx.tz),
                             motivo=b.motivo) for b in blocks])


# ------------------------------------------------------------------ crear
async def _staff_for(db: AsyncSession, tid: UUID, staff_id: UUID, service_ids: list[UUID]) -> StaffProfile:
    s = await get_or_404(db, StaffProfile, staff_id, tid, "Profesional")
    if not s.activo:
        raise AppError("VALIDATION_ERROR", "El profesional está inactivo")
    if service_ids and staff_id not in await _qualified_staff(db, tid, service_ids, staff_id):
        raise AppError("STAFF_NOT_QUALIFIED", "El profesional no realiza todos los servicios elegidos")
    return s


async def _insert(db: AsyncSession, p: Principal, d: AppointmentIn, staff_id: UUID | None, inicio: datetime, fin: datetime,
                  services: list[Service], origen: str) -> Appointment | None:
    """None = el profesional ya está ocupado a esa hora (constraint de exclusión). Savepoint: no aborta la transacción."""
    a = Appointment(tenant_id=p.tid, customer_id=d.customer_id, staff_id=staff_id, inicio=inicio, fin=fin,
                    estado="pendiente", origen=origen, notas=d.notas,
                    created_by=None if p.kind == "public" else p.id)
    try:
        async with db.begin_nested():
            db.add(a)
            await db.flush()
    except DBAPIError as e:
        if is_slot_conflict(e):
            return None
        raise
    for s in services:
        db.add(AppointmentService(tenant_id=p.tid, appointment_id=a.id, service_id=s.id,
                                  precio_pactado=s.precio, duracion_min=s.duracion_min))
    await db.flush()
    return a


async def create_appointment(db: AsyncSession, p: Principal, d: AppointmentIn, *, strict: bool = False,
                             origen: str = "recepcion") -> AppointmentOut:
    """strict=True (reservas públicas/WhatsApp/IA): exige que la hora esté realmente disponible
    (horario, bloqueos, buffer, cupos, anticipación). Recepción (strict=False) puede agendar fuera de horario,
    pero la BD siempre impide el doble agendamiento de un mismo profesional."""
    tid = p.tid
    ctx = await get_ctx(db, tid)
    await get_or_404(db, Customer, d.customer_id, tid, "Cliente")
    services = await services_by_ids(db, tid, d.service_ids) if d.service_ids else []
    duration = _duration(services, d.duracion_min)
    inicio = d.inicio.astimezone(UTC)
    fin = inicio + timedelta(minutes=duration)

    candidates: list[UUID | None]
    if d.staff_id:
        await _staff_for(db, tid, d.staff_id, d.service_ids)
        candidates = [d.staff_id]
        if strict and not await free_staff_at(db, tid, ctx, inicio, duration, d.service_ids, d.staff_id, ignore_lead=False):
            raise AppError("SLOT_UNAVAILABLE", "Esa hora ya no está disponible")
    elif d.asignar_automatico or (strict and ctx.modo == "automatica"):
        libres = await free_staff_at(db, tid, ctx, inicio, duration, d.service_ids, None, ignore_lead=not strict)
        if not libres:
            raise AppError("SLOT_UNAVAILABLE", "No hay profesionales disponibles a esa hora")
        candidates = list(av.rank_staff(libres, await _citas_del_dia(db, tid, ctx, inicio)))    # menos citas primero
    else:
        candidates = [None]                                      # "en recepción": se asigna después
        if strict and not await free_staff_at(db, tid, ctx, inicio, duration, d.service_ids, None, ignore_lead=False):
            raise AppError("SLOT_UNAVAILABLE", "Esa hora ya no está disponible")

    appt = None
    for sid in candidates:                                       # si otro request gana la carrera, se prueba el siguiente
        appt = await _insert(db, p, d, sid, inicio, fin, services, origen)
        if appt:
            break
    if appt is None:
        raise AppError("SLOT_TAKEN", "Ese horario acaba de ser ocupado. Elige otro.", status=409)
    _aud(db, p, "cita_creada", appt.id, despues={"inicio": inicio.isoformat(), "staff_id": str(appt.staff_id) if appt.staff_id else None,
                                                  "origen": origen})
    return (await _outs(db, tid, [appt], ctx.tz))[0]


# ------------------------------------------------------------------ cambios
async def patch_appointment(db: AsyncSession, p: Principal, aid: UUID, d: AppointmentPatch) -> AppointmentOut:
    a = await get_or_404(db, Appointment, aid, p.tid, "Cita")
    ch = d.model_dump(exclude_unset=True)
    if ch.get("customer_id"):
        await get_or_404(db, Customer, ch["customer_id"], p.tid, "Cliente")
    for k, v in ch.items():
        setattr(a, k, v)
    await db.flush()
    _aud(db, p, "cita_actualizada", a.id, despues={k: str(v) for k, v in ch.items()})
    ctx = await get_ctx(db, p.tid)
    return (await _outs(db, p.tid, [a], ctx.tz))[0]


async def change_status(db: AsyncSession, p: Principal, aid: UUID, d: StatusIn) -> AppointmentOut:
    a = await _get_visible(db, p, aid)
    perms = permissions_for(p.rol or "")
    if "appointments:write" not in perms and d.estado not in ("en_atencion", "atendida"):
        raise AppError("FORBIDDEN", "Solo puedes iniciar o finalizar la atención de tus citas")
    if not av.can_transition(a.estado, d.estado):
        raise AppError("INVALID_STATE_TRANSITION", f"No se puede pasar de {a.estado} a {d.estado}",
                       {"actual": a.estado, "permitidos": sorted(av.TRANSITIONS[a.estado])})
    if d.estado == "no_asistio" and datetime.now(UTC) < a.inicio:
        raise AppError("INVALID_STATE_TRANSITION", "No se puede marcar inasistencia antes de la hora de la cita")
    antes = a.estado
    a.estado = d.estado
    if d.motivo and d.estado == "cancelada":
        a.notas = f"{a.notas}\n[Cancelada] {d.motivo}" if a.notas else f"[Cancelada] {d.motivo}"
    await db.flush()
    _aud(db, p, "cita_estado", a.id, {"estado": antes}, {"estado": d.estado, "motivo": d.motivo})
    ctx = await get_ctx(db, p.tid)
    return (await _outs(db, p.tid, [a], ctx.tz))[0]


async def move_appointment(db: AsyncSession, p: Principal, aid: UUID, d: MoveIn) -> AppointmentOut:
    a = await get_or_404(db, Appointment, aid, p.tid, "Cita")
    if a.estado not in ("pendiente", "confirmada"):
        raise AppError("INVALID_STATE_TRANSITION", "Solo se pueden mover citas pendientes o confirmadas")
    if d.staff_id and d.staff_id != a.staff_id:
        svc_ids = list((await db.execute(select(AppointmentService.service_id).where(
            AppointmentService.tenant_id == p.tid, AppointmentService.appointment_id == a.id))).scalars())
        await _staff_for(db, p.tid, d.staff_id, svc_ids)
    antes = {"inicio": a.inicio.isoformat(), "staff_id": str(a.staff_id) if a.staff_id else None}
    dur = a.fin - a.inicio
    a.inicio = d.inicio.astimezone(UTC)
    a.fin = a.inicio + dur
    if d.staff_id:
        a.staff_id = d.staff_id
    try:
        async with db.begin_nested():
            await db.flush()
    except DBAPIError as e:
        if is_slot_conflict(e):
            raise AppError("SLOT_TAKEN", "Ese horario ya está ocupado para el profesional", status=409) from e
        raise
    _aud(db, p, "cita_movida", a.id, antes, {"inicio": a.inicio.isoformat(), "staff_id": str(a.staff_id) if a.staff_id else None})
    ctx = await get_ctx(db, p.tid)
    return (await _outs(db, p.tid, [a], ctx.tz))[0]


async def stats(db: AsyncSession, p: Principal, desde: datetime, hasta: datetime) -> StatsOut:
    q = (select(Appointment.estado, func.count()).where(Appointment.tenant_id == p.tid, Appointment.deleted_at.is_(None),
                                                         Appointment.inicio >= desde, Appointment.inicio < hasta)
         .group_by(Appointment.estado))
    if not _sees_all(p):
        q = q.where(Appointment.staff_id == await _my_staff_id(db, p))
    por = {e: n for e, n in (await db.execute(q))}
    return StatsOut(total=sum(por.values()), por_estado=por)
