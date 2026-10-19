from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Money = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]
Positive = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]


class OpenIn(BaseModel):
    monto_inicial: Money = Decimal("0")
    notas: str | None = Field(None, max_length=500)


class CloseIn(BaseModel):
    monto_contado: Money = Field(description="Efectivo contado físicamente en la caja")
    notas: str | None = Field(None, max_length=500)


class MovementIn(BaseModel):
    tipo: Literal["ingreso", "egreso"]
    categoria: str = Field(min_length=2, max_length=60, examples=["insumos", "propinas", "gastos varios"])
    monto: Positive
    descripcion: str | None = Field(None, max_length=300)
    soporte_url: str | None = Field(None, max_length=500)


class MovementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    tipo: str
    categoria: str
    monto: Decimal
    descripcion: str | None
    soporte_url: str | None
    created_at: datetime


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    abierta_en: datetime
    cerrada_en: datetime | None
    responsable_id: UUID
    monto_inicial: Decimal
    monto_esperado: Decimal | None
    monto_contado: Decimal | None
    diferencia: Decimal | None
    estado: str
    notas: str | None


class MethodTotal(BaseModel):
    metodo: str
    monto: Decimal
    pagos: int


class Cuadre(BaseModel):
    """Cuadre de caja: lo que DEBERÍA haber en efectivo vs. lo contado."""
    sesion: SessionOut
    monto_inicial: Decimal
    ventas_efectivo: Decimal
    ingresos: Decimal
    egresos: Decimal
    esperado_efectivo: Decimal
    contado: Decimal | None
    diferencia: Decimal | None
    por_metodo: list[MethodTotal]
    ventas_pagadas: int
    total_vendido: Decimal
    propinas: Decimal
    movimientos: list[MovementOut]
