from datetime import datetime, time
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Time
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class StaffProfile(Base):
    __tablename__ = "staff_profiles"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    user_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    nombre_publico: Mapped[str]
    especialidad: Mapped[str | None]
    color_calendario: Mapped[str] = mapped_column(default="#6B8F71")
    foto_url: Mapped[str | None]
    activo: Mapped[bool] = mapped_column(default=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StaffSchedule(Base):
    __tablename__ = "staff_schedules"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    staff_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    dia_semana: Mapped[int]
    hora_inicio: Mapped[time] = mapped_column(Time)
    hora_fin: Mapped[time] = mapped_column(Time)


class ScheduleBlock(Base):
    __tablename__ = "schedule_blocks"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    staff_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    inicio: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    fin: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    motivo: Mapped[str | None]


class StaffService(Base):
    __tablename__ = "staff_services"
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    staff_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    service_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
