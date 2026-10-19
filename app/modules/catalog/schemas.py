from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Money = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]


class CategoryIn(BaseModel):
    nombre: str = Field(min_length=2, max_length=80)
    orden: int = 0


class CategoryPatch(BaseModel):
    nombre: str | None = Field(None, min_length=2, max_length=80)
    orden: int | None = None


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    nombre: str
    orden: int


class ServiceIn(BaseModel):
    category_id: UUID | None = None
    nombre: str = Field(min_length=2, max_length=120)
    duracion_min: int = Field(gt=0, le=720)
    precio: Money
    precio_editable: bool = False
    aplica_iva: bool = False
    porcentaje_iva: Decimal | None = Field(None, ge=0, le=100, description="Si aplica_iva y se omite: 19")
    activo: bool = True

    model_config = {"json_schema_extra": {"example": {
        "nombre": "Corte clásico", "duracion_min": 30, "precio": "25000", "precio_editable": False,
        "aplica_iva": False}}}


class ServicePatch(BaseModel):
    category_id: UUID | None = None
    nombre: str | None = Field(None, min_length=2, max_length=120)
    duracion_min: int | None = Field(None, gt=0, le=720)
    precio: Money | None = None
    precio_editable: bool | None = None
    aplica_iva: bool | None = None
    porcentaje_iva: Decimal | None = Field(None, ge=0, le=100)
    activo: bool | None = None


class ServiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    category_id: UUID | None
    nombre: str
    duracion_min: int
    precio: Decimal
    precio_editable: bool
    aplica_iva: bool
    porcentaje_iva: Decimal
    activo: bool
