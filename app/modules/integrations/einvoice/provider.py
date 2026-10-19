"""Interfaz del proveedor tecnológico de facturación electrónica (autorizado por la DIAN) + un proveedor SIMULADO.

El resto del sistema solo conoce `EInvoiceProvider`. Conectar un proveedor real = escribir una clase con estos 3 métodos,
registrarla en `PROVIDERS` y pasar la prueba de contrato (`tests/test_phase8.py::test_provider_contract`).

Contrato:
  * `emit(doc)` debe ser IDEMPOTENTE por `doc["referencia"]` (reenviar el mismo documento no crea otro).
  * Un rechazo por datos (la DIAN dijo que no) es un RESULTADO `rechazada` con sus errores, no una excepción.
  * `ProviderTransient`: no se pudo saber (red, 5xx, 429) → se reintenta. `ProviderConfigError`: credenciales/ajustes malos → no se reintenta.
"""
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, Protocol

from app.modules.integrations.einvoice import rules


class ProviderTransient(Exception):
    """Fallo temporal: el documento NO quedó resuelto; hay que reintentar con el mismo número."""


class ProviderConfigError(Exception):
    """Credenciales o configuración inválidas: reintentar no sirve; requiere acción del administrador."""


@dataclass
class ProviderResult:
    estado: Literal["aceptada", "rechazada", "pendiente"]
    cufe: str | None = None                      # CUFE (factura) o CUDE (nota crédito) OFICIAL, calculado por el proveedor
    referencia: str | None = None                # id del documento en el proveedor (para consultar su estado)
    xml_url: str | None = None
    pdf_url: str | None = None
    errores: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


class EInvoiceProvider(Protocol):
    nombre: str

    async def validate(self) -> None: ...
    async def emit(self, doc: dict[str, Any]) -> ProviderResult: ...
    async def status(self, referencia: str, doc: dict[str, Any]) -> ProviderResult: ...


class SandboxProvider:
    """SIMULADO: no habla con la DIAN, no tiene validez fiscal. Solo existe en ambiente de habilitación para desarrollo y pruebas.
    Casos de prueba por el documento del adquirente: 8888888888 = caída temporal del proveedor, 9999999999 = rechazo."""
    nombre = "sandbox"

    def __init__(self, credenciales: dict[str, Any] | None, ambiente: str) -> None:
        if ambiente != "habilitacion":
            raise ProviderConfigError("El proveedor simulado solo puede usarse en ambiente de habilitación, nunca en producción")
        self.clave = str((credenciales or {}).get("clave_tecnica") or "clave-tecnica-simulada")
        self.ambiente = ambiente

    async def validate(self) -> None:
        return None

    async def emit(self, doc: dict[str, Any]) -> ProviderResult:
        doc_adq = str(doc["adquirente"]["documento"])
        if doc_adq == "8888888888":
            raise ProviderTransient("Proveedor simulado: caída temporal")
        if doc_adq == "9999999999":
            return ProviderResult("rechazada", errores=["FAK24: documento del adquirente inválido (simulado)"], raw={"simulado": True})
        t = doc["totales"]
        cufe = rules.cufe_reference(numero=doc["numero_completo"], fecha=datetime.fromisoformat(doc["fecha_emision"]), valor_factura=Decimal(t["subtotal"]),
                                    iva=Decimal(t["iva"]), total=Decimal(t["total"]), nit_emisor=doc["emisor"]["nit"], doc_adquirente=doc_adq,
                                    clave_tecnica=self.clave, ambiente=self.ambiente)
        return ProviderResult("aceptada", cufe=cufe, referencia=f"sbx-{doc['referencia']}", pdf_url=f"/api/v1/invoices/{doc['referencia']}/pdf",
                              raw={"simulado": True, "aviso": "SIN VALIDEZ FISCAL"})

    async def status(self, referencia: str, doc: dict[str, Any]) -> ProviderResult:
        return await self.emit(doc)               # idempotente por referencia


PROVIDERS: dict[str, type] = {"sandbox": SandboxProvider}


def get_provider(nombre: str, credenciales: dict[str, Any] | None, ambiente: str) -> EInvoiceProvider:
    cls = PROVIDERS.get(nombre)
    if cls is None:
        raise ProviderConfigError(f"Proveedor de facturación desconocido: {nombre}")
    return cls(credenciales, ambiente)       # type: ignore[no-any-return]
