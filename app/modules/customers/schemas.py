from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

Origen = Literal["reserva_web", "whatsapp", "recepcion"]


class CustomerIn(BaseModel):
    nombre: str = Field(min_length=2, max_length=120)
    telefono: str | None = Field(None, max_length=30)
    email: EmailStr | None = None
    documento: str | None = Field(None, max_length=30)
    notas: str | None = None
    consentimiento_datos: bool = Field(False, description="Autorización de tratamiento de datos (Ley 1581)")
    origen: Origen = "recepcion"

    model_config = {"json_schema_extra": {"example": {
        "nombre": "Carlos Pérez", "telefono": "3001234567", "consentimiento_datos": True}}}


class CustomerPatch(BaseModel):
    nombre: str | None = Field(None, min_length=2, max_length=120)
    telefono: str | None = None
    email: EmailStr | None = None
    documento: str | None = None
    notas: str | None = None
    consentimiento_datos: bool | None = None


class CustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    nombre: str
    telefono: str | None
    email: str | None
    documento: str | None
    notas: str | None
    consentimiento_datos: bool
    consentimiento_fecha: datetime | None
    consentimiento_version: str | None
    origen: str
    created_at: datetime


class HistoryServiceItem(BaseModel):
    nombre: str
    precio_pactado: Decimal


class HistoryItem(BaseModel):
    appointment_id: UUID
    inicio: datetime
    estado: str
    profesional: str | None
    servicios: list[HistoryServiceItem]


class CustomerHistory(BaseModel):
    cliente: CustomerOut
    total_citas: int
    atendidas: int
    canceladas: int
    no_asistio: int
    ultima_visita: datetime | None
    citas: list[HistoryItem]
