from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Numeric, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Appointment(Base):
    __tablename__ = "appointments"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    customer_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    staff_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    inicio: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    fin: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    estado: Mapped[str] = mapped_column(default="pendiente")
    origen: Mapped[str] = mapped_column(default="recepcion")
    notas: Mapped[str | None]
    created_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AppointmentService(Base):
    __tablename__ = "appointment_services"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    appointment_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    service_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    precio_pactado: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    duracion_min: Mapped[int]
