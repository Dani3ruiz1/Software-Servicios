# Catálogo de errores

> Generado por `scripts/export_openapi.py`. No editar a mano.

Todos los errores tienen la forma `{ "code": "...", "message": "...", "details": {} }`. El frontend decide por **`code`**, nunca por el texto de `message`.

| Código | HTTP | Qué significa | Qué hace el frontend |
|---|---|---|---|
| `VALIDATION_ERROR` | 422 | Algún dato enviado no es válido. | Mostrar `details.campos[]` junto a cada campo del formulario. |
| `UNAUTHORIZED` | 401 | Falta el token, es inválido o fue manipulado. | Ir a la pantalla de login. |
| `TOKEN_EXPIRED` | 401 | El access token (15 min) venció. | Llamar a `POST /auth/refresh` una vez y repetir la petición; si falla, ir al login. |
| `INVALID_CREDENTIALS` | 401 | Correo o contraseña incorrectos (mensaje igual para ambos casos). | Mostrar el mensaje; no revelar cuál falló. |
| `REFRESH_REUSED` | 401 | Un refresh token ya usado se presentó otra vez: se cerraron todas las sesiones de esa cadena. | Ir al login. |
| `TOTP_REQUIRED` | 401 | La cuenta tiene doble factor y falta el código. | Pedir el código de 6 dígitos y reenviar el login con `totp_code`. |
| `INVALID_TOTP` | 401 | El código de doble factor es incorrecto. | Pedirlo de nuevo. |
| `ACCOUNT_LOCKED` | 423 | Demasiados intentos fallidos: cuenta bloqueada 15 min (`details.bloqueado_hasta`). | Mostrar hasta cuándo. |
| `FORBIDDEN` | 403 | El rol del usuario no permite esta acción. | Ocultar la acción según `permisos` de `/auth/me`; si llega igual, mostrar aviso. |
| `MODULE_NOT_ENABLED` | 403 | El plan del negocio no incluye este módulo (`details.modulo`). | Ocultar el menú según `modulos_activos`; ofrecer mejorar el plan. |
| `TENANT_BLOCKED` | 402 | Suscripción vencida (402) o cuenta suspendida (403). `details.motivo`: vencimiento | manual | sin_suscripcion. | Mostrar pantalla de cuenta bloqueada con la fecha (`details.fecha_fin`). `/auth/me` sigue funcionando. |
| `USER_LIMIT_REACHED` | 409 | El plan no permite más usuarios activos (`details.usados/maximo`). | Mostrar el cupo y ofrecer mejorar el plan. |
| `NOT_FOUND` | 404 | El recurso no existe o no pertenece a este negocio. | Mostrar aviso o volver a la lista. |
| `CONFLICT` | 409 | La acción choca con el estado actual (duplicado, ya existe, tiene dependencias). | Mostrar `message`. |
| `SLOT_TAKEN` | 409 | Otra persona acaba de ocupar ese horario (carrera entre dos reservas). | Volver a consultar disponibilidad y ofrecer otro horario. |
| `INVALID_STATE_TRANSITION` | 409 | El estado actual no permite ese cambio (`details.permitidos`). | Refrescar el registro y mostrar los cambios válidos. |
| `SLOT_UNAVAILABLE` | 409 | Ese horario no está disponible (fuera de horario, bloqueado, cupos, anticipación). | Volver a consultar disponibilidad. |
| `STAFF_NOT_QUALIFIED` | 422 | El profesional no realiza todos los servicios elegidos. | Filtrar profesionales por servicio. |
| `PRICE_NOT_EDITABLE` | 422 | Ese servicio no permite cambiar el precio al cobrar. | Deshabilitar el campo precio según `precio_editable`. |
| `CASH_SESSION_REQUIRED` | 409 | No hay caja abierta y el negocio la exige para cobrar. | Llevar a abrir caja. |
| `STOCK_INSUFFICIENT` | 409 | Una salida de inventario pide más unidades de las que hay. | Mostrar `details.disponible` y ofrecer corregir la cantidad. |
| `CASH_SESSION_ALREADY_OPEN` | 409 | Ya hay una caja abierta. | Ir a la caja abierta (`GET /cash/sessions/current`). |
| `BANK_ENTITY_REQUIRED` | 422 | La transferencia exige la entidad bancaria. | Marcar el campo banco como obligatorio. |
| `PAYMENT_MISMATCH` | 422 | Los pagos no suman el total (`details.total/pagado/diferencia`). | Mostrar la diferencia. |
| `AI_LIMIT_REACHED` | 429 | Se alcanzó el límite mensual de mensajes de IA del plan. | Informar al administrador. |
| `RATE_LIMITED` | 429 | Demasiadas peticiones desde esta red. Cabecera `Retry-After` en segundos. | Esperar y reintentar con espera creciente. |
| `TOO_MANY_BOOKINGS` | 429 | Este teléfono ya tiene el máximo de reservas activas (`details.maximo`). | Sugerir cancelar una existente. |
| `INTEGRATION_NOT_CONFIGURED` | 409 | Falta configurar la integración (WhatsApp, IA o facturación). | Llevar a la pantalla de configuración. |
| `RESOLUTION_EXHAUSTED` | 409 | Se agotó el rango de numeración de facturas autorizado por la DIAN. | Avisar al administrador: solicitar nueva resolución. |
| `RESOLUTION_EXPIRED` | 409 | La resolución de numeración de la DIAN venció. | Avisar al administrador: solicitar nueva resolución. |
| `INVOICE_NOT_ALLOWED` | 409 | La factura/nota crédito no se puede emitir en este estado (venta sin pagar, ya facturada, exceso). | Mostrar `message`. |
| `WHATSAPP_WINDOW_CLOSED` | 409 | Pasaron más de 24 h desde el último mensaje del cliente: WhatsApp solo permite plantillas aprobadas. | Deshabilitar el cuadro de respuesta y explicar. |
| `WHATSAPP_SEND_FAILED` | 502 | WhatsApp (Meta) rechazó o no pudo entregar el mensaje. | Mostrar el motivo y permitir reintentar. |
| `CANCELLATION_WINDOW_CLOSED` | 409 | Ya pasó el límite para cancelar o reprogramar en línea (`details.horas_minimas`). | Mostrar el teléfono del negocio. |
| `INTERNAL_ERROR` | 500 | Error inesperado del servidor (ya registrado con el `X-Request-ID` de la respuesta). | Mostrar un aviso genérico y ofrecer el id de la petición a soporte. |

Notas: `TENANT_BLOCKED` responde 402 (vencimiento) o 403 (suspensión manual); `SLOT_TAKEN` y `SLOT_UNAVAILABLE` son 409; `RATE_LIMITED` incluye la cabecera `Retry-After`.
