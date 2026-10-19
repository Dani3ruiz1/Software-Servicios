from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    actor_tipo: Mapped[str]
    actor_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    tenant_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    accion: Mapped[str]
    entidad: Mapped[str]
    entidad_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    antes: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    despues: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ip: Mapped[str | None] = mapped_column(INET)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
