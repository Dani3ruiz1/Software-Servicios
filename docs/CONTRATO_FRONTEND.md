# Contrato para el frontend

Fuente de verdad exacta: [`openapi.json`](../openapi.json) (cada endpoint con ejemplos de petición/respuesta y de errores) y [`postman_collection.json`](../postman_collection.json). Este documento explica **cómo usarlos**. Todas las rutas cuelgan de `/api/v1`.

## 1. Sesión

| Paso | Endpoint | Notas |
|---|---|---|
| Entrar | `POST /auth/login` `{tenant_slug, email, password, totp?}` | Devuelve `access_token` (15 min), `refresh_token`, `expires_in` (segundos). Super admin: `POST /auth/platform/login` (sin `tenant_slug`). |
| Usar | `Authorization: Bearer <access_token>` | El negocio sale del token; **nunca** mandes `tenant_id`. |
| Renovar | `POST /auth/refresh` `{refresh_token}` | **Rotativo**: cada uso devuelve un refresh nuevo y el anterior muere. Guarda siempre el último. Reusar uno viejo revoca toda la sesión (robo detectado) → pide login. |
| Salir | `POST /auth/logout` | Revoca la familia de tokens. |
| Olvidé mi clave | `POST /auth/forgot-password` (siempre 202), `POST /auth/reset-password` | No revela si la cuenta existe. |

Estrategia recomendada: al recibir `401 TOKEN_EXPIRED`, llama **una vez** a `/auth/refresh` (serializa las peticiones concurrentes para que solo una renueve), repite la petición original; si el refresh falla → pantalla de login.

Errores de login: siempre `INVALID_CREDENTIALS` (401) con el mismo mensaje (no distingue negocio/correo/clave). Tras 5 fallos: `ACCOUNT_LOCKED` (423) con `details.bloqueado_hasta`. Con 2FA activo y sin código: pide `totp`.

## 2. `GET /auth/me` — la pantalla de arranque del frontend

Devuelve `{id, email, nombre, rol, totp_habilitado, tenant{nombre, slug, zona_horaria, modo, estado, bloqueado, motivo_bloqueo, fecha_fin}, modulos_activos[], permisos[], impersonado}`.

- **Pinta el menú con `modulos_activos`** y los botones con `permisos` (`"reports:read"`, `"sales:create"`…). Es solo UX: el servidor valida de todos modos y responde `403 FORBIDDEN` / `MODULE_NOT_ENABLED`.
- **Negocio bloqueado** (suscripción vencida): `/auth/me` **sigue funcionando** con `tenant.bloqueado=true` y `motivo_bloqueo`; el resto de rutas responden `403 TENANT_BLOCKED`. Muestra una pantalla de "suscripción vencida" con `fecha_fin` y contacto, no un error genérico.
- **Impersonación** (soporte): `impersonado=true` → muestra una barra fija "Estás viendo el negocio X como soporte". Ese token no renueva y no puede cambiar credenciales (`FORBIDDEN`).
- Zona horaria del negocio: `tenant.zona_horaria` (p. ej. `America/Bogota`).

## 3. Convenciones

- **Errores**: siempre `{code, message, details}` (ver `docs/ERRORES.md`). Decide por `code`; `message` está en español y se puede mostrar. `422 VALIDATION_ERROR` trae `details.campos[] = {campo, mensaje}` para marcar el campo exacto. `429 RATE_LIMITED` trae cabecera `Retry-After`.
- **Listas**: `?page=1&page_size=20&sort=-campo` → `{items, page, page_size, total}`. Filtros según endpoint (ver OpenAPI).
- **Fechas**: ISO-8601 con zona (`2026-10-20T10:00:00-05:00`); las fechas sin hora, `YYYY-MM-DD`. Muestra siempre en la zona del negocio.
- **Dinero**: **texto decimal** (`"35000.00"`), COP. No uses `float`; usa una librería decimal o enteros.
- **Borrado**: las entidades con historial se desactivan/anulan, no se borran (el ID sigue válido en reportes y facturas).
- **Cabecera `X-Request-ID`**: viene en toda respuesta; muéstrala en "algo salió mal" para que soporte encuentre el log.
- **Concurrencia de agenda**: dos personas pueden intentar el mismo hueco; el perdedor recibe `409` (`SLOT_TAKEN`): vuelve a pedir disponibilidad y ofrece el siguiente.

## 4. Reserva pública (sin login) — `/public/{slug}/…`

1. `GET /public/{slug}/config` → datos del negocio, servicios activos, campos extra del formulario y reglas (anticipación, cancelación).
2. `GET /public/{slug}/availability?fecha_desde=YYYY-MM-DD&service_ids=…[&staff_id=…]` → `{dias:[{fecha, horarios:[ISO…]}]}`.
3. `POST /public/{slug}/bookings` `{service_ids, inicio, staff_id?, cliente{nombre, telefono, email?}, consentimiento_datos:true, respuestas{}, website:""}` → `201` con la cita y un **`token`**. Teléfono: se normaliza (acepta `300 123 4567`).
4. El cliente gestiona su cita con ese `token` (enlace en el correo/WhatsApp): `GET /public/{slug}/bookings/{token}` (ver), `POST …/{token}/cancel` y `POST …/{token}/reschedule`.

Errores a tratar: `SLOT_TAKEN` / `SLOT_UNAVAILABLE` (el hueco ya no está: vuelve a pedir disponibilidad), `STAFF_NOT_QUALIFIED`, `CANCELLATION_WINDOW_CLOSED` (dentro del plazo mínimo de cancelación), `TOO_MANY_BOOKINGS` (límite por cliente), `422` datos, `429 RATE_LIMITED` (honra `Retry-After`). El campo `website` es una trampa anti-bots: envíalo **vacío** y oculto; si un bot lo llena, la reserva se descarta.

## 5. Piezas que el frontend debe cuidar

- **Facturación electrónica**: hoy el proveedor es **simulado**; todo documento sale marcado "SIN VALIDEZ FISCAL". Muestra ese aviso mientras `proveedor=simulado`.
- **WhatsApp**: fuera de la ventana de 24 h solo se envían plantillas aprobadas; la bandeja muestra el estado (`enviada`, `fallida`, `escalada a humano`).
- **Permisos por rol**: no codifiques roles; usa `permisos[]`. Un rol nuevo no exige cambios de frontend.

## 6. Paneles del Super Admin (`/app/plataforma.html`)

Todos exigen sesión de plataforma (un negocio recibe 403). Solo lectura salvo planes, módulos y negocios.

| Panel | Endpoint | Notas |
|---|---|---|
| Tablero | `GET /platform/analytics?dias=7..365` | Solo agregados; ningún dato de clientes. |
| Cobros | `GET /platform/cobros?desde&hasta&metodo&page` | Pagos de todos los negocios + `resumen` (mes actual/anterior, vencidos). |
| Usuarios | `GET /platform/usuarios?q&rol&tenant_id&page` | Sin hash, 2FA ni tokens; marca `bloqueado` y `con_2fa`. |
| Auditoría | `GET /platform/auditoria?accion&actor_tipo&tenant_id&page` | Sin `antes/despues` (pueden contener datos personales). |
| Salud | `GET /platform/salud` | Cola de mensajes, integraciones, cuentas bloqueadas, admins sin 2FA, tamaño de la BD. |
| Módulos | `GET/PATCH /platform/modules` | Precio base; lo ya pactado no cambia. |

Acciones sobre usuarios (siempre auditadas, nunca registran la clave): `POST /platform/usuarios/{id}/desbloquear`, `PUT /platform/usuarios/{id}/activo {activo}` (al desactivar cierra sus sesiones) y `POST /platform/usuarios/{id}/restablecer-clave` (devuelve `clave_temporal` una sola vez, cierra sesiones y desbloquea). Un access token ya emitido sigue valiendo hasta 15 min.
