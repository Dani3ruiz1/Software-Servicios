from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Product(Base):
    __tablename__ = "products"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    nombre: Mapped[str]
    sku: Mapped[str | None]
    costo: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    precio_venta: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    stock_actual: Mapped[int] = mapped_column(default=0)
    stock_minimo: Mapped[int] = mapped_column(default=0)
    activo: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StockMovement(Base):
    """Libro de movimientos: solo se agrega (la API no puede modificarlo ni borrarlo)."""
    __tablename__ = "stock_movements"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    product_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    tipo: Mapped[str]
    delta: Mapped[int]
    stock_resultante: Mapped[int]
    costo_unitario: Mapped[Decimal | None]
    motivo: Mapped[str | None]
    user_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
