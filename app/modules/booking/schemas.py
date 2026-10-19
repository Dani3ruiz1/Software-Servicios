from datetime import date
from decimal import Decimal
from typing import Annotated, Literal
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
Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{1,48}[a-z0-9]$")]
Modo = Literal["cliente_elige", "automatica", "en_recepcion"]
TipoCampo = Literal["texto", "telefono", "email", "numero", "fecha", "seleccion", "casilla", "area_texto"]
TEXTOS_PERMITIDOS = {"titulo", "bienvenida", "confirmacion", "consentimiento"}
RESERVED_SLUGS = {"api", "admin", "www", "app", "docs", "health", "login", "public", "static"}


# ---------------------------------------------------------------- administración (autenticada)
class SettingsIn(BaseModel):
    slug: Slug | None = Field(None, description="Dirección pública. Por defecto, la del negocio")
    activo: bool = False
    color_primario: Color = "#2F5D50"
    color_secundario: Color = "#E8B86D"
    color_fondo: Color = "#FBF7F0"
    logo_url: Annotated[str, StringConstraints(pattern=r"^https://", max_length=500)] | None = None
    textos: dict[str, Annotated[str, StringConstraints(max_length=600)]] = {}
    anticipacion_min_horas: int = Field(1, ge=0, le=720)
    anticipacion_max_dias: int = Field(60, ge=1, le=365)
    buffer_min: int = Field(0, ge=0, le=240)
    cancelacion_min_horas: int = Field(2, ge=0, le=168, description="Horas antes de la cita hasta las que el cliente puede cancelar/reprogramar")
    modo_asignacion: Modo = "cliente_elige"
    pedir_servicio: bool = True

    @field_validator("slug")
    @classmethod
    def _slug(cls, v: str | None) -> str | None:
        if v and v in RESERVED_SLUGS:
            raise ValueError("Esa dirección está reservada")
        return v

    @field_validator("textos")
    @classmethod
    def _textos(cls, v: dict[str, str]) -> dict[str, str]:
        extra = set(v) - TEXTOS_PERMITIDOS
        if extra:
            raise ValueError(f"Textos no permitidos: {sorted(extra)}. Permitidos: {sorted(TEXTOS_PERMITIDOS)}")
        return v


class SettingsOut(SettingsIn):
    model_config = ConfigDict(from_attributes=True)
    slug: str
    configurado: bool = True


class FieldIn(BaseModel):
    etiqueta: str = Field(min_length=2, max_length=80)
    tipo: TipoCampo
    obligatorio: bool = False
    orden: int = 0
    opciones: list[Annotated[str, StringConstraints(min_length=1, max_length=80)]] | None = Field(None, max_length=30)

    @model_validator(mode="after")
    def _opciones(self) -> "FieldIn":
        if self.tipo == "seleccion" and not self.opciones:
            raise ValueError("Un campo de selección necesita opciones")
        if self.tipo != "seleccion" and self.opciones:
            raise ValueError("Solo los campos de selección llevan opciones")
        return self


class FieldPatch(BaseModel):
    etiqueta: str | None = Field(None, min_length=2, max_length=80)
    obligatorio: bool | None = None
    orden: int | None = None
    opciones: list[str] | None = None


class FieldOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    etiqueta: str
    tipo: str
    obligatorio: bool
    orden: int
    opciones: list[str] | None


class RuleIn(BaseModel):
    service_id: UUID | None = Field(None, description="Vacío = todos los servicios")
    staff_id: UUID | None = Field(None, description="Aún no soportado: debe ir vacío")
    cupos_simultaneos: int = Field(1, ge=1, le=100, description="Máximo de citas al mismo tiempo en todo el negocio")
    duracion_turno_min: int | None = Field(None, ge=5, le=240, description="Separación entre horarios ofrecidos (por defecto 30)")

    @field_validator("staff_id")
    @classmethod
    def _sin_staff(cls, v: UUID | None) -> UUID | None:
        if v is not None:
            raise ValueError("Las reglas por profesional aún no están soportadas")
        return v


class RuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    service_id: UUID | None
    staff_id: UUID | None
    cupos_simultaneos: int
    duracion_turno_min: int | None


# ---------------------------------------------------------------- público (sin autenticación)
class PublicBrand(BaseModel):
    nombre: str
    tipo: str = "barberia"     # business_type: barberia | spa | unas | estetica | salon
    color_primario: str
    color_secundario: str
    color_fondo: str
    logo_url: str | None
    textos: dict[str, str]


class PublicService(BaseModel):
    id: UUID
    nombre: str
    categoria: str | None
    duracion_min: int
    precio: Decimal


class PublicStaff(BaseModel):
    id: UUID
    nombre_publico: str
    especialidad: str | None
    foto_url: str | None
    service_ids: list[UUID]


class PublicField(BaseModel):
    id: UUID
    etiqueta: str
    tipo: str
    obligatorio: bool
    opciones: list[str] | None


class PublicConfig(BaseModel):
    marca: PublicBrand
    zona_horaria: str
    moneda: str
    pedir_servicio: bool
    modo_asignacion: str
    escoger_profesional: bool
    anticipacion_min_horas: int
    anticipacion_max_dias: int
    cancelacion_min_horas: int
    consentimiento_texto: str
    servicios: list[PublicService]
    profesionales: list[PublicStaff]
    campos: list[PublicField]


class ClientIn(BaseModel):
    nombre: str = Field(min_length=2, max_length=120)
    telefono: str = Field(min_length=7, max_length=20, description="Con o sin +57, espacios o guiones")
    email: EmailStr | None = None


class BookingIn(BaseModel):
    service_ids: list[UUID] = Field([], max_length=10)
    staff_id: UUID | None = Field(None, description="Solo si el negocio deja escoger profesional; vacío = cualquiera")
    inicio: AwareDatetime
    cliente: ClientIn
    consentimiento_datos: bool = Field(description="Debe ser true: autorización de tratamiento de datos (Ley 1581)")
    respuestas: dict[UUID, str | bool | int | float] = Field({}, description="id del campo del formulario -> valor")
    website: str = Field("", max_length=200, description="Trampa anti-bots: debe ir vacío")

    model_config = {"json_schema_extra": {"example": {
        "service_ids": ["a3d1c6f2-0c44-4c58-8f9e-6a8b2f0e5d33"], "inicio": "2026-10-20T10:00:00-05:00",
        "cliente": {"nombre": "Carlos Pérez", "telefono": "300 123 4567", "email": "carlos@correo.co"},
        "consentimiento_datos": True, "respuestas": {}, "website": ""}}}


class BookedService(BaseModel):
    nombre: str
    duracion_min: int
    precio: Decimal


class BookingOut(BaseModel):
    token: str = Field(description="Guárdalo/envíalo al cliente: permite ver, cancelar o reprogramar")
    estado: str
    negocio: str
    inicio: AwareDatetime
    fin: AwareDatetime
    profesional: str | None
    servicios: list[BookedService]
    total: Decimal
    puede_cancelar_hasta: AwareDatetime


class BookingView(BaseModel):
    estado: str
    negocio: str
    cliente: str
    inicio: AwareDatetime
    fin: AwareDatetime
    profesional: str | None
    servicios: list[BookedService]
    total: Decimal
    puede_modificar: bool
    puede_cancelar_hasta: AwareDatetime


class CancelIn(BaseModel):
    motivo: str | None = Field(None, max_length=300)


class RescheduleIn(BaseModel):
    inicio: AwareDatetime


class PublicDay(BaseModel):
    fecha: date
    horarios: list[AwareDatetime]
    profesionales_por_horario: dict[str, list[UUID]] = {}


class PublicAvailability(BaseModel):
    zona_horaria: str
    duracion_min: int
    dias: list[PublicDay]


