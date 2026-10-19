from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.repository import get_or_404, scoped
from app.core.tenancy import Principal
from app.modules.appointments.models import Appointment
from app.modules.audit import service as audit
from app.modules.catalog.models import Service
from app.modules.staff.models import ScheduleBlock, StaffProfile, StaffSchedule, StaffService
from app.modules.staff.schemas import (
    AccessIn,
    AccessOut,
    BlockIn,
    BlockOut,
    ScheduleIn,
    ScheduleItem,
    StaffIn,
    StaffOut,
    StaffPatch,
)
from app.modules.users import service as users
from app.modules.users.models import User
from app.modules.users.schemas import UserIn


def _aud(db: AsyncSession, p: Principal, accion: str, sid: UUID, despues: dict | None = None) -> None:  # type: ignore[type-arg]
    audit.record(db, actor_tipo="user", actor_id=p.id, tenant_id=p.tid, accion=accion,
                 entidad="staff_profile", entidad_id=sid, despues=despues)


async def _out(db: AsyncSession, s: StaffProfile) -> StaffOut:
    ids = (await db.execute(select(StaffService.service_id).where(
        StaffService.tenant_id == s.tenant_id, StaffService.staff_id == s.id))).scalars().all()
    return StaffOut.model_validate(s).model_copy(update={"service_ids": list(ids)})


async def list_staff(db: AsyncSession, p: Principal, solo_activos: bool) -> list[StaffOut]:
    q = scoped(StaffProfile, p.tid).order_by(StaffProfile.nombre_publico)
    if solo_activos:
        q = q.where(StaffProfile.activo.is_(True))
    return [await _out(db, s) for s in (await db.execute(q)).scalars()]


async def get_staff(db: AsyncSession, p: Principal, sid: UUID) -> StaffOut:
    return await _out(db, await get_or_404(db, StaffProfile, sid, p.tid, "Profesional"))


async def _check_services(db: AsyncSession, tenant_id: UUID, ids: list[UUID]) -> None:
    n = (await db.execute(select(func.count()).select_from(Service).where(
        Service.tenant_id == tenant_id, Service.deleted_at.is_(None), Service.id.in_(set(ids))))).scalar_one()
    if n != len(set(ids)):
        raise AppError("NOT_FOUND", "Algún servicio no existe")


async def _replace_services(db: AsyncSession, tenant_id: UUID, staff_id: UUID, ids: list[UUID]) -> None:
    await _check_services(db, tenant_id, ids)
    await db.execute(delete(StaffService).where(StaffService.tenant_id == tenant_id, StaffService.staff_id == staff_id))
    for sv in set(ids):
        db.add(StaffService(tenant_id=tenant_id, staff_id=staff_id, service_id=sv))
    await db.flush()


async def create_staff(db: AsyncSession, p: Principal, d: StaffIn) -> StaffOut:
    if d.user_id:
        await get_or_404(db, User, d.user_id, p.tid, "Usuario")
        if (await db.execute(scoped(StaffProfile, p.tid).where(StaffProfile.user_id == d.user_id))).first():
            raise AppError("CONFLICT", "Ese usuario ya tiene un perfil de profesional")
    s = StaffProfile(tenant_id=p.tid, **d.model_dump(exclude={"service_ids"}))
    db.add(s)
    await db.flush()
    if d.service_ids:
        await _replace_services(db, p.tid, s.id, d.service_ids)
    _aud(db, p, "profesional_creado", s.id, {"nombre": s.nombre_publico})
    return await _out(db, s)


async def grant_access(db: AsyncSession, p: Principal, sid: UUID, d: AccessIn) -> AccessOut:
    """Crea el usuario (rol profesional, respeta el límite de usuarios del plan) y lo enlaza a este perfil de agenda."""
    s = await get_or_404(db, StaffProfile, sid, p.tid, "Profesional")
    if s.user_id:
        raise AppError("CONFLICT", "Esta persona ya tiene acceso")
    u = await users.create_user(db, p, UserIn(nombre=s.nombre_publico, email=d.email, password=d.password, rol="profesional",
                                              crear_perfil_profesional=False))
    s.user_id = u.id
    await db.flush()
    _aud(db, p, "acceso_profesional_creado", s.id, {"email": d.email})
    return AccessOut(usuario_id=u.id, email=u.email)


async def revoke_access(db: AsyncSession, p: Principal, sid: UUID) -> None:
    """Quita el acceso: el usuario se elimina (libera el cupo, cierra sus sesiones) y el perfil de agenda se conserva con sus citas."""
    s = await get_or_404(db, StaffProfile, sid, p.tid, "Profesional")
    if not s.user_id:
        raise AppError("NOT_FOUND", "Esta persona no tiene acceso")
    uid, s.user_id = s.user_id, None
    await db.flush()
    await users.delete_user(db, p, uid)
    _aud(db, p, "acceso_profesional_revocado", s.id)


async def patch_staff(db: AsyncSession, p: Principal, sid: UUID, d: StaffPatch) -> StaffOut:
    s = await get_or_404(db, StaffProfile, sid, p.tid, "Profesional")
    for k, v in d.model_dump(exclude_unset=True).items():
        setattr(s, k, v)
    await db.flush()
    _aud(db, p, "profesional_actualizado", s.id, d.model_dump(exclude_unset=True))
    return await _out(db, s)


async def delete_staff(db: AsyncSession, p: Principal, sid: UUID) -> None:
    s = await get_or_404(db, StaffProfile, sid, p.tid, "Profesional")
    futuras = (await db.execute(select(func.count()).select_from(Appointment).where(
        Appointment.tenant_id == p.tid, Appointment.staff_id == sid, Appointment.deleted_at.is_(None),
        Appointment.inicio >= datetime.now(UTC), Appointment.estado.in_(("pendiente", "confirmada"))))).scalar_one()
    if futuras:
        raise AppError("CONFLICT", "El profesional tiene citas futuras; reasígnalas o cancélalas antes",
                       {"citas_futuras": futuras})
    s.deleted_at, s.activo = datetime.now(UTC), False
    _aud(db, p, "profesional_eliminado", s.id)


async def set_services(db: AsyncSession, p: Principal, sid: UUID, ids: list[UUID]) -> StaffOut:
    s = await get_or_404(db, StaffProfile, sid, p.tid, "Profesional")
    await _replace_services(db, p.tid, sid, ids)
    _aud(db, p, "profesional_servicios", s.id, {"servicios": [str(i) for i in ids]})
    return await _out(db, s)


# ---- horario semanal
async def get_schedule(db: AsyncSession, p: Principal, sid: UUID) -> list[ScheduleItem]:
    await get_or_404(db, StaffProfile, sid, p.tid, "Profesional")
    rows = (await db.execute(select(StaffSchedule).where(StaffSchedule.tenant_id == p.tid, StaffSchedule.staff_id == sid)
                             .order_by(StaffSchedule.dia_semana, StaffSchedule.hora_inicio))).scalars()
    return [ScheduleItem(dia_semana=r.dia_semana, hora_inicio=r.hora_inicio, hora_fin=r.hora_fin) for r in rows]


async def set_schedule(db: AsyncSession, p: Principal, sid: UUID, d: ScheduleIn) -> list[ScheduleItem]:
    await get_or_404(db, StaffProfile, sid, p.tid, "Profesional")
    await db.execute(delete(StaffSchedule).where(StaffSchedule.tenant_id == p.tid, StaffSchedule.staff_id == sid))
    for t in d.tramos:
        db.add(StaffSchedule(tenant_id=p.tid, staff_id=sid, dia_semana=t.dia_semana,
                             hora_inicio=t.hora_inicio, hora_fin=t.hora_fin))
    await db.flush()
    _aud(db, p, "horario_actualizado", sid, {"tramos": len(d.tramos)})
    return await get_schedule(db, p, sid)


# ---- bloqueos (almuerzo, vacaciones, festivos)
async def _affected(db: AsyncSession, tenant_id: UUID, sid: UUID, ini: datetime, fin: datetime) -> int:
    return (await db.execute(select(func.count()).select_from(Appointment).where(
        Appointment.tenant_id == tenant_id, Appointment.staff_id == sid, Appointment.deleted_at.is_(None),
        Appointment.estado.in_(("pendiente", "confirmada", "en_atencion")),
        Appointment.inicio < fin, Appointment.fin > ini))).scalar_one()


async def list_blocks(db: AsyncSession, p: Principal, sid: UUID, desde: datetime | None, hasta: datetime | None) -> list[BlockOut]:
    await get_or_404(db, StaffProfile, sid, p.tid, "Profesional")
    q = select(ScheduleBlock).where(ScheduleBlock.tenant_id == p.tid, ScheduleBlock.staff_id == sid)
    if desde:
        q = q.where(ScheduleBlock.fin > desde)
    if hasta:
        q = q.where(ScheduleBlock.inicio < hasta)
    return [BlockOut.model_validate(b) for b in (await db.execute(q.order_by(ScheduleBlock.inicio))).scalars()]


async def create_block(db: AsyncSession, p: Principal, sid: UUID, d: BlockIn) -> BlockOut:
    await get_or_404(db, StaffProfile, sid, p.tid, "Profesional")
    b = ScheduleBlock(tenant_id=p.tid, staff_id=sid, **d.model_dump())
    db.add(b)
    await db.flush()
    _aud(db, p, "bloqueo_creado", sid, {"inicio": d.inicio.isoformat(), "fin": d.fin.isoformat(), "motivo": d.motivo})
    # No cancela citas: avisa cuántas quedan en conflicto para que recepción las reprograme.
    return BlockOut.model_validate(b).model_copy(update={"citas_afectadas": await _affected(db, p.tid, sid, d.inicio, d.fin)})


async def delete_block(db: AsyncSession, p: Principal, sid: UUID, bid: UUID) -> None:
    b = (await db.execute(select(ScheduleBlock).where(ScheduleBlock.tenant_id == p.tid, ScheduleBlock.staff_id == sid,
                                                       ScheduleBlock.id == bid))).scalar_one_or_none()
    if b is None:
        raise AppError("NOT_FOUND", "Bloqueo no encontrado")
    await db.delete(b)
    _aud(db, p, "bloqueo_eliminado", sid, {"bloqueo": str(bid)})
