"""Lógica de autenticación. Los routers solo llaman a estas funciones.

Patrón de fallos: los contadores de intentos y la auditoría deben PERSISTIR aunque el login falle,
así que el error se calcula dentro de la transacción y se lanza DESPUÉS de hacer commit.
"""
import logging
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pyotp
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import scoped_session
from app.core.errors import AppError
from app.core.rbac import permissions_for
from app.core.security import (
    DUMMY_HASH,
    create_access_token,
    encrypt_secret,
    hash_password,
    hash_token,
    new_opaque_token,
    verify_password,
    verify_totp,
)
from app.core.tenancy import Principal, get_access_info
from app.modules.audit import service as audit
from app.modules.auth.models import PasswordResetToken, RefreshToken
from app.modules.auth.schemas import MeOut, TenantBrief, TokenOut
from app.modules.platform.models import Module, PlatformUser, Tenant, TenantModule
from app.modules.users.models import User

log = logging.getLogger("app.auth")
LOCK_MINUTES = 15
RESET_MINUTES = 60
Subject = User | PlatformUser


def _now() -> datetime:
    return datetime.now(UTC)


def _kind(s: Subject) -> str:
    return "user" if isinstance(s, User) else "platform_user"


def _tenant_id(s: Subject) -> UUID | None:
    return s.tenant_id if isinstance(s, User) else None


def _rol(s: Subject) -> str:
    return s.rol if isinstance(s, User) else "super_admin"


def _audit(db: AsyncSession, s: Subject | None, accion: str, ip: str | None,
           tenant_id: UUID | None = None, **extra: object) -> None:
    audit.record(db, actor_tipo=_kind(s) if s else "system", actor_id=s.id if s else None,
                 tenant_id=_tenant_id(s) if s else tenant_id, accion=accion,
                 entidad="user" if isinstance(s, User) else "platform_user",
                 entidad_id=s.id if s else None, despues=dict(extra) or None, ip=ip)


def _issue(db: AsyncSession, s: Subject, family: UUID | None = None) -> TokenOut:
    raw = new_opaque_token()
    db.add(RefreshToken(
        sujeto_tipo=_kind(s), sujeto_id=s.id, tenant_id=_tenant_id(s), token_hash=hash_token(raw),
        familia_id=family or uuid4(), expira_en=_now() + timedelta(days=get_settings().jwt_refresh_days)))
    access, ttl = create_access_token(sub=s.id, kind=_kind(s), tenant_id=_tenant_id(s), rol=_rol(s))
    return TokenOut(access_token=access, refresh_token=raw, expires_in=ttl)


def _check_credentials(s: Subject | None, password: str, totp_code: str | None) -> AppError | None:
    """Valida y actualiza contadores de bloqueo. None = credenciales correctas."""
    bad = AppError("INVALID_CREDENTIALS", "Correo o contraseña incorrectos")
    if s is None or not s.activo:
        verify_password(DUMMY_HASH, password)            # iguala el tiempo de respuesta
        return bad
    if s.bloqueado_hasta and s.bloqueado_hasta > _now():
        return AppError("ACCOUNT_LOCKED", "Cuenta bloqueada temporalmente por intentos fallidos",
                        {"bloqueado_hasta": s.bloqueado_hasta.isoformat()})
    ok = verify_password(s.password_hash, password)
    if ok and s.totp_habilitado and s.totp_secret:
        if not totp_code:
            return AppError("TOTP_REQUIRED", "Ingresa el código de verificación en dos pasos")
        ok = verify_totp(s.totp_secret, totp_code)
    if not ok:
        s.intentos_fallidos += 1
        if s.intentos_fallidos >= get_settings().login_max_attempts:
            s.bloqueado_hasta = _now() + timedelta(minutes=LOCK_MINUTES)
            s.intentos_fallidos = 0
        return bad
    s.intentos_fallidos, s.bloqueado_hasta, s.ultimo_login = 0, None, _now()
    return None


async def _login(email: str, password: str, totp_code: str | None, ip: str | None,
                 tenant_slug: str | None) -> TokenOut:
    err: AppError | None = None
    out: TokenOut | None = None
    async with scoped_session(platform=True) as db:       # el tenant aún no es conocido
        subject: Subject | None
        tenant_id: UUID | None = None
        if tenant_slug is None:
            subject = (await db.execute(select(PlatformUser).where(PlatformUser.email == email))).scalar_one_or_none()
        else:
            t = (await db.execute(select(Tenant).where(Tenant.slug == tenant_slug, Tenant.deleted_at.is_(None)))).scalar_one_or_none()
            tenant_id = t.id if t else None
            subject = None if t is None else (await db.execute(select(User).where(
                User.tenant_id == t.id, User.email == email, User.deleted_at.is_(None)))).scalar_one_or_none()
        err = _check_credentials(subject, password, totp_code)
        if err is None:
            assert subject is not None
            out = _issue(db, subject)
            _audit(db, subject, "login", ip)
        else:
            _audit(db, subject, "login_fallido", ip, tenant_id=tenant_id, codigo=err.code)
    if err:
        raise err
    assert out is not None
    return out


async def login(tenant_slug: str, email: str, password: str, totp_code: str | None, ip: str | None) -> TokenOut:
    return await _login(email, password, totp_code, ip, tenant_slug)


async def platform_login(email: str, password: str, totp_code: str | None, ip: str | None) -> TokenOut:
    return await _login(email, password, totp_code, ip, None)


async def _load(db: AsyncSession, kind: str, sid: UUID) -> Subject | None:
    model: type[User] | type[PlatformUser] = User if kind == "user" else PlatformUser
    s = cast(Subject | None, (await db.execute(select(model).where(model.id == sid))).scalar_one_or_none())
    if s is None or not s.activo or getattr(s, "deleted_at", None):
        return None
    return s


async def _revoke_family(db: AsyncSession, family: UUID) -> None:
    await db.execute(update(RefreshToken).where(RefreshToken.familia_id == family,
                     RefreshToken.revocado_en.is_(None)).values(revocado_en=_now()))


async def refresh(raw: str, ip: str | None) -> TokenOut:
    err: AppError | None = None
    out: TokenOut | None = None
    async with scoped_session(platform=True) as db:
        rt = (await db.execute(select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw))
                               .with_for_update())).scalar_one_or_none()
        if rt is None or rt.expira_en < _now():
            err = AppError("TOKEN_EXPIRED", "La sesión expiró, inicia sesión de nuevo")
        elif rt.revocado_en is not None:     # token ya usado: posible robo -> se mata toda la familia
            await _revoke_family(db, rt.familia_id)
            audit.record(db, actor_tipo=rt.sujeto_tipo, actor_id=rt.sujeto_id, tenant_id=rt.tenant_id,
                         accion="refresh_reutilizado", entidad="refresh_token", entidad_id=rt.id, ip=ip)
            err = AppError("REFRESH_REUSED", "Sesión inválida, inicia sesión de nuevo")
        else:
            s = await _load(db, rt.sujeto_tipo, rt.sujeto_id)
            if s is None:
                err = AppError("UNAUTHORIZED", "Cuenta inactiva")
            else:
                rt.revocado_en = _now()
                out = _issue(db, s, family=rt.familia_id)
    if err:
        raise err
    assert out is not None
    return out


async def logout(raw: str) -> None:
    async with scoped_session(platform=True) as db:
        rt = (await db.execute(select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw)))).scalar_one_or_none()
        if rt:
            await _revoke_family(db, rt.familia_id)
            audit.record(db, actor_tipo=rt.sujeto_tipo, actor_id=rt.sujeto_id, tenant_id=rt.tenant_id,
                         accion="logout", entidad="refresh_token", entidad_id=rt.id)


def _no_impersonation(p: Principal) -> None:
    if p.impersonator_id:
        raise AppError("FORBIDDEN", "No disponible durante una sesión de soporte")


async def change_password(db: AsyncSession, p: Principal, current: str, new: str, ip: str | None) -> None:
    _no_impersonation(p)
    s = await _load(db, p.kind, p.id)
    if s is None or not verify_password(s.password_hash, current):
        raise AppError("INVALID_CREDENTIALS", "La contraseña actual no es correcta")
    s.password_hash = hash_password(new)
    await db.execute(update(RefreshToken).where(RefreshToken.sujeto_id == s.id,
                     RefreshToken.revocado_en.is_(None)).values(revocado_en=_now()))   # cierra otras sesiones
    _audit(db, s, "cambio_password", ip)


async def forgot_password(tenant_slug: str | None, email: str) -> None:
    """Siempre responde igual (no revela si la cuenta existe)."""
    async with scoped_session(platform=True) as db:
        if tenant_slug is None:
            s: Subject | None = (await db.execute(select(PlatformUser).where(PlatformUser.email == email))).scalar_one_or_none()
        else:
            t = (await db.execute(select(Tenant).where(Tenant.slug == tenant_slug))).scalar_one_or_none()
            s = None if t is None else (await db.execute(select(User).where(
                User.tenant_id == t.id, User.email == email, User.deleted_at.is_(None)))).scalar_one_or_none()
        if s is None or not s.activo:
            return
        raw = new_opaque_token()
        db.add(PasswordResetToken(sujeto_tipo=_kind(s), sujeto_id=s.id, tenant_id=_tenant_id(s),
                                  token_hash=hash_token(raw), expira_en=_now() + timedelta(minutes=RESET_MINUTES)))
        # Fase de notificaciones: encolar correo. Hasta entonces solo se registra en desarrollo.
        if get_settings().environment == "development":
            log.info("reset_password_token", extra={"email": email, "token": raw})


async def reset_password(raw: str, new: str, ip: str | None) -> None:
    err: AppError | None = None
    async with scoped_session(platform=True) as db:
        t = (await db.execute(select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_token(raw))
                              .with_for_update())).scalar_one_or_none()
        if t is None or t.usado_en is not None or t.expira_en < _now():
            err = AppError("UNAUTHORIZED", "El enlace no es válido o ya expiró")
        else:
            s = await _load(db, t.sujeto_tipo, t.sujeto_id)
            if s is None:
                err = AppError("UNAUTHORIZED", "El enlace no es válido o ya expiró")
            else:
                s.password_hash, s.intentos_fallidos, s.bloqueado_hasta = hash_password(new), 0, None
                t.usado_en = _now()
                await db.execute(update(RefreshToken).where(RefreshToken.sujeto_id == s.id,
                                 RefreshToken.revocado_en.is_(None)).values(revocado_en=_now()))
                _audit(db, s, "reset_password", ip)
    if err:
        raise err


async def totp_setup(db: AsyncSession, p: Principal) -> tuple[str, str]:
    _no_impersonation(p)
    s = await _load(db, p.kind, p.id)
    if s is None or (isinstance(s, User) and s.rol != "admin"):
        raise AppError("FORBIDDEN", "El doble factor solo está disponible para administradores")
    secret = pyotp.random_base32()
    s.totp_secret, s.totp_habilitado = encrypt_secret(secret), False   # se activa al confirmar un código
    return secret, pyotp.TOTP(secret).provisioning_uri(name=s.email, issuer_name="Agenda")


async def totp_enable(db: AsyncSession, p: Principal, code: str, ip: str | None) -> None:
    _no_impersonation(p)
    s = await _load(db, p.kind, p.id)
    if s is None or not s.totp_secret or not verify_totp(s.totp_secret, code):
        raise AppError("INVALID_TOTP", "Código incorrecto")
    s.totp_habilitado = True
    _audit(db, s, "2fa_activado", ip)


async def me(db: AsyncSession, p: Principal) -> MeOut:
    s = await _load(db, p.kind, p.id)
    if s is None:
        raise AppError("UNAUTHORIZED", "Cuenta inactiva")
    tenant: TenantBrief | None = None
    modulos: list[str] = []
    if isinstance(s, User):
        t = (await db.execute(select(Tenant).where(Tenant.id == s.tenant_id))).scalar_one()
        a = await get_access_info(db, t.id)
        tenant = TenantBrief(id=t.id, nombre=t.nombre, slug=t.slug, tipo=t.business_type, zona_horaria=t.zona_horaria, modo=t.modo,
                             estado=t.estado, bloqueado=a.bloqueado, motivo_bloqueo=a.motivo, fecha_fin=a.fecha_fin)
        modulos = sorted((await db.execute(select(Module.codigo).join(TenantModule, TenantModule.module_id == Module.id)
                                           .where(TenantModule.tenant_id == t.id, TenantModule.activo.is_(True)))).scalars())
    return MeOut(id=s.id, email=s.email, nombre=s.nombre, rol=_rol(s), totp_habilitado=s.totp_habilitado,
                 tenant=tenant, modulos_activos=modulos, permisos=permissions_for(_rol(s)),
                 impersonado=p.impersonator_id is not None)
