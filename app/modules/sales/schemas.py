from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

Money = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]
Positive = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]
Metodo = Literal["efectivo", "datafono", "transferencia", "otro"]


class SaleItemIn(BaseModel):
    service_id: UUID | None = None
    descripcion: str | None = Field(None, max_length=200, description="Solo para ítems sin servicio (p. ej. un producto)")
    precio_cobrado: Money | None = Field(None, description="Omitir = precio de lista. Solo se puede cambiar si el servicio es de precio editable")

    @model_validator(mode="after")
    def _algo(self) -> "SaleItemIn":
        if self.service_id is None and not (self.descripcion and self.precio_cobrado is not None):
            raise ValueError("Cada ítem necesita service_id, o descripcion + precio_cobrado")
        return self


class PaymentIn(BaseModel):
    metodo: Metodo
    monto: Positive
    entidad_bancaria: str | None = Field(None, max_length=80, description="Obligatoria en transferencias")
    referencia: str | None = Field(None, max_length=80)

    @model_validator(mode="after")
    def _banco(self) -> "PaymentIn":
        if self.metodo == "transferencia" and not (self.entidad_bancaria or "").strip():
            raise ValueError("La transferencia exige entidad_bancaria")
        return self


class SaleIn(BaseModel):
    customer_id: UUID | None = None
    staff_id: UUID | None = None
    appointment_id: UUID | None = Field(None, description="Convierte la cita en venta (cliente, profesional e ítems salen de ella)")
    items: list[SaleItemIn] = []
    propina: Money = Decimal("0")
    pagos: list[PaymentIn] = Field([], description="Vacío = la venta queda abierta y se cobra luego con /pay. Pago dividido = varios elementos")

    @model_validator(mode="after")
    def _items(self) -> "SaleIn":
        if not self.items and not self.appointment_id:
            raise ValueError("Envía items o appointment_id")
        return self

    model_config = {"json_schema_extra": {"example": {
        "appointment_id": "7b0c6a3e-3d1a-4a52-9c1f-0a1d6d7a9e11", "propina": "3000",
        "pagos": [{"metodo": "efectivo", "monto": "20000"},
                  {"metodo": "transferencia", "monto": "8000", "entidad_bancaria": "Bancolombia", "referencia": "TX123"}]}}}


class PayIn(BaseModel):
    pagos: list[PaymentIn] = Field(min_length=1)
    propina: Money | None = Field(None, description="Opcional: fijar/cambiar la propina al cobrar")


class VoidIn(BaseModel):
    motivo: str = Field(min_length=3, max_length=300)


class SendIn(BaseModel):
    canal: Literal["whatsapp", "email"]
    destinatario: str | None = Field(None, description="Por defecto: teléfono/correo del cliente")


class ItemOut(BaseModel):
    id: UUID
    service_id: UUID | None
    descripcion: str
    precio_original: Decimal
    precio_cobrado: Decimal
    descuento: Decimal
    iva: Decimal


class PaymentOut(BaseModel):
    id: UUID
    metodo: str
    monto: Decimal
    entidad_bancaria: str | None
    referencia: str | None


class SaleOut(BaseModel):
    id: UUID
    estado: str
    customer_id: UUID | None
    cliente: str | None
    staff_id: UUID | None
    profesional: str | None
    appointment_id: UUID | None
    cash_session_id: UUID | None
    subtotal: Decimal
    iva: Decimal
    propina: Decimal
    total: Decimal
    comprobante_numero: str | None
    items: list[ItemOut]
    pagos: list[PaymentOut]
    created_at: datetime
    pagada_en: datetime | None
    anulada_en: datetime | None
    anulada_motivo: str | None


class SaleListItem(BaseModel):
    id: UUID
    estado: str
    cliente: str | None
    profesional: str | None
    total: Decimal
    comprobante_numero: str | None
    created_at: datetime
    pagada_en: datetime | None


class Receipt(BaseModel):
    """Comprobante interno listo para renderizar/imprimir en el frontend."""
    tipo: Literal["interna"] = "interna"
    numero: str | None
    estado: str
    negocio: dict[str, str | None]
    fecha: datetime
    cliente: str | None
    profesional: str | None
    items: list[ItemOut]
    pagos: list[PaymentOut]
    subtotal: Decimal
    iva: Decimal
    propina: Decimal
    total: Decimal
    moneda: str


class SendOut(BaseModel):
    notificacion_id: UUID
    estado: str
    canal: str
    destinatario: str
