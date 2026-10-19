from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Numeric
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ServiceCategory(Base):
    __tablename__ = "service_categories"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    nombre: Mapped[str]
    orden: Mapped[int] = mapped_column(default=0)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Service(Base):
    __tablename__ = "services"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    category_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    nombre: Mapped[str]
    duracion_min: Mapped[int]
    precio: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    precio_editable: Mapped[bool] = mapped_column(default=False)
    aplica_iva: Mapped[bool] = mapped_column(default=False)
    porcentaje_iva: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0"))
    activo: Mapped[bool] = mapped_column(default=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
