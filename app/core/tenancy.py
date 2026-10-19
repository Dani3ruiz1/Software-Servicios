"""El tenant SIEMPRE sale del JWT firmado; ningún parámetro del cliente lo puede cambiar.

Cada request autenticado de un tenant verifica además su suscripción (regla 1) y, si el endpoint
lo pide, que el módulo esté activo (regla 3). La verificación lee la BD en cada request: un pago
registrado desbloquea al instante, sin esperar al job diario.
"""
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import scoped_session
from app.core.errors import AppError
from app.core.security import decode_access_token

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    id: UUID
    kind: str                  # 'user' | 'platform_user'
    tenant_id: UUID | None
    rol: str | None
    impersonator_id: UUID | None = None

    @property
    def tid(self) -> UUID:
        """El negocio del usuario. Las rutas de negocio lo usan SIEMPRE (nunca `tenant_id`, que es opcional para el Super Admin):
        si falta, es un error de uso claro y no un `None` silencioso metido en una consulta."""
        if self.tenant_id is None:
            raise AppError("FORBIDDEN", "Esta acción requiere un usuario de un negocio")
        return self.tenant_id


@dataclass(frozen=True)
class AccessInfo:
    bloqueado: bool
    motivo: str | None         # 'manual' | 'vencimiento' | 'sin_suscripcion'
    estado: str
    fecha_fin: date | None


async def get_principal(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> Principal:
    if creds is None:
        raise AppError("UNAUTHORIZED", "Falta el token de acceso")
    c = decode_access_token(creds.credentials)
    tid = UUID(c["tid"]) if c.get("tid") else None
    if c["kind"] == "user" and tid is None:
        raise AppError("UNAUTHORIZED", "Token inválido")
    return Principal(UUID(c["sub"]), c["kind"], tid, c.get("rol"),
                     UUID(c["imp"]) if c.get("imp") else None)


def evaluate_access(*, bloqueo_manual: bool, estado: str, fecha_fin: date | None,
                    today: date, grace_days: int) -> AccessInfo:
    """Pura (testeable). Bloquea si: bloqueo manual, sin suscripción, o fecha_fin + gracia < hoy."""
    if bloqueo_manual:
        return AccessInfo(True, "manual", estado, fecha_fin)
    if fecha_fin is None:
        return AccessInfo(True, "sin_suscripcion", estado, None)
    if fecha_fin + timedelta(days=grace_days) < today:
        return AccessInfo(True, "vencimiento", estado, fecha_fin)
    return AccessInfo(False, None, estado, fecha_fin)


async def get_access_info(s: AsyncSession, tenant_id: UUID) -> AccessInfo:
    row = (await s.execute(text(
        "SELECT t.bloqueo_manual, t.estado, t.zona_horaria, sub.fecha_fin FROM tenants t "
        "LEFT JOIN tenant_subscriptions sub ON sub.tenant_id = t.id AND sub.estado = 'vigente' "
        "WHERE t.id = :t AND t.deleted_at IS NULL"), {"t": tenant_id})).one_or_none()
    if row is None:
        raise AppError("UNAUTHORIZED", "El negocio no existe")
    today = datetime.now(ZoneInfo(row.zona_horaria)).date()
    return evaluate_access(bloqueo_manual=row.bloqueo_manual, estado=row.estado, fecha_fin=row.fecha_fin,
                           today=today, grace_days=get_settings().subscription_grace_days)


async def ensure_tenant_active(s: AsyncSession, tenant_id: UUID) -> None:
    a = await get_access_info(s, tenant_id)
    if not a.bloqueado:
        return
    if a.motivo == "manual":
        raise AppError("TENANT_BLOCKED", "Tu cuenta está suspendida. Comunícate con soporte.",
                       {"motivo": a.motivo, "estado": a.estado}, status=403)
    msg = ("Tu suscripción venció" + (f" el {a.fecha_fin.isoformat()}" if a.fecha_fin else "")
           + ". Realiza el pago para reactivar tu cuenta.")
    raise AppError("TENANT_BLOCKED", msg, {"motivo": a.motivo, "estado": a.estado,
                                          "fecha_fin": a.fecha_fin.isoformat() if a.fecha_fin else None})


async def _session(p: Principal, *, check: bool) -> AsyncIterator[AsyncSession]:
    async with scoped_session(tenant_id=p.tenant_id, platform=p.kind == "platform_user") as s:
        # El Super Admin impersonando puede entrar a un tenant bloqueado (soporte).
        if check and p.kind == "user" and p.tenant_id and not p.impersonator_id:
            await ensure_tenant_active(s, p.tenant_id)
        yield s


async def get_db(p: Principal = Depends(get_principal)) -> AsyncIterator[AsyncSession]:
    async for s in _session(p, check=True):
        yield s


async def get_db_unrestricted(p: Principal = Depends(get_principal)) -> AsyncIterator[AsyncSession]:
    """Solo para /auth/me y logout: el frontend debe poder mostrar la pantalla de 'cuenta bloqueada'."""
    async for s in _session(p, check=False):
        yield s


@asynccontextmanager
async def tenant_tx(p: Principal) -> AsyncIterator[AsyncSession]:
    """Transacción que se CONFIRMA al salir del bloque `async with`. Úsala cuando después de escribir hay que lanzar una tarea de
    fondo que necesita ver esos datos: en FastAPI las tareas de fondo corren ANTES de que `get_db` confirme su transacción
    (el código posterior al `yield` de la dependencia se ejecuta al final), así que con `get_db` la tarea no vería la fila."""
    async for s in _session(p, check=True):
        yield s


def require_module(codigo: str) -> Callable[..., object]:
    async def dep(p: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)) -> None:
        if p.kind != "user":
            return
        ok = (await db.execute(text(
            "SELECT 1 FROM tenant_modules tm JOIN modules m ON m.id = tm.module_id "
            "WHERE tm.tenant_id = :t AND m.codigo = :c AND tm.activo"), {"t": p.tenant_id, "c": codigo})).first()
        if not ok:
            raise AppError("MODULE_NOT_ENABLED", "Tu plan no incluye este módulo", {"modulo": codigo})
    return dep


def require_any_module(*codigos: str) -> Callable[..., object]:
    """Para funciones que existen con cualquiera de varios módulos (p. ej. ventas: calendar o reception)."""
    async def dep(p: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)) -> None:
        if p.kind != "user":
            return
        ok = (await db.execute(text(
            "SELECT 1 FROM tenant_modules tm JOIN modules m ON m.id = tm.module_id "
            "WHERE tm.tenant_id = :t AND m.codigo = ANY(:c) AND tm.activo"), {"t": p.tenant_id, "c": list(codigos)})).first()
        if not ok:
            raise AppError("MODULE_NOT_ENABLED", "Tu plan no incluye este módulo", {"modulos": list(codigos)})
    return dep
