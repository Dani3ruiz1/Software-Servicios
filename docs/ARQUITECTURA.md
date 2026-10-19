# Agenda SaaS — Fase 1: Arquitectura

## 1. Decisiones de diseño

| # | Decisión | Por qué |
|---|----------|---------|
| 1 | **Una BD, `tenant_id` en toda tabla de negocio** + RLS (`sql/rls.sql`) con `app.current_tenant` | Barato de operar; RLS es la red de seguridad si un repositorio olvida el filtro. Fail-closed: sin variable, 0 filas. |
| 2 | **FK compuestas `(tenant_id, x_id)`** hacia padres con `UNIQUE (tenant_id, id)` | Las FK no pasan por RLS: sin esto un INSERT podría enlazar una cita del tenant A con un cliente del tenant B. |
| 3 | **Dos rutas de acceso a RLS**: `app.current_tenant` (requests de tenant) y `app.is_platform='on'` (Super Admin, Celery, webhooks, resolución de login) | Hay flujos legítimamente globales. Solo código del servidor puede fijarlas; el tenant sale del JWT. |
| 4 | **Rol `agenda_app`** (NOSUPERUSER, NOBYPASSRLS) para la API; Alembic usa superusuario | Un superusuario ignora RLS: la API nunca debe conectarse con él. |
| 5 | **Enums como `CHECK`** | Añadir un valor es un `ALTER` trivial; `CREATE TYPE` complica migraciones. |
| 6 | **Anti doble reserva en BD**: `EXCLUDE USING gist (staff_id =, tstzrange(inicio,fin,'[)') &&)` parcial (excluye canceladas/no_asistio/borradas) | Ninguna condición de carrera de la app puede violarlo → la API traduce el error `23P01` a 409 `SLOT_TAKEN`. `[)` permite citas consecutivas. |
| 7 | **Login = `tenant_slug + email + password`** | El email es único *por tenant*, no global; el slug identifica el tenant antes de autenticar. |
| 8 | **Refresh tokens rotativos con `familia_id`** (se guarda el hash) | Logout real y detección de reutilización de un refresh robado. |
| 9 | **Numeración de comprobantes con `document_sequences`** (`UPDATE … RETURNING` en la misma transacción) | Sin huecos ni duplicados por tenant. |
| 10 | **Precio plan congelado** en `tenant_subscriptions.precio_plan_aplicado` | Un cambio de precio del plan no altera lo que ya se pactó. |
| 11 | **Alembic 0001 ejecuta `schema.sql` + `rls.sql`** | Una sola fuente de verdad ⇒ migración y pgAdmin son equivalentes por construcción. Desde Fase 2, cambios nuevos = migraciones normales. |
| 12 | **Alembic síncrono (psycopg), API asíncrona (asyncpg)** | asyncpg no ejecuta scripts multi-sentencia; migrar no necesita ser async. |

### Supuestos (puedes corregirlos)
- **Una sola caja abierta a la vez por tenant** (índice único parcial). Si un negocio necesita una caja por recepcionista, se cambia a `(tenant_id, responsable_id)`.
- **Días de semana 0 = lunes**. Horarios en hora local del tenant; citas y bloqueos en `timestamptz`.
- **IVA**: se guarda por ítem; el porcentaje viene de `services.porcentaje_iva`. Colombia general 19 %.
- **Módulos "núcleo"** (no son módulos vendibles): usuarios, staff, clientes, catálogo, ventas básicas. Modo independiente = plan con `es_independiente` (1 usuario, solo módulo `calendar`).
- **`inventory`** existe como módulo vendible pero **no tiene tablas**: tu modelo de datos no las define. Queda reservado; ¿lo diseño en una fase posterior?
- **Límite de IA**: `plans.max_mensajes_ia_mes`; se cuenta con `messages` donde `rol='ia'` (índice parcial listo).
- **Estado `por_vencer`**: fecha_fin dentro de ≤7 días; `vencida`: pasó fecha_fin (gracia de lectura); `bloqueada`: pasó la gracia. Gracia por defecto: **3 días** → decisión tuya.

## 2. ERD

```mermaid
erDiagram
  plans ||--o{ plan_modules : incluye
  modules ||--o{ plan_modules : en
  plans ||--o{ tenant_subscriptions : contrata
  tenants ||--o{ tenant_subscriptions : tiene
  tenants ||--o{ tenant_modules : activa
  modules ||--o{ tenant_modules : es
  tenants ||--o{ subscription_payments : paga
  platform_users ||--o{ subscription_payments : registra
  business_types ||--o{ tenants : clasifica
  business_types ||--o{ business_type_templates : plantilla

  tenants ||--o{ users : emplea
  users |o--o| staff_profiles : perfil
  staff_profiles ||--o{ staff_schedules : horario
  staff_profiles ||--o{ schedule_blocks : bloqueos
  staff_profiles ||--o{ staff_services : realiza
  services ||--o{ staff_services : es
  service_categories ||--o{ services : agrupa

  customers ||--o{ appointments : reserva
  staff_profiles |o--o{ appointments : atiende
  appointments ||--o{ appointment_services : detalla
  services ||--o{ appointment_services : en

  appointments |o--o| sales : origina
  customers |o--o{ sales : compra
  staff_profiles |o--o{ sales : vende
  cash_sessions ||--o{ sales : registra
  cash_sessions ||--o{ cash_movements : movimientos
  users ||--o{ cash_sessions : responsable
  sales ||--|{ sale_items : contiene
  sales ||--|{ payments : "pagos divididos"
  sales ||--o{ invoices : comprobante
  invoices |o--o{ invoices : "nota crédito"

  tenants ||--|| booking_settings : reservas
  tenants ||--o{ booking_form_fields : formulario
  tenants ||--o{ availability_rules : cupos
  tenants ||--o{ integrations : conecta
  tenants ||--o| invoice_settings : facturacion
  customers |o--o{ conversations : habla
  conversations ||--o{ messages : mensajes
  tenants ||--o{ notifications : avisa
  tenants ||--o{ audit_logs : audita
```

Cada tabla de negocio además lleva `tenant_id → tenants` (omitido en el diagrama por legibilidad).

## 3. Cómo ejecutar

**Docker (dev):** `cp .env.example .env` → `docker compose up --build`. Con volumen vacío, Postgres carga `schema → rls → seed` solo.
API: <http://localhost:8000/docs> · pgAdmin: <http://localhost:5050> (servidor: host `postgres`, puerto 5432).

**pgAdmin manual:** crear BD vacía → Query Tool → ejecutar `sql/schema.sql`, `sql/rls.sql`, `sql/seed.sql` (en ese orden, como superusuario).

**Alembic (producción):** `alembic upgrade head` carga schema + rls en una BD vacía. No combinar con la carga automática de initdb sobre la misma BD (usar una u otra).

Super admin de desarrollo: `superadmin@agenda-saas.co` / `Cambiar.Ya.123` — **cambiar de inmediato**.

## 4. Estructura
`app/core` (config, security, db, tenancy, errores, logging) · `app/modules/<módulo>/{router,schemas,models,service,repository}.py` · `app/workers` · `sql/` · `alembic/` · `tests/`. Los archivos de módulo están vacíos a propósito: se llenan fase por fase.

## 5. Criterios de aceptación de la Fase 1
- [x] `schema.sql`, `rls.sql`, `seed.sql` pasan el parser de PostgreSQL (96 + 14 + 6 sentencias).
- [ ] **Pendiente de ejecutar en Postgres real**: carga limpia en PG16 y `INSERT` solapado que dispare `23P01`. (Docker no estaba disponible al escribir esto.)
- [x] Todas las tablas de negocio tienen `tenant_id`, índices en FKs y `(tenant_id, …)`.
- [x] `docker-compose.yml` define api, postgres, pgadmin, redis, worker, beat.
- [x] `/health` y `/docs` en el esqueleto FastAPI.

## 6. Riesgos y decisiones tuyas
1. **Facturación DIAN**: elegir proveedor tecnológico (Alegra, Siigo, Factus, etc.) antes de la Fase 8; cambia el adaptador y el costo.
2. **Meta WhatsApp**: requiere cuenta Business verificada y plantillas aprobadas para mensajes fuera de la ventana de 24 h (recordatorios).
3. **Proveedor de IA** (Fase 7): ¿Claude u otro? Define costo por mensaje y por tanto los límites de plan.
4. **Gracia de bloqueo** (3 días por defecto) y política de qué ve un tenant bloqueado (¿solo lectura o nada?). Supuesto actual: 402 en todo salvo `/me` y pago.
5. **Roles BD**: la separación tenant/plataforma en la BD usa un solo rol + variable de sesión; si quieres defensa más fuerte, dos roles (uno con BYPASSRLS solo para plataforma).
6. **Ley 1581**: se guarda consentimiento + fecha + versión del texto; falta definir el texto legal y la política de retención/eliminación.
7. **Respaldos**: `pg_dump` programado se documenta en Fase 9; hoy solo hay volumen Docker.
8. **Estética pedida** ("visualmente distinta, auténtica, amigable"): es de frontend. Del lado backend ya queda soportada vía `booking_settings` (colores, logo, textos por negocio).

---

# Fase 2 — Core, auth, RBAC, tenancy + RLS, errores, auditoría

## Decisiones
- **Tenant desde el JWT** (`core/tenancy.py`): `get_principal` decodifica el token firmado; `get_db` abre una transacción con `set_config('app.current_tenant', …, true)` (local a la transacción → no queda "pegado" en el pool). Sin contexto, RLS devuelve 0 filas.
- **Login** = `tenant_slug + email + password`. La resolución del tenant corre en modo plataforma (aún no hay tenant); el JWT resultante ya lleva `tid`.
- **Anti fuerza bruta**: 5 intentos (`LOGIN_MAX_ATTEMPTS`) → bloqueo 15 min (`ACCOUNT_LOCKED`, 423). Contadores y auditoría se confirman aunque el login falle. Hash dummy cuando el usuario no existe (igual latencia). Mensaje idéntico para correo/clave erróneos.
- **Refresh rotativo** con familia: reutilizar un refresh ya usado revoca toda la familia (`REFRESH_REUSED`). Cambiar/restablecer contraseña revoca todas las sesiones.
- **2FA TOTP opcional para admins** (`/auth/2fa/setup` → `/auth/2fa/enable`); secreto cifrado con Fernet.
- **RBAC**: matriz rol→permisos en `core/rbac.py`; `/me` devuelve `permisos` y `modulos_activos` para armar el menú. `require_permission("x")` protege rutas.
- **Auditoría**: `audit.service.record()` escribe en la misma transacción del cambio; redacta campos sensibles; la tabla es append-only para el rol de la API.
- **Errores**: `{code,message,details}` con catálogo en `GET /api/v1/meta/error-codes`; los 500 no filtran detalles; `X-Request-ID` en cada respuesta y en logs JSON.
- Cambio a Fase 1: `platform_users` ganó `totp_habilitado`, `intentos_fallidos`, `bloqueado_hasta`.

## Endpoints (`/api/v1/auth`)
`POST login · platform/login · refresh · logout(204) · change-password(204) · forgot-password(202) · reset-password(204) · 2fa/setup · 2fa/enable` · `GET me`.

## Pruebas
`pytest tests` → **10 pasan** (hash, JWT manipulado/expirado/alg=none, Fernet/TOTP, RBAC, forma de errores, catálogo, request-id).
`tests/test_tenant_isolation.py` (**7 pruebas, no ejecutadas**: requieren Postgres): lectura/escritura/update/delete cruzados, sin contexto = 0 filas, FK compuesta bloquea enlace entre tenants incluso en modo plataforma, solapamiento rechazado (`ex_appointments_no_overlap`), consecutivas permitidas, 10 reservas concurrentes → solo 1 gana.

## Criterios de aceptación
- [x] Tenant solo desde JWT; token manipulado → 401.
- [x] Errores uniformes y catálogo publicado.
- [x] Login, refresh rotativo, logout, cambio/recuperación de contraseña, 2FA, `/me`.
- [ ] **Pendiente de ejecutar contra Postgres real**: pruebas de aislamiento y flujo auth end-to-end (Docker no disponible).

## Riesgos / decisiones tuyas
1. **Rate limiting por IP** (Redis) queda para la Fase 9; hoy solo hay bloqueo por cuenta.
2. **Envío del correo de recuperación**: depende del módulo notifications; en desarrollo el token se escribe en el log. ¿Qué proveedor de correo (SMTP, SES, Resend)?
3. **Impersonación** (Super Admin) se implementa en Fase 3 con auditoría.
4. Faltan los chequeos de suscripción (`TENANT_BLOCKED`) y `require_module` → Fase 3 (los ganchos están en `get_db`).
5. Las pruebas de BD usan el rol de la API; correrlas solo en una BD desechable.

---

# Fase 3 — Plataforma: tenants, planes, módulos, precios, pagos, bloqueo, impersonación

## Decisiones
- **Bloqueo evaluado en cada request** (`core/tenancy.py::ensure_tenant_active`, dentro de `get_db`): bloquea si `bloqueo_manual`, si no hay suscripción vigente, o si `fecha_fin + gracia < hoy` (hoy = fecha local del tenant). No depende del job → un pago desbloquea **al instante**. Error `TENANT_BLOCKED`: 402 por vencimiento, 403 por suspensión manual; `details.motivo` = `vencimiento|manual|sin_suscripcion`.
- **Gracia** configurable: `SUBSCRIPTION_GRACE_DAYS` (por defecto **0**, como pide la regla 1). Con gracia > 0 el negocio queda `vencida` (con acceso) y luego `bloqueada`.
- **`tenants.bloqueo_manual`** (nuevo en schema.sql): suspender/bloquear lo activa; ni pagos ni el job lo revierten, solo `POST …/estado/activar`.
- **`/auth/me` y login/refresh/logout NO se bloquean** (`get_db_unrestricted`): el frontend necesita `tenant.bloqueado`, `motivo_bloqueo` y `fecha_fin` para pintar la pantalla de "cuenta vencida".
- **`require_module("cash")`** disponible en `core/tenancy.py` → `403 MODULE_NOT_ENABLED` (se usará en los routers de las Fases 4-6).
- **Precio**: total = `precio_plan_aplicado` (congelado al contratar) + adicionales con `precio_override` (si es `null` → precio base *vigente* del módulo; `0` = regalo). Cambiar el precio de un plan no toca suscripciones existentes; cambiar el precio base de un módulo sí afecta a los adicionales sin override.
- **Cambio de plan**: sincroniza módulos (los del plan nuevo → `origen=plan`; los del plan viejo que ya no están → desactivados; adicionales se conservan) y rechaza con `USER_LIMIT_REACHED` si hay más usuarios activos que `max_usuarios`. Plan independiente ⇒ `tenants.modo='independiente'`.
- **Pagos**: extienden `fecha_fin` hasta `periodo_hasta` (si es posterior) y recalculan el estado. Un pago de un periodo ya vencido no desbloquea.
- **Job Celery** `daily_subscription_check` (beat 11:00 UTC = 06:00 Bogotá): una sentencia SQL actualiza estados por tenant en su zona horaria + inserta avisos a admins a 7 y 3 días en `notifications` (idempotente por fecha_fin/días). El envío real del correo lo hará el módulo de notificaciones.
- **Impersonación**: `POST /platform/tenants/{id}/impersonate` → JWT de 30 min como primer admin del negocio, claim `imp` = super admin, **sin refresh**, no puede cambiar contraseña ni 2FA, puede entrar a tenants bloqueados (soporte). Inicio auditado; `/me` devuelve `impersonado: true` para mostrar un banner.
- Todos los cambios de plataforma escriben en `audit_logs` (antes/después) en la misma transacción.

## Endpoints (`/api/v1/platform`, solo Super Admin; el guard corre antes de abrir la BD)
`GET dashboard` · `GET/PATCH modules[/{codigo}]` · `GET/POST plans`, `PATCH plans/{id}` ·
`GET/POST tenants`, `GET/PATCH tenants/{id}` · `POST tenants/{id}/estado/{activar|suspender|bloquear}` ·
`PUT tenants/{id}/plan` · `PUT tenants/{id}/modules/{codigo}` · `GET tenants/{id}/billing` ·
`GET/POST tenants/{id}/payments` · `POST tenants/{id}/impersonate`.
Listados: `?page&page_size&sort=-campo` → `{items,page,page_size,total}`; tenants admite `q` y `estado`.

## Pruebas
`pytest tests` → **28 pasan** (estados por días y gracia, desglose de precio con override/0, `evaluate_access`, token de impersonación, 403 a usuarios de tenant en rutas de plataforma, OpenAPI se construye).
`tests/test_platform_flow.py` (**6 pruebas, no ejecutadas**, requieren Postgres con seed): alta completa de negocio (admin, módulos, servicios de ejemplo), vencimiento bloquea y pago desbloquea al instante, suspensión manual sobrevive a pagos y job, módulo adicional con precio manual, job idempotente de estados/avisos, aislamiento de suscripciones.

## Criterios de aceptación
- [x] Reglas 1-4 (bloqueo, precio total, módulos, límite de usuarios en cambio de plan) implementadas y cubiertas por pruebas sin BD o escritas para BD.
- [x] Dashboard con activas / por vencer / vencidas / suspendidas y MRR.
- [ ] **Pendiente de ejecutar contra Postgres real** (sumando las 7 de Fase 2): `pytest tests` con la BD levantada debe dar 0 skipped.

## Riesgos / decisiones tuyas
1. **MRR** cuenta `activa`, `por_vencer` y `vencida`; excluye suspendidas/bloqueadas. ¿Lo quieres distinto?
2. El límite `USER_LIMIT_REACHED` al **crear** usuarios se aplica en la Fase 4 (aquí solo al cambiar de plan).
3. La consulta de acceso añade 1 query por request; si pesa, se cachea 30-60 s en Redis (el desbloqueo dejaría de ser instantáneo → decisión de producto).
4. Plan "sin suscripción" tras `assign_plan` queda vigente solo hoy: obliga a registrar un pago.
5. Los precios semilla (COP) son de ejemplo.

---

# Fase 4 — Usuarios, staff, clientes, catálogo, agenda y disponibilidad

## Decisiones
- **Límite de usuarios (regla 4)**: `POST /users` (y reactivar) cuenta usuarios activos no borrados contra `plans.max_usuarios` de la suscripción vigente. Se serializa por tenant con `pg_advisory_xact_lock` (sin él, dos altas simultáneas pasan el límite). Borrar/desactivar libera el cupo. `GET /users/seats` → `{usados, maximo, disponibles}` para mostrar "3/5". Siempre queda ≥1 admin activo; no puedes borrarte a ti mismo. Desactivar o cambiar la contraseña revoca sus sesiones.
- **Rol profesional**: al crear un usuario `profesional` se crea su `staff_profile` (se puede omitir). Solo ve **sus** citas (lista, calendario, detalle → 404 si no es suya) y solo puede pasar sus citas a `en_atencion`/`atendida`.
- **Motor de disponibilidad (`appointments/availability.py`, funciones puras)**: horario semanal (tramos; el almuerzo son dos tramos) − bloqueos − citas (buffer simétrico) − cupos simultáneos − citas **sin profesional** (consumen capacidad: no se promete más de lo que hay) con anticipación mínima/máxima. Aritmética en UTC; los tramos son hora local del tenant. Rejilla por defecto 30 min (o `duracion_turno_min` de `availability_rules`). Buffer/anticipación salen de `booking_settings` si existe (Fase 6); si no, 0 h / 60 días / 0 min.
- **`availability_rules` (cupos)**: aplican las reglas del negocio entero (`staff_id` NULL), con `service_id` NULL (todas las citas) o igual a un servicio pedido: máximo N citas simultáneas en su alcance. Reglas por profesional → pendiente (ver riesgos).
- **Crear cita**: recepción (`strict=False`) puede agendar fuera de horario; **reservas públicas/WhatsApp/IA (Fases 6-7) llamarán `strict=True`** y exigen disponibilidad real. En ambos casos la BD impide el solape del mismo profesional: el insert corre en un *savepoint*; conflicto → se prueba el siguiente candidato (asignación automática, ordenados por menos citas ese día) → si no queda ninguno, `409 SLOT_TAKEN`. Sin `staff_id` ni `asignar_automatico` la cita queda "en recepción".
- **Máquina de estados**: pendiente→confirmada→en_atencion→atendida; cancelar desde pendiente/confirmada/en_atencion; `no_asistio` solo desde pendiente/confirmada y solo después de la hora de inicio; estados finales no se reabren (`INVALID_STATE_TRANSITION`). Cancelada/no_asistio liberan el hueco.
- **Mover** (`POST …/move`): conserva la duración, permite reasignar profesional; solo pendientes/confirmadas. Servicios de una cita no se editan: se cancela y se crea otra (precio pactado queda histórico).
- **Bloqueos** no cancelan citas: devuelven `citas_afectadas` para que recepción las reprograme.
- **Clientes**: consentimiento (Ley 1581) guarda fecha y versión del texto (`PRIVACY_POLICY_VERSION`); otorgar/revocar queda en auditoría. Borrado lógico (historial intacto). Historial con contadores (atendidas, canceladas, no-shows); las ventas se suman en la Fase 5.
- **Catálogo**: `aplica_iva=false ⇒ 0 %`; `aplica_iva=true` sin porcentaje ⇒ 19 %. No se borra una categoría con servicios.
- **Defensa en profundidad**: `core/repository.py` obliga el filtro `tenant_id` + `deleted_at` en cada consulta, además de RLS y FK compuestas. (Los repositorios viven como `core/repository.py` + consultas en cada `service.py`; no hay `repository.py` por módulo para no duplicar código.)
- Todo `/appointments/*` exige `require_module("calendar")`.

## Endpoints nuevos (`/api/v1`)
`users` (CRUD, `/seats`) · `staff` (CRUD, `PUT /{id}/services`, `GET|PUT /{id}/schedule`, `GET|POST /{id}/blocks`, `DELETE /{id}/blocks/{bid}`) · `customers` (CRUD + `q`, `/{id}/history`) · `catalog/categories` y `catalog/services` (CRUD, filtros `q`, `category_id`, `activo`) · `appointments` (`GET /availability`, `GET /calendar?vista=dia|semana|mes&fecha=`, lista paginada, `POST`, `PATCH`, `POST /{id}/status`, `POST /{id}/move`).
Fechas con zona horaria; la agenda y la disponibilidad se devuelven en la zona del negocio.

## Pruebas
`pytest tests` → **57 pasan, 20 omitidas** (necesitan Postgres). Nuevas sin BD: motor de disponibilidad (13: rejilla, almuerzo, buffer, bloqueos, anticipación, cupos, citas sin profesional, ranking), **todas** las rutas protegidas rechazan anónimos (barrido automático de OpenAPI, ≥60 rutas), RBAC por rol, validaciones de horario/bloqueo/cita, rangos de calendario, IVA y cupos.
`tests/test_phase4_flow.py` (**7, no ejecutadas**): `USER_LIMIT_REACHED`; 8 altas concurrentes → exactamente 4; último admin protegido; 6 reservas simultáneas → 1 gana y el resto `SLOT_TAKEN`; asignación automática (menos citas y *fallback*); disponibilidad refleja reservar/cancelar y estados; profesional ve solo lo suyo.

## Criterios de aceptación
- [x] Reglas 4, 5 (vía plan independiente de 1 usuario), 7 y 8 implementadas; regla 3 aplicada a agenda.
- [x] Paginación, filtros y orden consistentes; errores uniformes.
- [ ] **Pendiente contra Postgres real**: las 20 pruebas omitidas (Fases 2-4) deben pasar con la BD levantada.

## Riesgos / decisiones tuyas
1. **Rejilla por defecto de 30 min**: ¿prefieres 15 o la duración del servicio? Se cambia con `availability_rules.duracion_turno_min` o un default.
2. **Cupos por profesional** (p. ej. "Ana atiende 2 a la vez") no están soportados; el anti-solape de la BD es estricto por profesional. Si lo necesitas, cambia el `EXCLUDE` de la BD.
3. **Buffer solo en la app**: la BD no lo conoce (solo evita solape exacto); recepción puede saltárselo a propósito.
4. **Cambios de servicios en una cita ya creada**: no se permiten; ¿lo quieres (recalculando duración y revalidando)?
5. **Duplicados de clientes** (mismo teléfono) no se detectan aún; conviene decidirlo antes de WhatsApp (Fase 7), donde el teléfono identifica al cliente.
6. **Horario de verano**: Colombia no lo tiene; el motor trabaja en UTC y es seguro para otras zonas, pero no hay pruebas con DST.


---

# Verificación contra Postgres real (cierre de las Fases 1-4)

Se probó en un PostgreSQL 18 local desechable (Docker Desktop no arrancó en esta máquina; el contenedor `postgres:16` de `docker-compose.yml` **sigue sin probarse**, y tampoco `alembic upgrade`).

- `schema.sql`, `rls.sql`, `seed.sql` cargan sin errores con `ON_ERROR_STOP`.
- `pytest tests` → **0 omitidas** (las 20 que faltaban ahora corren), todas verdes: aislamiento RLS (lectura/escritura/update/delete cruzados), FK compuestas, anti-solape, 10 reservas simultáneas → 1 gana, 8 altas concurrentes de usuarios → exactamente 4, bloqueo/desbloqueo por pago, job idempotente, profesional que solo ve lo suyo.
- Recorrido HTTP completo (login, crear negocio, `/me`, refresh rotativo con detección de reuso, suspender/activar, impersonación, bloqueo por 5 intentos, tenant→plataforma = 403) verificado a mano.

## Bugs reales que solo apareció al ejecutar (ya corregidos)
1. **El super admin sembrado no podía iniciar sesión**: `admin@plataforma.local` usa un dominio reservado que rechaza el validador de correo. Ahora es `superadmin@agenda-saas.co` (cámbialo en producción).
2. **Login → 500 si el host del cliente no es una IP** (socket Unix, proxy raro): la columna de auditoría es `inet`. `core/http.py::client_ip` valida y devuelve `None` en ese caso.
Ambos tienen prueba de regresión.

## Para repetirlo
```
initdb -D <dir> -U postgres ...; pg_ctl -D <dir> -o "-p 55432" start
psql -d agenda -f sql/schema.sql -f sql/rls.sql -f sql/seed.sql      # como superusuario
DATABASE_URL=postgresql+asyncpg://agenda_app:agenda_app_dev@127.0.0.1:55432/agenda pytest tests
```

---

# Fase 5 — Ventas, pagos, caja y reportes

## Decisiones
- **Dos formas de vender**: venta libre (`items`) o **convertir una cita** (`appointment_id`: cliente, profesional e ítems salen de ella; el precio original es el **pactado al agendar**). Con `pagos` se cobra en el acto; sin `pagos` queda *abierta* y se cobra después con `POST /sales/{id}/pay`. Cobrar una cita la pasa a `atendida`; anular la devuelve a `en_atencion` (se puede cobrar de nuevo).
- **Precio editable (regla 6)**: `precio_cobrado` solo difiere de la lista si `services.precio_editable`; si no → `PRICE_NOT_EDITABLE`. Siempre se guarda `precio_original` y el reporte muestra el descuento. Los ítems libres (p. ej. una gaseosa) no tienen precio de lista que proteger.
- **IVA**: el precio de lista **no** incluye IVA; se suma encima (`total = subtotal + iva + propina`, además lo exige un CHECK en la BD). Redondeo a centavos, mitad hacia arriba.
- **Pago dividido**: varios `pagos`; la suma debe ser **exactamente** el total (`PAYMENT_MISMATCH`, con la diferencia). Transferencia sin banco → rechazada en el schema, en el servicio y por CHECK en la BD (3 barreras).
- **Caja (regla 9)**: se exige caja abierta **al cobrar** (no al abrir una venta) si el negocio lo tiene configurado **y** tiene el módulo `cash`; el plan independiente vende sin caja. Una sola caja abierta por negocio. La venta cae en la caja donde se **cobró** (no donde se creó). Cobrar usa un candado compartido y cerrar uno exclusivo: no se puede cobrar en una caja que se está cerrando.
- **Cuadre**: `esperado = base + cobros en efectivo + ingresos − egresos`. Datáfono y transferencias se reportan por método pero no cuentan para el cajón. Al cerrar se congela `monto_esperado`, `monto_contado` y `diferencia` (negativo = faltante).
- **Anular**: solo mientras la caja de la venta siga abierta; después → `CONFLICT` ("usa una nota crédito", Fase 8). Así un cuadre ya cerrado nunca cambia.
- **Comprobante interno**: consecutivo `CI-000001` por negocio, **sin huecos** (UPSERT que bloquea la fila y se revierte con la transacción) y un registro en `invoices` tipo `interna`. `GET /sales/{id}/receipt` devuelve el comprobante listo para imprimir; `POST /sales/{id}/send` lo **encola** en `notifications` (el envío real por WhatsApp/correo es de la Fase 7).
- **Ventas sin módulo `reception`**: exigen `calendar` **o** `reception` (`require_any_module`), así funcionan en el plan independiente.
- **Reportes**: una sola forma (`columnas` + `filas` + `totales`) para tabla, gráfica y exportación; `GET /reports/{nombre}?desde&hasta&agrupar=dia|mes|anio&staff_id&formato=json|xlsx|pdf`. Reportes: `ventas`, `por-profesional`, `por-servicio`, `por-metodo`, `ocupacion`, `clientes` (nuevos vs recurrentes), `citas` (cancelaciones y no-shows). Fechas locales del negocio, inclusivas, máx. 366 días. Se basan en la **fecha de cobro** (`sales.pagada_en`, columna nueva) y excluyen ventas anuladas/abiertas.
- **Ocupación** = horas reservadas ÷ horas disponibles (horario − bloqueos fusionados); una cita fuera de horario o sobre un bloqueo no puede dar más de 100 %.
- **Clientes nuevos/recurrentes**: *nuevo* = su primera venta pagada cae en el periodo; *recurrente* = ya había comprado antes. Los totales cuentan a cada cliente una sola vez en el rango.
- **Turnos para el plan independiente**: `GET /appointments/stats` (conteo por estado), porque ese plan no tiene reportes.
- Cambios de esquema: `sales.pagada_en` + índice parcial. Permisos: nuevo `sales:read`; anular es solo admin; reportes solo admin.

## Endpoints nuevos
`sales`: `POST/GET /sales`, `GET /sales/{id}`, `POST /sales/{id}/pay|void|send`, `GET /sales/{id}/receipt` ·
`cash`: `POST /cash/sessions/open`, `GET /cash/sessions[/current]`, `GET /cash/sessions/{id}/cuadre`, `POST /cash/sessions/{id}/close`, `POST /cash/movements` ·
`reports`: `GET /reports/{nombre}` · `appointments`: `GET /appointments/stats`.

## Pruebas
`pytest tests` → **107 pasan, 0 omitidas** (Postgres real). Fase 5: 19 puras (dinero/IVA/precio editable/pagos/cuadre/ocupación/exportación a xlsx y pdf/permisos) + 10 de flujo: caja obligatoria vs plan independiente, cobro dividido con propina → cita atendida → cuadre → cierre con faltante → bloqueo posterior, precio editable, IVA, **5 cobros simultáneos de la misma venta → 1 solo**, **6 ventas simultáneas → consecutivos 2..7 sin huecos**, anulación y re-cobro, comprobante + cola de envío, y reportes que **cuadran contra el libro** (ventas = método = servicio; nuevos/recurrentes con una venta antigua). Recorrido HTTP verificado (permisos por rol, descargas xlsx/pdf).

## Criterios de aceptación
- [x] Reglas 6 y 9 completas; comprobante interno; pagos divididos; cuadre esperado vs contado.
- [x] Reportes con exportación Excel/PDF; todos con los mismos filtros.
- [ ] Pendiente: probar con el **contenedor** `postgres:16` y `alembic upgrade` (solo se ha probado con un Postgres 18 local).

## Riesgos / decisiones tuyas
1. **¿El precio de lista incluye IVA?** Asumí que no (se suma). Si tus precios ya lo incluyen, hay que desglosarlo (`precio/(1+IVA)`); es un cambio pequeño pero afecta todos los totales.
2. **Propinas**: se cobran junto con la venta y se reportan por profesional; no hay liquidación de propinas ni comisiones. ¿Las necesitas?
3. **Ventas en caja cerrada**: no se anulan; la corrección legal es nota crédito (Fase 8). Hasta entonces, un error en una caja cerrada solo se corrige con un movimiento de caja manual.
4. **Una caja por negocio**: si tienes varias recepciones simultáneas, hay que pasar a una caja por usuario.
5. **Inventario** sigue sin tablas (módulo reservado).
6. **Reportes grandes**: se calculan en vivo (sirve hasta decenas de miles de ventas/año); luego conviene una tabla resumen diaria.

---

# Fase 6 — Motor de reservas público

## Decisiones
- **Rutas públicas** `/api/v1/public/{slug}/…` (sin login; el negocio se identifica por su dirección): `GET config`, `GET availability`, `POST bookings`, `GET bookings/{token}`, `POST bookings/{token}/cancel`, `POST bookings/{token}/reschedule`. La administración vive en `/api/v1/booking/*` (login + módulo `booking` + permiso `booking:manage`): `settings`, `form-fields`, `rules`.
- **No se filtra información**: dirección inexistente, página desactivada, negocio suspendido/vencido o plan sin el módulo → **el mismo 404 con el mismo mensaje**. El slug se resuelve en modo plataforma de solo lectura; el resto corre con RLS del negocio, así que una sesión pública jamás ve datos de otro negocio.
- **Configuración por negocio** (`booking_settings`): colores, logo (solo https), textos (`titulo`, `bienvenida`, `confirmacion`, `consentimiento`), anticipación mínima/máxima, buffer, **límite para cancelar/reprogramar** (`cancelacion_min_horas`, columna nueva, 2 h por defecto), `modo_asignacion` y `pedir_servicio`. Formulario con hasta 20 campos propios (texto, teléfono, correo, número, fecha, selección, casilla, área de texto) validados en el servidor; las respuestas se guardan en las notas de la cita.
- **Modos de asignación**: `cliente_elige` (puede escoger profesional o "cualquiera" → asignación automática), `automatica` (se ignora lo que mande el cliente y se asigna al libre con menos citas ese día), `en_recepcion` (la cita queda sin profesional y **consume capacidad**: no se promete más de los profesionales libres). Fuera de `cliente_elige` el equipo ni siquiera se muestra.
- **Reserva estricta** (`strict=True`): exige horario, bloqueos, buffer, cupos y anticipación reales; la BD sigue siendo la última barrera (409 `SLOT_TAKEN` si dos clientes llegan a la vez; 409 `SLOT_UNAVAILABLE` si el horario ya no existe).
- **Clientes**: se busca por **teléfono normalizado** (`+57 300-123 4567` = `3001234567`); si existe se reutiliza **sin pisar sus datos** (solo se registra el consentimiento si faltaba); si no, se crea con origen `reserva_web`, consentimiento, fecha y versión del texto. Alta serializada por (negocio, teléfono) para no duplicar con solicitudes simultáneas. Esto resuelve el riesgo de duplicados para los flujos web (WhatsApp usará lo mismo).
- **Token firmado** (JWT HS256, `typ=booking`): lleva cita, negocio y **hora de inicio**; si se reprograma, el enlace anterior deja de servir (se entrega uno nuevo); vence 1 día después de la cita; no sirve en otro negocio ni como sesión (y viceversa). La vista pública solo muestra el primer nombre, sin teléfono ni correo.
- **Anti-spam / abuso**: trampa `website` (honeypot); consentimiento obligatorio; máx. **3 reservas activas por teléfono** (`TOO_MANY_BOOKINGS`, 429); limitador por IP+dirección: 5 reservas/10 min y 20/día, 60 config/min, 40 consultas de disponibilidad/min, 10 cancelaciones y 10 reprogramaciones/hora (`RATE_LIMITED` con cabecera `Retry-After`). Los intentos rechazados también cuentan.
- **Limitador**: Redis; si Redis no responde cae a un contador en memoria **por proceso** (protección menor pero las reservas no se caen).
- **Confirmación**: se encola en `notifications` (WhatsApp si el plan tiene el módulo, si no correo). Se guarda solo el id de la cita; el enlace se genera al enviar (no hay secretos en la cola). El envío real es la Fase 7.
- **Cupos** (`availability_rules`): ahora se editan por API; solo reglas del negocio entero (por profesional se rechazan hasta que existan). Cambio de esquema: `booking_settings.cancelacion_min_horas`.

## Pruebas
`pytest tests` → **150 pasan, 0 omitidas**. Fase 6: 21 sin BD (token: manipulación/expiración/tipo cruzado; teléfono; formulario; limitador con reloj falso; esquemas; contrato: exactamente 6 rutas públicas, sin credenciales y sin datos de contacto en las respuestas) + 15 con Postgres: 4 casos de "página no disponible" idénticos, configuración pública, reserva completa (cliente, cita, notificación, auditoría `system`), WhatsApp en E.164, mismo teléfono = mismo cliente y límite de 3, reglas estrictas, formulario/consentimiento/honeypot sin dejar basura, **6 reservas simultáneas al mismo horario → 1**, asignación automática con 4 solicitudes → 2 (una por profesional), modo recepción, cupo global, cancelar libera el hueco y respeta la ventana, reprogramar (sin chocar consigo misma, token nuevo), aislamiento entre negocios. Recorrido HTTP verificado (incluido el 429).

## Criterios de aceptación
- [x] Marca/formulario/servicios/profesionales visibles; disponibilidad; reserva; cancelar/reprogramar con token firmado; rate limiting y anti-spam.
- [ ] Pendiente: el limitador contra un **Redis real** (solo probado el modo de respaldo) y el contenedor `postgres:16`.

## Riesgos / decisiones tuyas
1. **Límite por IP compartida**: 5 reservas/10 min por IP puede afectar a una oficina o red móvil con IP compartida. ¿Lo subimos? Se ajusta con una constante.
2. **CAPTCHA**: hoy solo hay honeypot + límites. Si hay ataques reales, conviene Cloudflare Turnstile/hCaptcha (requiere una cuenta tuya).
3. **Dominio propio por negocio** (reservas.minegocio.com): no está; hoy es `tu-dominio/slug`. Es trabajo de despliegue/DNS.
4. **Quién ve el enlace**: el token viaja en la URL; quien lo tenga puede cancelar. Es el estándar, pero evita compartirlo.
5. **Recordatorios** (24 h antes, etc.) los programa la Fase 7 junto con el envío por WhatsApp.


---

# Fase 7 — WhatsApp (Meta) + asistente de IA

## Qué hace
Un cliente escribe al WhatsApp del negocio → Meta llama al webhook → se guarda el mensaje → el asistente (Claude) responde usando **solo** el motor real: consulta disponibilidad, crea la reserva, ve o cancela citas del propio cliente, o pasa la conversación a una persona. Además, una cola envía confirmaciones, recordatorios (≈24 h antes) y comprobantes por WhatsApp o correo.

## Decisiones
- **Un webhook por negocio**: `/api/v1/webhooks/whatsapp/{slug}`. El `GET` (verificación) compara el `verify_token` guardado; el `POST` exige firma **`X-Hub-Signature-256`** (HMAC-SHA256 del cuerpo exacto con el *app secret* del negocio). **No se lee el cuerpo antes de validar la firma**; slug inexistente = token malo = firma mala (mismo rechazo). Un número que no es el del negocio dentro de un payload firmado se ignora. Límite de 1 MB y 600 peticiones/min por IP.
- **Credenciales** (token de Meta, app secret, clave de IA propia): cifradas con Fernet por negocio, **jamás devueltas** (solo banderas `tiene_token`…). El `verify_token` se muestra **una sola vez**, al guardar. `POST …/validate` prueba el token contra Meta / la clave contra el proveedor y deja el estado `activa`/`error`.
- **Idempotencia y orden**: Meta reintenta → el mensaje es único por `wamid`. Un candado por conversación + "solo responde el último mensaje" evita respuestas dobles si el cliente escribe tres líneas seguidas. Los estados de entrega (`sent/delivered/read/failed`) nunca retroceden.
- **Procesar sin hacer esperar a Meta**: el webhook guarda y responde 200; la respuesta de la IA corre en segundo plano. Si el proceso cae a mitad, un **barrido** de Celery (cada minuto) reprocesa los mensajes sin marca `procesado`.
- **Agente (`integrations/ai`)** — herramientas: `consultar_disponibilidad`, `crear_reserva`, `consultar_mis_citas`, `cancelar_cita`, `escalar_a_recepcion`. Seguridad: el modelo **no elige el negocio ni el teléfono** (salen de la conversación firmada); toda entrada del modelo se valida; cada herramienta corre en un *savepoint*; solo toca citas del propio cliente (si no es suya, "no existe"); reservar usa el mismo motor estricto de la Fase 6 (horario, bloqueos, cupos, anticipación, doble reserva en BD) y respeta la ventana de cancelación.
- **"Nunca inventa horarios"** — 4 capas: (1) el prompt lo prohíbe; (2) los horarios solo existen como salida de la herramienta; (3) **guarda de código**: toda hora `HH:MM` que la IA escriba debe aparecer en un resultado de herramienta de ese turno o en la conversación; si no, la respuesta **no se envía**, se le pide corregir una vez y, si reincide, se escala a una persona; (4) la reserva se revalida en el servidor y la BD impide el solape.
- **Consentimiento (Ley 1581)**: cliente nuevo ⇒ la herramienta exige nombre y `acepta_tratamiento_datos=true` (solo si el cliente dijo que sí en el chat; el texto queda en la conversación como evidencia). Sin eso no se crea ni cliente ni cita.
- **Escalamiento a humano**: lo dispara la IA (reclamos, pagos, salud, petición del cliente), un fallo del proveedor, un rechazo de seguridad, una hora no verificada, un envío fallido a WhatsApp o el límite del plan. Queda `escalado_a_humano` + motivo + aviso interno; **el bot se calla** hasta `POST /conversations/{id}/resolve`. Una persona responde con `POST …/reply` (entra en escalado automáticamente).
- **Límite por plan**: se cuentan los mensajes con `rol='ia'` del mes (zona del negocio) contra `plans.max_mensajes_ia_mes`; al alcanzarlo se avisa **una vez** y se pasa a una persona. Los avisos fijos del sistema no consumen cupo. Plan sin cupo de IA ⇒ el asistente no responde.
- **Ventana de 24 h de WhatsApp**: texto libre solo dentro de las 24 h del último mensaje del cliente; fuera de ella, **solo plantillas aprobadas** por Meta (se mapean por negocio en `plantillas`). Sin ventana ni plantilla, la notificación falla con motivo claro (no reintenta para siempre). Respuesta humana fuera de ventana → `409 WHATSAPP_WINDOW_CLOSED`.
- **Notificaciones**: worker cada minuto (`FOR UPDATE SKIP LOCKED`: dos workers no envían lo mismo); reintentos con espera 2/4/8 min solo para fallos transitorios (red, 429, 5xx); los errores permanentes fallan al primer intento; se descartan las de citas ya canceladas. El enlace de gestión se genera al enviar (no se guardan secretos en la cola). Correo por SMTP (si no está configurado, falla con motivo).
- **Claude**: SDK oficial `anthropic`, modelo `claude-opus-5-5` (configurable con `AI_MODEL`), `output_config.effort=medium`, sin `thinking`/`temperature`/`tool_choice` forzado (Opus 5.5 los rechaza), bloque de sistema estable con `cache_control` y lo volátil (hora, cliente) aparte, y el contenido del asistente se reenvía intacto dentro del ciclo de herramientas. **Reintento por rechazo de seguridad activado por defecto** (`fallbacks` → `claude-opus-4-8`, `AI_REFUSAL_FALLBACK`): si un clasificador rechaza, la API reintenta sola en otro modelo; si aun así rechaza, se escala a una persona.
- **Arreglos de paso**: el pool de BD se recrea por tarea de Celery (antes la 2.ª ejecución de la misma tarea fallaba por usar un event loop viejo — afectaba también el job de vencimientos de la Fase 3); el limitador no hace esperar 0,5 s a cada petición cuando Redis está caído (interruptor de 30 s); el lector del webhook no lanza ante payloads malformados; el SDK de IA se carga solo cuando se usa.

## Endpoints nuevos (`/api/v1`)
`integrations/whatsapp` (GET, PUT, DELETE, `POST /validate`) · `integrations/ai` (GET, PUT, `POST /validate`) · `conversations` (lista, `/{id}/messages`, `POST /{id}/reply`, `POST /{id}/resolve`) · `webhooks/whatsapp/{slug}` (GET verificación, POST eventos; públicos y firmados). Permisos: integraciones = admin; bandeja = admin y recepcionista; requieren los módulos `whatsapp` / `ai` del plan.

## Pruebas
`pytest tests` → **215 pasan, 0 omitidas** (también en una segunda corrida sobre datos sobrantes). Fase 7: **43 sin BD** (firma, parser tolerante, cliente Meta con transporte simulado, proveedor Claude con cliente simulado y mapeo de errores, guarda de horarios, ciclo del agente con un modelo de mentira, textos/reintentos, esquemas sin secretos, puerta del webhook) + **23 con Postgres** (credenciales cifradas, webhook firmado de punta a punta, idempotencia, aislamiento, agente reservando con el motor real, hora inventada bloqueada, consentimiento, límite del plan, escalamiento, mensaje superado/reintento, audio, caída del proveedor y de WhatsApp, herramientas acotadas a las citas del cliente, barrido, respuesta humana y ventana, confirmaciones/plantillas/reintentos/descartes/correo, 3 workers simultáneos sin duplicar, recordatorios idempotentes, tarea con pool limpio). Recorrido HTTP verificado.
Al probar aparecieron y se corrigieron: un parser que fallaba con `entry` malformado, una espera de 0,5 s por petición con Redis caído, y 2 pruebas viejas (Fase 5) que dependían de la hora del día.

## Lo que NO se ha probado (importante)
**Nunca se llamó a Meta ni a Claude de verdad** (no hay cuentas ni claves; todo fue simulado con las formas documentadas de ambas APIs). Falta, con credenciales reales: validar el token, recibir un webhook real de Meta, enviar un mensaje real, tener una conversación real con Claude y confirmar el consumo de tokens. Tampoco se probaron Redis/Celery reales ni Docker.

## Criterios de aceptación
- [x] Webhook con verificación y firma; el mensaje entrante identifica el negocio; IA con contexto real; consulta disponibilidad real; crea la reserva y confirma; nunca inventa horarios (guarda en código); escala a recepción; límite por plan; envío de mensajes; credenciales cifradas y nunca devueltas.
- [ ] Prueba con cuentas reales de Meta y de Anthropic (requiere tu ayuda: ver abajo).

## Decisiones tuyas (importantes)
1. **Costo de la IA (el más importante).** Con `claude-opus-5-5` ($4 entrada / $20 salida por millón de tokens) estimo ~US$0,03–0,06 por mensaje de cliente (suposición: ~4.500 tokens de entrada y 2–3 llamadas por mensaje; **no medido**). El plan Profesional (500 mensajes) podría costar hasta ~US$15–30/mes y el Premium (3.000) ~US$90–180/mes, frente a un precio de plan de COP 149.000–249.000. Alternativas: `claude-sonnet-5-5` ($2/$10, ~la mitad) o `claude-haiku-5-5` ($0,10/$0,50, ~40 veces más barato). Se cambia con `AI_MODEL`. Recomiendo **medir ~50 conversaciones reales con Haiku y con Opus** antes de fijar límites y precios de los planes.
2. **Reintento por rechazo de seguridad activado** (usa un 2.º modelo si el primero rechaza). Se apaga con `AI_REFUSAL_FALLBACK=false`.
3. **¿Una app de Meta para todos o una por negocio?** Hoy cada negocio guarda sus credenciales y tiene su webhook. Si prefieres que **tú** (la plataforma) seas el proveedor de la app de Meta, el cambio es pequeño pero conviene decidirlo antes de incorporar clientes.
4. **Plantillas de WhatsApp**: recordatorios y confirmaciones fuera de las 24 h requieren plantillas **aprobadas por Meta** (tardan días). Hay que diseñarlas: `reserva_confirmada`, `recordatorio_cita`, `comprobante`.
5. **Correo**: falta elegir proveedor SMTP (SES, Resend, Google Workspace…). Sin él, los avisos por correo (p. ej. suscripción por vencer) fallan con aviso.
6. **Quién paga la IA**: hoy la clave de la plataforma (el negocio puede traer la suya y el límite del plan igual aplica). ¿Cobrar mensajes extra por encima del cupo?
7. **Privacidad**: los mensajes de clientes se guardan en la BD y parte del historial viaja al proveedor de IA; hay que reflejarlo en tu política de datos y en el contrato con el proveedor.


---

# Fase 8 — Factura electrónica (DIAN)

## Qué hay y qué NO hay (léelo primero)
**Hay**: toda la lógica nuestra, probada: resolución y numeración, impuestos, estados, reintentos, notas crédito, protección contra duplicados, reporte neto, y una **interfaz de proveedor intercambiable**.
**No hay**: conexión con un proveedor tecnológico real ni con la DIAN. El único proveedor es **`sandbox` (SIMULADO)**: no habla con la DIAN, sus documentos salen marcados *"SIN VALIDEZ FISCAL"* y el sistema **se niega a usarlo en producción** (en 3 puntos: al guardar la resolución, al elegir proveedor y al instanciarlo). Hasta que elijas e integremos un proveedor real, **ninguna factura emitida tiene validez legal**.

## Decisiones
- **Opcional por venta y solo Premium**: `POST /sales/{id}/invoice` exige el módulo `einvoice`. Sin él (otros planes), el negocio sigue con su **comprobante interno** (Fase 5), sin cambios. Se factura solo una venta **pagada**; la propina no hace parte de la factura (queda anotado en el documento).
- **Proveedor desacoplado** (`einvoice/provider.py`): 3 métodos (`validate`, `emit`, `status`), idempotentes por `referencia`; un rechazo de la DIAN es un **resultado** (`rechazada` + códigos), no una excepción; `ProviderTransient` = reintentar; `ProviderConfigError` = no reintentar. Hay una **prueba de contrato** reutilizable (`assert_provider_contract`) que cualquier adaptador real debe pasar. Credenciales del proveedor: cifradas, nunca devueltas.
- **Resolución y numeración (`invoice_settings`)**: NIT con **dígito de verificación calculado/validado** (comprobado con DIAN 800197268-4, Bancolombia 890903938-8, Ecopetrol 899999068-1), número de resolución, vigencia, prefijo y rango. Consecutivo **sin huecos** dentro del rango (UPSERT bloqueante dentro de la transacción: si algo falla antes de confirmar, el número se revierte). Rango agotado → `409 RESOLUTION_EXHAUSTED`; resolución vencida → `409 RESOLUTION_EXPIRED`; ninguno "quema" números. La configuración informa cuántos números **quedan** para avisar a tiempo.
- **Estados**: `emitida` (numerada, pendiente de enviar) → `enviada` (el proveedor la recibió, falta validación) → `aceptada` | `rechazada`; `error` = hay que revisar. Los **fallos temporales** (red, 5xx) se reintentan con espera 1-2-5-15-30-60-120-240 min **con el mismo número** (el documento ya numerado jamás se descarta ni se duplica); tras 8 intentos pasa a `error`. Un **rechazo** conserva su número y se corrige con `POST /invoices/{id}/retry` (se puede corregir el adquirente). Una aceptada no se reenvía.
- **Sin duplicados**: índice único parcial — una sola factura **viva** (`emitida|enviada|aceptada`) por venta — más candado de fila sobre la venta. 5 emisiones simultáneas de la misma venta → 1.
- **Adquirente**: lo enviado en la petición > datos del cliente de la venta (documento; **supuesto: cédula**) > **Consumidor final** (222222222222).
- **Impuestos**: IVA por línea desde la venta; resumen por tarifa (0 %, 5 %, 19 %…) con base y valor; la tarifa efectiva se ajusta a la conocida (evita 18,99 %). **No** hay INC, retenciones ni otros tributos todavía.
- **Medios de pago DIAN**: efectivo 10, transferencia 47, datáfono/otro 1 (no definido).
- **Notas crédito**: de una factura **aceptada**, total o **por ítems** (nunca el mismo ítem dos veces ni más que el valor de la factura), con motivo DIAN (1 devolución parcial · 2 anulación · 3 rebaja · 4 descuento · 5 rescisión · 6 otros), numeración propia (prefijo `NC` configurable) y referencia al CUFE de la factura. Una factura rechazada no se acredita.
- **Reportes**: el reporte de ventas añade **Notas crédito** y **Neto**; la nota resta en el periodo en que **se emite y es aceptada** (el pasado cerrado no se reescribe). Con filtro por profesional no se muestran (las notas no tienen profesional).
- **Envío inmediato**: tras emitir, una tarea de fondo envía al proveedor; un barrido de Celery cada minuto reintenta y consulta las pendientes (`FOR UPDATE SKIP LOCKED`).
- **Lección técnica (bug real encontrado al probar)**: en FastAPI las tareas de fondo corren **antes** de que se confirme la transacción de `get_db`; el envío no veía la factura recién creada y esperaba al barrido. Ahora esos endpoints usan `tenant_tx` (confirma al salir del bloque, *luego* lanza la tarea). Quedó cubierto por la prueba HTTP.
- **Proveedor simulado para desarrollo**: el documento del adquirente `8888888888` simula una caída temporal y `9999999999` un rechazo; el PDF de representación gráfica se genera en `/invoices/{id}/pdf`. El CUFE que calcula es **de referencia** (SHA-384 de los campos del anexo técnico), no oficial.

## Endpoints nuevos (`/api/v1`, módulo `einvoice`)
`einvoice/settings` (GET, PUT) · `einvoice/provider` (GET, PUT, `POST /validate`) · `POST sales/{id}/invoice` · `invoices` (lista con filtros) · `invoices/{id}` (con el documento completo) · `POST invoices/{id}/retry` · `POST invoices/{id}/credit-note` · `GET invoices/{id}/pdf` (solo simulado). Configuración: admin; facturar: admin y recepcionista.

## Pruebas
`pytest tests` → **255 pasan, 0 omitidas** (dos corridas seguidas). Fase 8: **29 sin BD** (DV con casos reales, rango/vigencia, impuestos, medios de pago, adquirente, CUFE sensible a cada campo, máquina de estados, reintentos, tope de notas crédito, proveedor simulado + contrato, esquemas, permisos) + **12 con Postgres** (configuración y cifrado, 3 candados contra producción+simulador, emisión completa con snapshot/PDF/auditoría, una factura viva por venta y datos del cliente, rango sin huecos y agotamiento que no quema números, resolución vencida, 5 emisiones simultáneas → 1 y 5 ventas → números contiguos, rechazo y corrección con el mismo número, 8 reintentos y `error`, configuración faltante sin reintentos, notas crédito parciales/restantes/ajenas, aislamiento entre negocios, reporte neto, flujo HTTP completo y bloqueo por plan).

## Criterios de aceptación
- [x] Adaptador desacoplado, reintentos, estados y notas crédito; solo Premium y opcional por venta; comprobante interno para el resto.
- [ ] **Conexión real** con un proveedor autorizado por la DIAN y pruebas en el ambiente de habilitación de la DIAN (requiere tu decisión y credenciales).

## Decisiones tuyas (importantes)
1. **Elegir el proveedor tecnológico.** Evalúa proveedores colombianos autorizados por la DIAN (por ejemplo Alegra, Siigo, Factus, Carvajal, Facture; no verifiqué su estado ni precios actuales). Criterios: API REST documentada con ambiente de pruebas, precio por documento, soporte de notas crédito, cuenta por negocio vs. esquema para revendedores/multi-empresa, y cuánto ayudan con la **habilitación** de cada negocio ante la DIAN. Con el elegido, escribo su adaptador (acotado) y lo valido con la prueba de contrato.
2. **Habilitación ante la DIAN**: cada negocio debe habilitarse como facturador electrónico (set de pruebas, resolución de numeración, certificado). Eso lo hace el negocio con su proveedor; el sistema solo guarda los datos.
3. **¿Factura electrónica o documento equivalente POS?** Para ventas al consumidor final en el punto de venta puede aplicar el *documento equivalente electrónico* en lugar de factura. **Consúltalo con un contador** antes de lanzar: cambia el tipo de documento que debe emitirse.
4. **No incluido todavía**: otros impuestos (INC, retenciones, ICA), notas crédito por **valor** (hoy son por ítems completos), notas débito, modo de **contingencia** cuando la DIAN no responde, y el **envío automático** de la factura al cliente (la notificación existe en la Fase 7: es un cambio pequeño).
5. **Revisión legal/contable**: este módulo implementa reglas generales de la facturación electrónica colombiana de buena fe, pero **no sustituye la validación de un contador** ni la del proveedor.

---

# Fase 9 — Endurecimiento, pruebas finales, documentación y despliegue

## Qué se hizo
- **Configuración segura**: en `ENVIRONMENT=production` la API **se niega a arrancar** con secretos por defecto/débiles, `FERNET_KEY` inválida, CORS/URLs sin https, docs abiertas, o un rol de BD con superusuario/BYPASSRLS (`startup_checks`). `/health` y `/health/ready`.
- **Perímetro HTTP**: cabeceras de seguridad, tope de tamaño de petición, `X-Request-ID` saneado, logs JSON con la ruta como plantilla (sin datos personales), límites de peticiones en login/refresh/olvido/reserva pública (Redis con respaldo en memoria y circuito de 30 s).
- **Secretos**: Fernet con rotación sin interrupción (`app.scripts.rotate_keys`), super admin solo por script con contraseña por teclado (`create_superadmin`), semilla separada (`seed.sql` dev / `seed_catalog.sql` catálogo).
- **Base de datos**: pool y `statement_timeout` configurables, `application_name`; migraciones Alembic **equivalentes** a `sql/schema.sql`+`rls.sql` (probado por comparación de volcados, subida, repetición y bajada).
- **Despliegue**: Dockerfile sin root, `docker-compose.prod.yml` + Caddy (HTTPS), CI, `scripts/backup.sh` / `restore.sh` con suma de verificación.
- **Contrato**: OpenAPI fiel (errores con `ErrorResponse`, ejemplos en cada endpoint), `openapi.json`, `postman_collection.json`, `docs/ERRORES.md`, `docs/CONTRATO_FRONTEND.md`.

## Verificación (Postgres 18 temporal, borrado al terminar)
| Comprobación | Resultado |
|---|---|
| Suite completa contra Postgres | 310 pruebas, **todas pasan** (2 corridas completas al inicio; tras las correcciones se repitieron los archivos tocados y la verificación estática) |
| RLS | **Todas** las tablas con datos de negocio tienen RLS forzada; solo el catálogo global (5 tablas) no la tiene |
| Rol de la API | Sin superusuario/BYPASSRLS/creación de roles; no puede alterar, borrar ni truncar la auditoría |
| Auth de punta a punta | Errores indistinguibles, bloqueo y recuperación, rotación de refresh y detección de reuso, impersonación sin cambio de credenciales, negocio bloqueado puede leer `/me` |
| Migraciones ≡ `schema.sql` | Idénticas (sin la tabla propia de Alembic); `downgrade` y re-`upgrade` correctos |
| Respaldo / restauración | Restaura todo, conserva RLS y permisos, detecta archivos manipulados, no pisa bases con datos |
| Calidad estática | `ruff` limpio, `mypy --strict` limpio (125 archivos), `pip-audit` limpio |
| Cobertura | **87 %** de líneas (`pytest --cov=app`); las tareas Celery (`workers/tasks.py`) y `celery_app.py` figuran en 0 %: se prueba la lógica que llaman, no el despliegue de Celery |
| Carga (1 proceso, peor caso) | ~20 consultas de disponibilidad/s; 0 errores; sin dobles reservas; ver `docs/OPERACION.md` §5 |

## Defectos reales encontrados por esta verificación (y corregidos)
1. **Reportes y cuadre de caja** fallaban con SQL inválido (`p.tid` en lugar de `p.tenant_id`) tras un cambio de nombres hecho para el tipado: lo detectaron las pruebas con Postgres real.
2. **La migración inicial de Alembic fallaba** (el driver tomaba `%I` del SQL por un parámetro) y su `downgrade` borraba la tabla de versiones. Habrían roto el primer despliegue con migraciones.
3. Dos pruebas con supuestos incorrectos (conteo de tablas y de auditoría) y una de operación con la codificación de la consola de Windows.

## Riesgos y decisiones
Ver [`DECISIONES_PENDIENTES.md`](DECISIONES_PENDIENTES.md). Lo no verificado aquí: proveedores reales (Meta, Claude, DIAN), Redis/Celery reales, `docker compose` de producción y Caddy.


---

# Inventario (módulo `inventory`) — agregado después de la Fase 9

Los planes Profesional y Premium ya incluían el módulo `inventory` en su lista, pero no existía ninguna funcionalidad detrás. Ahora sí.

## Qué hace
- **Productos**: nombre, código opcional (único por negocio, sin importar mayúsculas), costo, precio de venta, stock mínimo y si está activo. El borrado es lógico.
- **Movimientos**: `entrada` suma, `salida` resta, `ajuste` fija el stock en el conteo físico (exige motivo). Cada producto nuevo con stock inicial registra una entrada «Stock inicial»: el stock siempre tiene explicación.
- **Alerta de bajo stock**: `stock_minimo > 0` y `stock_actual <= stock_minimo` (con mínimo 0 no hay alerta).
- **Resumen**: productos, cuántos están bajo stock y valor del inventario a costo.

## Decisiones de diseño
- **El stock nunca queda negativo**: la salida bloquea la fila del producto antes de restar (probado con 12 salidas simultáneas sobre 5 unidades: exactamente 5 pasan y 7 reciben `STOCK_INSUFFICIENT`) y además la base lo impide con una restricción CHECK.
- **El libro de movimientos es de solo agregar**: el rol de la API no puede modificarlo ni borrarlo (igual que la auditoría). Un error se corrige con un ajuste nuevo.
- **Aislamiento**: ambas tablas llevan `tenant_id` y RLS forzada como el resto.
- **Permiso `inventory:manage`** (administrador y recepcionista) también para leer: el costo no es para todo el equipo.
- **El `PATCH` de un producto no cambia el stock**, solo datos; el stock solo cambia con movimientos.

## Endpoints (`/api/v1/inventory`)
`GET /summary` · `GET/POST /products` · `GET/PATCH/DELETE /products/{id}` · `GET/POST /products/{id}/movements`. Error nuevo: `STOCK_INSUFFICIENT` (409, con `details.disponible`).

## Alcance actual y lo que NO hace todavía
- **No descuenta stock automáticamente al cobrar** una venta ni al prestar un servicio (no hay receta de insumos por servicio). Los movimientos se registran a mano. Es la siguiente mejora natural, pero requiere decidir si un servicio gasta insumos y cuánto.
- Cantidades enteras (unidades). Para productos a granel (ml, gramos) habría que cambiar el tipo.
- Un solo almacén por negocio.

## Pruebas
`tests/test_inventory.py` (9 pruebas): validación, flujo completo y libro, código único, filtros, concurrencia, aislamiento entre negocios, historial intocable, restricción de la base y acceso por plan (403 `MODULE_NOT_ENABLED` en Básico) por HTTP.
