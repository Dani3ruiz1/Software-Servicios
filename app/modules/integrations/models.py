from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

_TS = DateTime(timezone=True)


class Integration(Base):
    __tablename__ = "integrations"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    tipo: Mapped[str]
    credenciales_cifradas: Mapped[str | None]
    identificador_externo: Mapped[str | None]
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    estado: Mapped[str] = mapped_column(default="pendiente")
    ultima_verificacion: Mapped[datetime | None] = mapped_column(_TS)


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    customer_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    canal: Mapped[str] = mapped_column(default="whatsapp")
    contacto_externo: Mapped[str]
    nombre_contacto: Mapped[str | None]
    escalado_a_humano: Mapped[bool] = mapped_column(default=False)
    escalado_motivo: Mapped[str | None]
    ultimo_mensaje_en: Mapped[datetime | None] = mapped_column(_TS)
    ultimo_mensaje_cliente_en: Mapped[datetime | None] = mapped_column(_TS)
    created_at: Mapped[datetime] = mapped_column(_TS, server_default=func.now())


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    conversation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    rol: Mapped[str]
    contenido: Mapped[str]
    estado: Mapped[str] = mapped_column(default="recibido")
    id_externo: Mapped[str | None]
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(_TS, server_default=func.now())
