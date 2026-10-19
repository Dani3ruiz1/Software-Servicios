from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import CITEXT, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class BookingSettings(Base):
    __tablename__ = "booking_settings"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), unique=True)
    slug: Mapped[str] = mapped_column(CITEXT, unique=True)
    activo: Mapped[bool] = mapped_column(default=False)
    color_primario: Mapped[str] = mapped_column(default="#2F5D50")
    color_secundario: Mapped[str] = mapped_column(default="#E8B86D")
    color_fondo: Mapped[str] = mapped_column(default="#FBF7F0")
    logo_url: Mapped[str | None]
    textos: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    anticipacion_min_horas: Mapped[int] = mapped_column(default=1)
    anticipacion_max_dias: Mapped[int] = mapped_column(default=60)
    buffer_min: Mapped[int] = mapped_column(default=0)
    cancelacion_min_horas: Mapped[int] = mapped_column(default=2)
    modo_asignacion: Mapped[str] = mapped_column(default="cliente_elige")
    pedir_servicio: Mapped[bool] = mapped_column(default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BookingFormField(Base):
    __tablename__ = "booking_form_fields"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    etiqueta: Mapped[str]
    tipo: Mapped[str]
    obligatorio: Mapped[bool] = mapped_column(default=False)
    orden: Mapped[int] = mapped_column(default=0)
    opciones: Mapped[list[str] | None] = mapped_column(JSONB)


class AvailabilityRule(Base):
    __tablename__ = "availability_rules"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    service_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    staff_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    cupos_simultaneos: Mapped[int] = mapped_column(default=1)
    duracion_turno_min: Mapped[int | None]
