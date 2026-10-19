"""Errores uniformes: { "code", "message", "details" }. El catálogo es el contrato con el frontend."""
import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("app.errors")

# código -> HTTP status por defecto
ERROR_CATALOG: dict[str, int] = {
    "VALIDATION_ERROR": 422,
    "UNAUTHORIZED": 401,
    "TOKEN_EXPIRED": 401,
    "INVALID_CREDENTIALS": 401,
    "REFRESH_REUSED": 401,
    "TOTP_REQUIRED": 401,
    "INVALID_TOTP": 401,
    "ACCOUNT_LOCKED": 423,
    "FORBIDDEN": 403,
    "MODULE_NOT_ENABLED": 403,
    "TENANT_BLOCKED": 402,
    "USER_LIMIT_REACHED": 409,
    "NOT_FOUND": 404,
    "CONFLICT": 409,
    "SLOT_TAKEN": 409,
    "INVALID_STATE_TRANSITION": 409,
    "SLOT_UNAVAILABLE": 409,
    "STAFF_NOT_QUALIFIED": 422,
    "PRICE_NOT_EDITABLE": 422,
    "CASH_SESSION_REQUIRED": 409,
    "STOCK_INSUFFICIENT": 409,
    "CASH_SESSION_ALREADY_OPEN": 409,
    "BANK_ENTITY_REQUIRED": 422,
    "PAYMENT_MISMATCH": 422,
    "AI_LIMIT_REACHED": 429,
    "RATE_LIMITED": 429,
    "TOO_MANY_BOOKINGS": 429,
    "INTEGRATION_NOT_CONFIGURED": 409,
    "RESOLUTION_EXHAUSTED": 409,
    "RESOLUTION_EXPIRED": 409,
    "INVOICE_NOT_ALLOWED": 409,
    "WHATSAPP_WINDOW_CLOSED": 409,
    "WHATSAPP_SEND_FAILED": 502,
    "CANCELLATION_WINDOW_CLOSED": 409,
    "INTERNAL_ERROR": 500,
}


# código -> (qué significa, qué debe hacer el frontend). Se publica en docs/ERRORES.md (generado) y una prueba exige que no falte ninguno.
ERROR_HELP: dict[str, tuple[str, str]] = {
    "VALIDATION_ERROR": ("Algún dato enviado no es válido.", "Mostrar `details.campos[]` junto a cada campo del formulario."),
    "UNAUTHORIZED": ("Falta el token, es inválido o fue manipulado.", "Ir a la pantalla de login."),
    "TOKEN_EXPIRED": ("El access token (15 min) venció.", "Llamar a `POST /auth/refresh` una vez y repetir la petición; si falla, ir al login."),
    "INVALID_CREDENTIALS": ("Correo o contraseña incorrectos (mensaje igual para ambos casos).", "Mostrar el mensaje; no revelar cuál falló."),
    "REFRESH_REUSED": ("Un refresh token ya usado se presentó otra vez: se cerraron todas las sesiones de esa cadena.", "Ir al login."),
    "TOTP_REQUIRED": ("La cuenta tiene doble factor y falta el código.", "Pedir el código de 6 dígitos y reenviar el login con `totp_code`."),
    "INVALID_TOTP": ("El código de doble factor es incorrecto.", "Pedirlo de nuevo."),
    "ACCOUNT_LOCKED": ("Demasiados intentos fallidos: cuenta bloqueada 15 min (`details.bloqueado_hasta`).", "Mostrar hasta cuándo."),
    "FORBIDDEN": ("El rol del usuario no permite esta acción.", "Ocultar la acción según `permisos` de `/auth/me`; si llega igual, mostrar aviso."),
    "MODULE_NOT_ENABLED": ("El plan del negocio no incluye este módulo (`details.modulo`).", "Ocultar el menú según `modulos_activos`; ofrecer mejorar el plan."),
    "TENANT_BLOCKED": ("Suscripción vencida (402) o cuenta suspendida (403). `details.motivo`: vencimiento | manual | sin_suscripcion.", "Mostrar pantalla de cuenta bloqueada con la fecha (`details.fecha_fin`). `/auth/me` sigue funcionando."),
    "USER_LIMIT_REACHED": ("El plan no permite más usuarios activos (`details.usados/maximo`).", "Mostrar el cupo y ofrecer mejorar el plan."),
    "NOT_FOUND": ("El recurso no existe o no pertenece a este negocio.", "Mostrar aviso o volver a la lista."),
    "CONFLICT": ("La acción choca con el estado actual (duplicado, ya existe, tiene dependencias).", "Mostrar `message`."),
    "SLOT_TAKEN": ("Otra persona acaba de ocupar ese horario (carrera entre dos reservas).", "Volver a consultar disponibilidad y ofrecer otro horario."),
    "INVALID_STATE_TRANSITION": ("El estado actual no permite ese cambio (`details.permitidos`).", "Refrescar el registro y mostrar los cambios válidos."),
    "SLOT_UNAVAILABLE": ("Ese horario no está disponible (fuera de horario, bloqueado, cupos, anticipación).", "Volver a consultar disponibilidad."),
    "STAFF_NOT_QUALIFIED": ("El profesional no realiza todos los servicios elegidos.", "Filtrar profesionales por servicio."),
    "PRICE_NOT_EDITABLE": ("Ese servicio no permite cambiar el precio al cobrar.", "Deshabilitar el campo precio según `precio_editable`."),
    "CASH_SESSION_REQUIRED": ("No hay caja abierta y el negocio la exige para cobrar.", "Llevar a abrir caja."),
    "STOCK_INSUFFICIENT": ("Una salida de inventario pide más unidades de las que hay.", "Mostrar `details.disponible` y ofrecer corregir la cantidad."),
    "CASH_SESSION_ALREADY_OPEN": ("Ya hay una caja abierta.", "Ir a la caja abierta (`GET /cash/sessions/current`)."),
    "BANK_ENTITY_REQUIRED": ("La transferencia exige la entidad bancaria.", "Marcar el campo banco como obligatorio."),
    "PAYMENT_MISMATCH": ("Los pagos no suman el total (`details.total/pagado/diferencia`).", "Mostrar la diferencia."),
    "AI_LIMIT_REACHED": ("Se alcanzó el límite mensual de mensajes de IA del plan.", "Informar al administrador."),
    "RATE_LIMITED": ("Demasiadas peticiones desde esta red. Cabecera `Retry-After` en segundos.", "Esperar y reintentar con espera creciente."),
    "TOO_MANY_BOOKINGS": ("Este teléfono ya tiene el máximo de reservas activas (`details.maximo`).", "Sugerir cancelar una existente."),
    "CANCELLATION_WINDOW_CLOSED": ("Ya pasó el límite para cancelar o reprogramar en línea (`details.horas_minimas`).", "Mostrar el teléfono del negocio."),
    "INTEGRATION_NOT_CONFIGURED": ("Falta configurar la integración (WhatsApp, IA o facturación).", "Llevar a la pantalla de configuración."),
    "RESOLUTION_EXHAUSTED": ("Se agotó el rango de numeración de facturas autorizado por la DIAN.", "Avisar al administrador: solicitar nueva resolución."),
    "RESOLUTION_EXPIRED": ("La resolución de numeración de la DIAN venció.", "Avisar al administrador: solicitar nueva resolución."),
    "INVOICE_NOT_ALLOWED": ("La factura/nota crédito no se puede emitir en este estado (venta sin pagar, ya facturada, exceso).", "Mostrar `message`."),
    "WHATSAPP_WINDOW_CLOSED": ("Pasaron más de 24 h desde el último mensaje del cliente: WhatsApp solo permite plantillas aprobadas.", "Deshabilitar el cuadro de respuesta y explicar."),
    "WHATSAPP_SEND_FAILED": ("WhatsApp (Meta) rechazó o no pudo entregar el mensaje.", "Mostrar el motivo y permitir reintentar."),
    "INTERNAL_ERROR": ("Error inesperado del servidor (ya registrado con el `X-Request-ID` de la respuesta).", "Mostrar un aviso genérico y ofrecer el id de la petición a soporte."),
}


class AppError(Exception):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None,
                 status: int | None = None, headers: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.code, self.message, self.details = code, message, details or {}
        self.status = status or ERROR_CATALOG[code]
        self.headers = headers


def _body(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"code": code, "message": message, "details": details or {}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, e: AppError) -> JSONResponse:
        return JSONResponse(_body(e.code, e.message, e.details), status_code=e.status, headers=e.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, e: RequestValidationError) -> JSONResponse:
        fields = [{"campo": ".".join(str(p) for p in err["loc"][1:]), "mensaje": err["msg"]}
                  for err in e.errors()]
        return JSONResponse(_body("VALIDATION_ERROR", "Datos inválidos", {"campos": fields}), 422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, e: StarletteHTTPException) -> JSONResponse:
        code = {401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND"}.get(e.status_code, "CONFLICT")
        return JSONResponse(_body(code, str(e.detail)), e.status_code)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, e: Exception) -> JSONResponse:
        log.exception("unhandled", exc_info=e)       # el detalle va al log, nunca al cliente
        return JSONResponse(_body("INTERNAL_ERROR", "Error interno"), 500)
