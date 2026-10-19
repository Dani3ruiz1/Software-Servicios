from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.pagination import Page, PageParams
from app.core.repository import get_or_404, paginate, scoped
from app.core.security import hash_password
from app.core.tenancy import Principal
from app.modules.audit import service as audit
from app.modules.auth.models import RefreshToken
from app.modules.staff.models import StaffProfile
from app.modules.users.models import User
from app.modules.users.schemas import SeatsOut, UserIn, UserOut, UserPatch


def _aud(db: AsyncSession, p: Principal, accion: str, u: User, antes: dict | None = None,  # type: ignore[type-arg]
         despues: dict | None = None) -> None:  # type: ignore[type-arg]
    audit.record(db, actor_tipo="user", actor_id=p.id, tenant_id=p.tid, accion=accion, entidad="user",
                 entidad_id=u.id, antes=antes, despues=despues)


def seats_available(usados: int, maximo: int) -> bool:
    return usados < maximo


async def _plan_limit(db: AsyncSession, tenant_id: UUID) -> tuple[int, str]:
    row = (await db.execute(text(
        "SELECT p.max_usuarios, p.nombre FROM tenant_subscriptions s JOIN plans p ON p.id = s.plan_id "
        "WHERE s.tenant_id = :t AND s.estado = 'vigente'"), {"t": tenant_id})).one_or_none()
    if row is None:
        raise AppError("TENANT_BLOCKED", "El negocio no tiene un plan vigente")
    return row.max_usuarios, row.nombre


async def _used(db: AsyncSession, tenant_id: UUID) -> int:
    return (await db.execute(select(func.count()).select_from(User).where(
        User.tenant_id == tenant_id, User.deleted_at.is_(None), User.activo.is_(True)))).scalar_one()


async def _lock_seats(db: AsyncSession, tenant_id: UUID) -> None:
    """Serializa altas/reactivaciones del mismo tenant: sin esto, dos requests simultáneos pasan el límite."""
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"seats:{tenant_id}"})


async def seats(db: AsyncSession, p: Principal) -> SeatsOut:
    maximo, plan = await _plan_limit(db, p.tid)
    usados = await _used(db, p.tid)
    return SeatsOut(usados=usados, maximo=maximo, disponibles=max(maximo - usados, 0), plan=plan)


async def _out(db: AsyncSession, u: User) -> UserOut:
    sid = (await db.execute(select(StaffProfile.id).where(StaffProfile.tenant_id == u.tenant_id,
                                                           StaffProfile.user_id == u.id,
                                                           StaffProfile.deleted_at.is_(None)))).scalar_one_or_none()
    return UserOut.model_validate(u).model_copy(update={"staff_id": sid})


async def list_users(db: AsyncSession, p: Principal, pp: PageParams, rol: str | None, activo: bool | None) -> Page[UserOut]:
    stmt = scoped(User, p.tid)
    if rol:
        stmt = stmt.where(User.rol == rol)
    if activo is not None:
        stmt = stmt.where(User.activo.is_(activo))
    rows, total = await paginate(db, stmt, pp, {"nombre": User.nombre, "email": User.email, "created_at": User.created_at}, "nombre")
    return Page[UserOut](items=[await _out(db, u) for u in rows], page=pp.page, page_size=pp.page_size, total=total)


async def create_user(db: AsyncSession, p: Principal, d: UserIn) -> UserOut:
    await _lock_seats(db, p.tid)
    maximo, plan = await _plan_limit(db, p.tid)
    usados = await _used(db, p.tid)
    if not seats_available(usados, maximo):
        raise AppError("USER_LIMIT_REACHED", f"Tu plan {plan} permite {maximo} usuario(s). Mejora tu plan para agregar más.",
                       {"usados": usados, "maximo": maximo, "plan": plan})
    u = User(tenant_id=p.tid, email=d.email, password_hash=hash_password(d.password), nombre=d.nombre, rol=d.rol)
    db.add(u)
    try:
        await db.flush()
    except IntegrityError as e:
        raise AppError("CONFLICT", "Ya existe un usuario con ese correo") from e
    if d.rol == "profesional" and d.crear_perfil_profesional is not False:
        db.add(StaffProfile(tenant_id=p.tid, user_id=u.id, nombre_publico=d.nombre))
        await db.flush()
    _aud(db, p, "usuario_creado", u, despues={"email": d.email, "rol": d.rol})
    return await _out(db, u)


async def patch_user(db: AsyncSession, p: Principal, uid: UUID, d: UserPatch) -> UserOut:
    u = await get_or_404(db, User, uid, p.tid, "Usuario")
    ch = d.model_dump(exclude_unset=True)
    if ch.get("activo") is True and not u.activo:
        await _lock_seats(db, p.tid)
        maximo, plan = await _plan_limit(db, p.tid)
        usados = await _used(db, p.tid)
        if not seats_available(usados, maximo):
            raise AppError("USER_LIMIT_REACHED", f"Tu plan {plan} permite {maximo} usuario(s).",
                           {"usados": usados, "maximo": maximo, "plan": plan})
    quita_admin = u.rol == "admin" and (ch.get("rol", "admin") != "admin" or ch.get("activo") is False)
    if quita_admin:
        await _ensure_other_admin(db, p, u)
    antes = {"rol": u.rol, "activo": u.activo}
    pwd = ch.pop("password", None)
    for k, v in ch.items():
        setattr(u, k, v)
    if pwd:
        u.password_hash, u.intentos_fallidos, u.bloqueado_hasta = hash_password(pwd), 0, None
        await _revoke_sessions(db, u.id)
    if ch.get("activo") is False:
        await _revoke_sessions(db, u.id)
    await db.flush()
    _aud(db, p, "usuario_actualizado", u, antes, {**ch, **({"password": "***"} if pwd else {})})
    return await _out(db, u)


async def delete_user(db: AsyncSession, p: Principal, uid: UUID) -> None:
    u = await get_or_404(db, User, uid, p.tid, "Usuario")
    if u.id == p.id:
        raise AppError("CONFLICT", "No puedes eliminar tu propio usuario")
    if u.rol == "admin":
        await _ensure_other_admin(db, p, u)
    u.deleted_at, u.activo = datetime.now(UTC), False     # libera el cupo del plan
    await _revoke_sessions(db, u.id)
    _aud(db, p, "usuario_eliminado", u)


async def _ensure_other_admin(db: AsyncSession, p: Principal, u: User) -> None:
    otros = (await db.execute(select(func.count()).select_from(User).where(
        User.tenant_id == p.tid, User.rol == "admin", User.activo.is_(True),
        User.deleted_at.is_(None), User.id != u.id))).scalar_one()
    if otros == 0:
        raise AppError("CONFLICT", "El negocio debe conservar al menos un administrador activo")


async def _revoke_sessions(db: AsyncSession, uid: UUID) -> None:
    await db.execute(update(RefreshToken).where(RefreshToken.sujeto_id == uid, RefreshToken.revocado_en.is_(None))
                     .values(revocado_en=datetime.now(UTC)))
