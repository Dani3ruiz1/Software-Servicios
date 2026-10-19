from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field, model_validator

Estado = Literal["pendiente", "confirmada", "en_atencion", "atendida", "cancelada", "no_asistio"]
Vista = Literal["dia", "semana", "mes"]


class AppointmentIn(BaseModel):
    customer_id: UUID
    staff_id: UUID | None = Field(None, description="Omitir = cita sin profesional (se asigna en recepción)")
    inicio: AwareDatetime
    service_ids: list[UUID] = []
    duracion_min: int | None = Field(None, gt=0, le=720, description="Obligatorio si no se envían servicios")
    notas: str | None = Field(None, max_length=1000)
    asignar_automatico: bool = Field(False, description="Elige al profesional libre con menos citas ese día")

    @model_validator(mode="after")
    def _algo_que_dure(self) -> "AppointmentIn":
        if not self.service_ids and not self.duracion_min:
            raise ValueError("Envía al menos un servicio o duracion_min")
        if self.staff_id and self.asignar_automatico:
            raise ValueError("Usa staff_id O asignar_automatico, no ambos")
        return self

    model_config = {"json_schema_extra": {"example": {
        "customer_id": "7b0c6a3e-3d1a-4a52-9c1f-0a1d6d7a9e11", "staff_id": "0f6f0d54-5c0e-4b8e-8d52-6b3c8a9b1c22",
        "inicio": "2026-10-20T10:00:00-05:00", "service_ids": ["a3d1c6f2-0c44-4c58-8f9e-6a8b2f0e5d33"]}}}


class AppointmentPatch(BaseModel):
    notas: str | None = Field(None, max_length=1000)
    customer_id: UUID | None = None


class MoveIn(BaseModel):
    inicio: AwareDatetime
    staff_id: UUID | None = Field(None, description="Opcional: reasigna a otro profesional (o asigna uno a una cita sin profesional)")


class StatusIn(BaseModel):
    estado: Estado
    motivo: str | None = Field(None, max_length=300, description="Recomendado al cancelar")


class CustomerBrief(BaseModel):
    id: UUID
    nombre: str
    telefono: str | None


class StaffBrief(BaseModel):
    id: UUID
    nombre_publico: str
    color_calendario: str


class ServiceLine(BaseModel):
    service_id: UUID
    nombre: str
    precio_pactado: Decimal
    duracion_min: int


class AppointmentOut(BaseModel):
    id: UUID
    cliente: CustomerBrief
    profesional: StaffBrief | None
    inicio: AwareDatetime
    fin: AwareDatetime
    estado: str
    origen: str
    notas: str | None
    servicios: list[ServiceLine]
    total: Decimal


class SlotOut(BaseModel):
    inicio: AwareDatetime
    fin: AwareDatetime
    staff_ids: list[UUID]


class DaySlots(BaseModel):
    fecha: date
    slots: list[SlotOut]


class AvailabilityOut(BaseModel):
    zona_horaria: str
    duracion_min: int
    dias: list[DaySlots]


class BlockBrief(BaseModel):
    id: UUID
    staff_id: UUID
    inicio: AwareDatetime
    fin: AwareDatetime
    motivo: str | None


class CalendarOut(BaseModel):
    vista: Vista
    desde: AwareDatetime
    hasta: AwareDatetime
    zona_horaria: str
    citas: list[AppointmentOut]
    bloqueos: list[BlockBrief]


class StatsOut(BaseModel):
    """Conteo de turnos (útil para el plan independiente, que no tiene reportes)."""
    total: int
    por_estado: dict[str, int]
