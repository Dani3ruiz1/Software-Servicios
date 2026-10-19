from datetime import date
from uuid import UUID, uuid4

from sqlalchemy import Date
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class InvoiceSettings(Base):
    __tablename__ = "invoice_settings"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), unique=True)
    nit: Mapped[str]
    dv: Mapped[int | None]
    razon_social: Mapped[str]
    resolucion: Mapped[str | None]
    vigencia_hasta: Mapped[date | None] = mapped_column(Date)
    prefijo: Mapped[str | None]
    prefijo_nota_credito: Mapped[str] = mapped_column(default="NC")
    rango_desde: Mapped[int | None]
    rango_hasta: Mapped[int | None]
    ambiente: Mapped[str] = mapped_column(default="habilitacion")
