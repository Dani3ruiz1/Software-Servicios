from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import AuditLog

# Campos que jamás se escriben en auditoría
_REDACT = {"password", "password_hash", "totp_secret", "credenciales", "token"}


def _clean(d: dict[str, Any] | None) -> dict[str, Any] | None:
    return None if d is None else {k: ("***" if k in _REDACT else v) for k, v in d.items()}


def record(s: AsyncSession, *, actor_tipo: str, actor_id: UUID | None, tenant_id: UUID | None,
           accion: str, entidad: str, entidad_id: UUID | None = None,
           antes: dict[str, Any] | None = None, despues: dict[str, Any] | None = None,
           ip: str | None = None) -> None:
    """Se añade a la MISMA transacción que el cambio auditado: o ambos o ninguno."""
    s.add(AuditLog(actor_tipo=actor_tipo, actor_id=actor_id, tenant_id=tenant_id, accion=accion,
                   entidad=entidad, entidad_id=entidad_id, antes=_clean(antes),
                   despues=_clean(despues), ip=ip))
