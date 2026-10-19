from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    nombre: Mapped[str]
    telefono: Mapped[str | None]
    email: Mapped[str | None] = mapped_column(CITEXT)
    documento: Mapped[str | None]
    notas: Mapped[str | None]
    consentimiento_datos: Mapped[bool] = mapped_column(default=False)
    consentimiento_fecha: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consentimiento_version: Mapped[str | None]
    origen: Mapped[str] = mapped_column(default="recepcion")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
