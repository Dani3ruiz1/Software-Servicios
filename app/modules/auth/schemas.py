from datetime import date
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, StringConstraints

Password = Annotated[str, StringConstraints(min_length=10, max_length=128)]
Slug = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9-]{3,50}$")]
Totp = Annotated[str, StringConstraints(pattern=r"^\d{6}$")]


class LoginIn(BaseModel):
    tenant_slug: Slug
    email: EmailStr
    password: str = Field(max_length=128)
    totp_code: Totp | None = None

    model_config = {"json_schema_extra": {"example": {
        "tenant_slug": "barberia-don-pepe", "email": "admin@donpepe.co", "password": "********"}}}


class PlatformLoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=128)
    totp_code: Totp | None = None


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105 - es el esquema HTTP, no una contraseña
    expires_in: int


class RefreshIn(BaseModel):
    refresh_token: str


class ChangePasswordIn(BaseModel):
    current_password: str = Field(max_length=128)
    new_password: Password


class ForgotPasswordIn(BaseModel):
    tenant_slug: Slug | None = None      # None => cuenta de plataforma
    email: EmailStr


class ResetPasswordIn(BaseModel):
    token: str
    new_password: Password


class TotpSetupOut(BaseModel):
    secret: str
    otpauth_uri: str


class TotpEnableIn(BaseModel):
    code: Totp


class TenantBrief(BaseModel):
    id: UUID
    nombre: str
    slug: str
    tipo: str = "barberia"
    zona_horaria: str
    modo: str
    estado: str
    bloqueado: bool
    motivo_bloqueo: str | None = None
    fecha_fin: date | None = None


class MeOut(BaseModel):
    id: UUID
    email: str
    nombre: str
    rol: str
    totp_habilitado: bool
    tenant: TenantBrief | None
    modulos_activos: list[str]
    permisos: list[str]
    impersonado: bool = False
