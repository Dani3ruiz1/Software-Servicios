from datetime import time
from typing import Annotated
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

Color = Annotated[str, StringConstraints(pattern=r"^#[0-9A-Fa-f]{6}$")]


class StaffIn(BaseModel):
    nombre_publico: str = Field(min_length=2, max_length=80)
    especialidad: str | None = None
    color_calendario: Color = "#6B8F71"
    foto_url: str | None = None
    user_id: UUID | None = Field(None, description="Usuario del sistema asociado (necesario para el rol profesional)")
    service_ids: list[UUID] = []


class StaffPatch(BaseModel):
    nombre_publico: str | None = Field(None, min_length=2, max_length=80)
    especialidad: str | None = None
    color_calendario: Color | None = None
    foto_url: str | None = None
    activo: bool | None = None


class StaffOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    user_id: UUID | None
    nombre_publico: str
    especialidad: str | None
    color_calendario: str
    foto_url: str | None
    activo: bool
    service_ids: list[UUID] = []


class AccessIn(BaseModel):
    """Crea el usuario con el que el profesional entra a ver SU agenda (rol `profesional`)."""
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)

    model_config = {"json_schema_extra": {"example": {"email": "andres@barberia.co", "password": "Clave.Larga.123"}}}


class AccessOut(BaseModel):
    usuario_id: UUID
    email: str


class StaffServicesIn(BaseModel):
    service_ids: list[UUID]


class ScheduleItem(BaseModel):
    dia_semana: int = Field(ge=0, le=6, description="0 = lunes … 6 = domingo")
    hora_inicio: time
    hora_fin: time

    @model_validator(mode="after")
    def _orden(self) -> "ScheduleItem":
        if self.hora_fin <= self.hora_inicio:
            raise ValueError("hora_fin debe ser posterior a hora_inicio")
        return self


class ScheduleIn(BaseModel):
    """Reemplaza el horario semanal completo. Un descanso de almuerzo = dos tramos en el mismo día."""
    tramos: list[ScheduleItem]

    @field_validator("tramos")
    @classmethod
    def _sin_solapes(cls, v: list[ScheduleItem]) -> list[ScheduleItem]:
        for i, a in enumerate(v):
            for b in v[i + 1:]:
                if a.dia_semana == b.dia_semana and a.hora_inicio < b.hora_fin and b.hora_inicio < a.hora_fin:
                    raise ValueError("Hay tramos que se solapan el mismo día")
        return v

    model_config = {"json_schema_extra": {"example": {"tramos": [
        {"dia_semana": 0, "hora_inicio": "09:00", "hora_fin": "13:00"},
        {"dia_semana": 0, "hora_inicio": "14:00", "hora_fin": "19:00"}]}}}


class BlockIn(BaseModel):
    inicio: AwareDatetime
    fin: AwareDatetime
    motivo: str | None = Field(None, max_length=200)

    @model_validator(mode="after")
    def _orden(self) -> "BlockIn":
        if self.fin <= self.inicio:
            raise ValueError("fin debe ser posterior a inicio")
        return self


class BlockOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    staff_id: UUID
    inicio: AwareDatetime
    fin: AwareDatetime
    motivo: str | None
    citas_afectadas: int = 0
