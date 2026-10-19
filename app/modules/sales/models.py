from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Numeric, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

_M = Numeric(12, 2)
_TS = DateTime(timezone=True)


class Sale(Base):
    __tablename__ = "sales"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    customer_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    staff_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    appointment_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    cash_session_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    subtotal: Mapped[Decimal] = mapped_column(_M, default=Decimal("0"))
    iva: Mapped[Decimal] = mapped_column(_M, default=Decimal("0"))
    propina: Mapped[Decimal] = mapped_column(_M, default=Decimal("0"))
    total: Mapped[Decimal] = mapped_column(_M, default=Decimal("0"))
    estado: Mapped[str] = mapped_column(default="abierta")
    comprobante_numero: Mapped[str | None]
    pagada_en: Mapped[datetime | None] = mapped_column(_TS)
    anulada_en: Mapped[datetime | None] = mapped_column(_TS)
    anulada_motivo: Mapped[str | None]
    created_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(_TS, server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(_TS)


class SaleItem(Base):
    __tablename__ = "sale_items"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    sale_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    service_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    descripcion: Mapped[str]
    precio_original: Mapped[Decimal] = mapped_column(_M)
    precio_cobrado: Mapped[Decimal] = mapped_column(_M)
    iva: Mapped[Decimal] = mapped_column(_M, default=Decimal("0"))


class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    sale_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    metodo: Mapped[str]
    monto: Mapped[Decimal] = mapped_column(_M)
    entidad_bancaria: Mapped[str | None]
    referencia: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(_TS, server_default=func.now())


class Invoice(Base):
    """Comprobantes: internos (`interna`, Fase 5), facturas electrónicas y notas crédito (Fase 8)."""
    __tablename__ = "invoices"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    sale_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    tipo: Mapped[str]
    factura_origen_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    prefijo: Mapped[str | None]
    numero: Mapped[int]
    cufe: Mapped[str | None]
    estado: Mapped[str] = mapped_column(default="emitida")
    xml_url: Mapped[str | None]
    pdf_url: Mapped[str | None]
    respuesta_proveedor: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    intentos: Mapped[int] = mapped_column(default=0)
    fecha_emision: Mapped[datetime] = mapped_column(_TS, server_default=func.now())
    datos: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    motivo: Mapped[str | None]
    referencia_proveedor: Mapped[str | None]
    proximo_intento: Mapped[datetime | None] = mapped_column(_TS)
    ultimo_error: Mapped[str | None]
