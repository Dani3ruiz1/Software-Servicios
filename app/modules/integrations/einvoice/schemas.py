from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, StringConstraints, model_validator

Ambiente = Literal["habilitacion", "produccion"]


class SettingsIn(BaseModel):
    nit: Annotated[str, StringConstraints(pattern=r"^\d{5,15}$")] = Field(description="Solo números, sin puntos, guion ni DV")
    dv: int | None = Field(None, ge=0, le=9, description="Dígito de verificación; si se omite se calcula, si se envía debe coincidir")
    razon_social: str = Field(min_length=3, max_length=200)
    resolucion: str = Field(min_length=3, max_length=40, description="Número de la resolución de numeración autorizada por la DIAN")
    vigencia_hasta: date = Field(description="Fecha hasta la que es válida la resolución")
    prefijo: Annotated[str, StringConstraints(pattern=r"^[A-Z0-9]{0,4}$")] = ""
    prefijo_nota_credito: Annotated[str, StringConstraints(pattern=r"^[A-Z0-9]{1,4}$")] = "NC"
    rango_desde: int = Field(ge=1)
    rango_hasta: int = Field(ge=1)
    ambiente: Ambiente = "habilitacion"

    @model_validator(mode="after")
    def _rango(self) -> "SettingsIn":
        if self.rango_hasta < self.rango_desde:
            raise ValueError("rango_hasta debe ser mayor o igual a rango_desde")
        if self.prefijo == self.prefijo_nota_credito:
            raise ValueError("El prefijo de las notas crédito debe ser distinto al de las facturas")
        return self


class SettingsOut(SettingsIn):
    dv: int
    configurado: bool = True
    ultimo_numero: int | None = None
    restantes: int | None = Field(None, description="Números que quedan en el rango autorizado")
    resolucion_vigente: bool = True


class ProviderIn(BaseModel):
    proveedor: str = Field(min_length=2, max_length=40, description="Nombre registrado del proveedor (hoy: sandbox)")
    credenciales: dict[str, Annotated[str, StringConstraints(max_length=500)]] = Field({}, description="Se guardan cifradas y no se devuelven")


class ProviderStatus(BaseModel):
    configurado: bool
    proveedor: str | None = None
    estado: str | None = None
    ultima_verificacion: datetime | None = None
    tiene_credenciales: bool = False
    proveedores_disponibles: list[str] = []


class ValidationOut(BaseModel):
    valida: bool
    detalle: str | None = None


class AdquirenteIn(BaseModel):
    tipo_documento: str = Field("13", description="13 cédula · 31 NIT · 22 cédula de extranjería · 41 pasaporte · 12 tarjeta de identidad · 42 doc. extranjero")
    documento: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9]{3,20}$")]
    nombre: str = Field(min_length=2, max_length=200)
    email: EmailStr | None = None


class EmitIn(BaseModel):
    adquirente: AdquirenteIn | None = Field(None, description="Por defecto: los datos del cliente de la venta, o «Consumidor final»")


class RetryIn(BaseModel):
    adquirente: AdquirenteIn | None = Field(None, description="Para corregir los datos del adquirente tras un rechazo (solo facturas)")


class CreditNoteIn(BaseModel):
    motivo: str = Field(min_length=3, max_length=300)
    codigo_motivo: int = Field(2, ge=1, le=6, description="1 devolución parcial · 2 anulación · 3 rebaja · 4 descuento · 5 rescisión · 6 otros")
    item_ids: list[UUID] | None = Field(None, description="Ítems de la venta a acreditar; vacío = todo lo que falte por acreditar")


class InvoiceOut(BaseModel):
    id: UUID
    tipo: str
    estado: str
    numero_completo: str
    sale_id: UUID
    factura_origen_id: UUID | None
    fecha_emision: datetime
    subtotal: Decimal
    iva: Decimal
    total: Decimal
    cufe: str | None
    xml_url: str | None
    pdf_url: str | None
    intentos: int
    ultimo_error: str | None
    proximo_intento: datetime | None
    ambiente: str | None = None
    proveedor: str | None = None


class InvoiceDetail(InvoiceOut):
    datos: dict[str, Any]
    respuesta_proveedor: dict[str, Any] | None
