from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

Money = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]
Cantidad = Annotated[int, Field(ge=0, le=1_000_000)]


class ProductIn(BaseModel):
    nombre: str = Field(min_length=2, max_length=120)
    sku: str | None = Field(None, max_length=40, description="Código interno o de barras; único por negocio")
    costo: Money = Decimal("0")
    precio_venta: Money = Decimal("0")
    stock_inicial: Cantidad = Field(0, description="Si es mayor que 0 se registra como una entrada «Stock inicial»")
    stock_minimo: Cantidad = Field(0, description="Por debajo (o igual) se marca como bajo stock; 0 = sin alerta")
    activo: bool = True

    model_config = {"json_schema_extra": {"example": {
        "nombre": "Cera para barba", "sku": "CERA-01", "costo": "18000", "precio_venta": "35000", "stock_inicial": 12, "stock_minimo": 4}}}


class ProductPatch(BaseModel):
    """No cambia el stock: eso solo se hace con movimientos."""
    nombre: str | None = Field(None, min_length=2, max_length=120)
    sku: str | None = Field(None, max_length=40)
    costo: Money | None = None
    precio_venta: Money | None = None
    stock_minimo: Cantidad | None = None
    activo: bool | None = None


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    nombre: str
    sku: str | None
    costo: Decimal
    precio_venta: Decimal
    stock_actual: int
    stock_minimo: int
    activo: bool
    created_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def bajo_stock(self) -> bool:
        return self.stock_minimo > 0 and self.stock_actual <= self.stock_minimo


class MovementIn(BaseModel):
    """`entrada` suma `cantidad`; `salida` resta `cantidad`; `ajuste` fija el stock en el conteo físico `cantidad`."""
    tipo: Literal["entrada", "salida", "ajuste"]
    cantidad: Cantidad
    motivo: str | None = Field(None, max_length=200)
    costo_unitario: Money | None = Field(None, description="Solo en entradas: actualiza el último costo del producto")

    @model_validator(mode="after")
    def _reglas(self) -> "MovementIn":
        if self.tipo != "ajuste" and self.cantidad < 1:
            raise ValueError("La cantidad debe ser al menos 1")
        if self.tipo == "ajuste" and len((self.motivo or "").strip()) < 3:
            raise ValueError("Un ajuste necesita un motivo (p. ej. «conteo físico»)")
        if self.costo_unitario is not None and self.tipo != "entrada":
            raise ValueError("El costo unitario solo se envía en entradas")
        return self

    model_config = {"json_schema_extra": {"example": {"tipo": "salida", "cantidad": 2, "motivo": "Uso en servicio"}}}


class MovementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    product_id: UUID
    tipo: str
    delta: int
    stock_resultante: int
    costo_unitario: Decimal | None
    motivo: str | None
    created_at: datetime


class SummaryOut(BaseModel):
    productos: int
    bajo_stock: int
    valor_inventario: Decimal
