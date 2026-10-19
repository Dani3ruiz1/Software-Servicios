from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

Rol = Literal["admin", "recepcionista", "profesional"]


class UserIn(BaseModel):
    nombre: str = Field(min_length=2, max_length=120)
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    rol: Rol
    crear_perfil_profesional: bool | None = Field(
        None, description="Por defecto true cuando rol=profesional: crea su perfil de agenda")

    model_config = {"json_schema_extra": {"example": {
        "nombre": "Laura Gómez", "email": "laura@negocio.co", "password": "Clave.Larga.123", "rol": "profesional"}}}


class UserPatch(BaseModel):
    nombre: str | None = Field(None, min_length=2, max_length=120)
    rol: Rol | None = None
    activo: bool | None = None
    password: str | None = Field(None, min_length=10, max_length=128, description="Restablece la contraseña (admin)")


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    nombre: str
    email: str
    rol: str
    activo: bool
    totp_habilitado: bool
    ultimo_login: datetime | None
    staff_id: UUID | None = None


class SeatsOut(BaseModel):
    usados: int
    maximo: int
    disponibles: int
    plan: str
