from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Numeric, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

_M = Numeric(12, 2)


class CashSession(Base):
    __tablename__ = "cash_sessions"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    abierta_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    cerrada_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    responsable_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    monto_inicial: Mapped[Decimal] = mapped_column(_M, default=Decimal("0"))
    monto_esperado: Mapped[Decimal | None] = mapped_column(_M)
    monto_contado: Mapped[Decimal | None] = mapped_column(_M)
    diferencia: Mapped[Decimal | None] = mapped_column(_M)
    estado: Mapped[str] = mapped_column(default="abierta")
    notas: Mapped[str | None]


class CashMovement(Base):
    __tablename__ = "cash_movements"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    cash_session_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    tipo: Mapped[str]
    categoria: Mapped[str]
    monto: Mapped[Decimal] = mapped_column(_M)
    descripcion: Mapped[str | None]
    soporte_url: Mapped[str | None]
    created_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
