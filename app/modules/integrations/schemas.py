from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class WhatsAppIn(BaseModel):
    phone_number_id: Annotated[str, StringConstraints(pattern=r"^\d{5,30}$")] = Field(description="ID del número en Meta (no es el teléfono)")
    access_token: str | None = Field(None, min_length=20, max_length=1000, description="Token permanente de la API de WhatsApp. Se guarda cifrado y no se devuelve")
    app_secret: str | None = Field(None, min_length=8, max_length=200, description="Clave secreta de la app de Meta (firma el webhook). Se guarda cifrada")
    verify_token: str | None = Field(None, min_length=8, max_length=100, description="Opcional: si se omite se genera uno")
    plantillas: dict[Annotated[str, StringConstraints(pattern=r"^[a-z_]{3,40}$")], Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]{1,512}$")]] = Field(
        {}, description="plantilla interna -> nombre de la plantilla APROBADA en Meta. Claves: reserva_confirmada, recordatorio_cita, comprobante")


class WhatsAppStatus(BaseModel):
    configurado: bool
    phone_number_id: str | None = None
    estado: str | None = None
    ultima_verificacion: datetime | None = None
    webhook_url: str | None = None
    tiene_token: bool = False
    tiene_app_secret: bool = False
    plantillas: dict[str, str] = {}
    verify_token: str | None = Field(None, description="Solo se muestra al guardar: cópialo en Meta junto con la URL del webhook")


class ValidationOut(BaseModel):
    valida: bool
    detalle: str | None = None
    numero: str | None = None
    nombre_verificado: str | None = None


class AIIn(BaseModel):
    activo: bool = False
    nombre_asistente: str = Field("Asistente", min_length=2, max_length=40)
    instrucciones: str = Field("", max_length=1500, description="Tono, promociones permitidas, políticas propias del negocio")
    api_key: str | None = Field(None, min_length=20, max_length=300, description="Opcional: clave de IA propia. Si se omite se usa la de la plataforma. No se devuelve")
    quitar_api_key: bool = False


class AIStatus(BaseModel):
    activo: bool
    nombre_asistente: str
    instrucciones: str
    usa_clave_propia: bool
    estado: str | None
    ultima_verificacion: datetime | None
    mensajes_usados_mes: int
    limite_mensual: int | None = Field(description="null = el plan no incluye mensajes de IA")


class ConversationItem(BaseModel):
    id: UUID
    contacto: str
    nombre: str | None
    customer_id: UUID | None
    escalado: bool
    motivo: str | None
    ultimo_mensaje_en: datetime | None
    ultimo_texto: str | None
    ultimo_rol: str | None
    sin_responder: bool = Field(description="El último mensaje es del cliente y nadie ha respondido")


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    rol: str
    contenido: str
    estado: str
    created_at: datetime


class ReplyIn(BaseModel):
    texto: str = Field(min_length=1, max_length=4000)


class ReplyOut(BaseModel):
    mensaje: MessageOut
    escalado: bool
