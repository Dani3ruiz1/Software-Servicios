# Operación y despliegue

## 1. Lista de verificación antes de salir a producción

1. Servidor con Docker y un dominio apuntando a él (`API_DOMAIN`). Caddy (en `docker-compose.prod.yml`) obtiene el certificado HTTPS solo.
2. `cp .env.production.example .env && chmod 600 .env` y rellena **todo**. Genera `JWT_SECRET` y `FERNET_KEY` con los comandos del archivo; no los reutilices entre entornos.
3. PostgreSQL 16 (administrado recomendado). Migraciones con el usuario administrador: `alembic upgrade head` (crea esquema, RLS, rol `agenda_app` y el catálogo de planes/módulos; **no** crea usuarios).
4. `ALTER ROLE agenda_app PASSWORD '<larga-y-aleatoria>';` y ponla en `DATABASE_URL`. La API usa **solo** ese rol.
5. Crea tu super admin: `docker compose exec api python -m app.scripts.create_superadmin tu@correo.com "Tu Nombre"` (la contraseña se pide por teclado, mínimo 14 caracteres). El usuario de desarrollo no se instala en producción.
6. `docker compose -f docker-compose.prod.yml up -d`. Comprueba `GET /health/ready` → `200`.
7. Programa el respaldo diario (sección 3) y **prueba una restauración** antes de tener clientes reales.

Si algo está mal (secreto por defecto, `FERNET_KEY` inválida, CORS sin https, rol de BD con superusuario/BYPASSRLS…), la API **no arranca** y el log dice exactamente qué corregir.

## 2. Salud y monitoreo

- `GET /health` — el proceso vive. `GET /health/ready` — base de datos (y Redis: `degraded` si cae, la API sigue con límites en memoria).
- Logs JSON en stdout con `request_id` (cabecera `X-Request-ID`, saneada) y la ruta como plantilla (`/api/v1/customers/{id}`), sin datos personales.
- Alertas mínimas: `/health/ready` ≠ 200 durante 2 min; `notifications` en estado `pendiente` con más de 15 min; tareas de Celery fallando; espacio en disco del respaldo.

## 3. Respaldos y restauración

```bash
scripts/backup.sh /ruta/respaldos          # .dump + .sha256, borra los de más de BACKUP_RETENTION_DAYS (30)
scripts/restore.sh archivo.dump postgresql://admin:...@host/agenda_nueva
```

- Variable: `BACKUP_DATABASE_URL` (usuario con lectura total). Cron sugerido: `15 3 * * * /opt/agenda/scripts/backup.sh /var/backups/agenda`.
- El respaldo se escribe como `.partial` y solo se renombra al terminar bien; la restauración **verifica la suma sha256 antes de tocar nada** y se niega a escribir sobre una base con datos (a menos que uses `FORCE=1`).
- La restauración conserva RLS y permisos del rol de la API (probado: sin contexto de negocio, el rol ve 0 filas).
- Guarda copias **fuera** del servidor. Un respaldo que nunca se restauró no cuenta.

## 4. Rotación de la clave de cifrado (`FERNET_KEY`)

Cifra credenciales de integraciones (WhatsApp, etc.) y secretos 2FA. Sin interrupción:

1. `FERNET_KEY=<nueva>,<vieja>` y reinicia `api`, `worker`, `beat`.
2. `docker compose exec api python -m app.scripts.rotate_keys` (idempotente; sale con error y lista cualquier valor ilegible).
3. Con salida 0, deja solo `FERNET_KEY=<nueva>` y reinicia.

Rotar `JWT_SECRET` cierra todas las sesiones (los usuarios deben iniciar sesión de nuevo): hazlo en una hora tranquila o ante sospecha de filtración.

## 5. Escalado y capacidad medida

Medición real (Windows, 1 proceso uvicorn, Postgres local, peor caso: horarios de 24 h × 3 profesionales):

| Concurrencia | Disponibilidad pública | Reservas | Errores |
|---|---|---|---|
| 10 | ~20 req/s, p95 0,8 s | 15/15 ok | 0 |
| 30 | ~19 req/s, p95 3,5 s | 10/10 ok | 0 |

Conclusión: por proceso hay un techo de ~20 consultas de disponibilidad por segundo (CPU de Python); con más concurrencia sube la latencia, no los errores. Escala con `--workers N` / más réplicas y, si hace falta, cachea la disponibilidad pública unos segundos. Sin dobles reservas en ninguna corrida (restricción de exclusión en la BD). Repite la medición en tu servidor real: `scripts/loadtest_server.py` + `scripts/load_test.py`.

## 6. Runbooks

- **Un negocio no puede entrar**: ¿bloqueado por vencimiento? (`TENANT_BLOCKED` en `/auth/me`; la plataforma registra el pago para reactivar). ¿Cuenta bloqueada por intentos? (`ACCOUNT_LOCKED` con `bloqueado_hasta`; se libera sola).
- **No salen recordatorios/WhatsApp**: revisa que `worker` y `beat` estén arriba, la cola `notifications` y que la plantilla esté aprobada por Meta (fuera de la ventana de 24 h solo se envían plantillas).
- **Redis caído**: la API sigue (límite de peticiones en memoria, circuito de 30 s); las tareas en segundo plano se detienen hasta que vuelva.
- **Sospecha de fuga de un token**: rota `JWT_SECRET`. De un secreto de integración: reemplázalo en la integración (se cifra de nuevo).
- **Restaurar**: sección 3, siempre sobre una base nueva y luego cambiar `DATABASE_URL`.

## 7. Costo del asistente de IA

Por defecto `AI_MODEL=claude-opus-5-5` (el más capaz). Estimación **no medida**: del orden de US$0,03–0,06 por mensaje atendido. Antes de fijar precios de planes, mide ~50 conversaciones reales (el uso queda registrado) y compara con un modelo más económico (`claude-sonnet-5-5` o `claude-haiku-5-5`): cambia solo `AI_MODEL`. Cada plan ya limita los mensajes de IA al mes.

## Alcance de la primera versión: solo motor de reservas

Esta versión **no cobra, no factura y no maneja inventario**: el negocio cobra por fuera y aquí solo se reserva y se miden las reservas.
`sql/perfil_solo_reservas.sql` quita Caja, Inventario y Factura DIAN de todos los planes (el código se conserva para una versión futura) y deja los planes así:
Independiente y Básico = agenda + clientes + reservas en línea; Profesional = + reportes y WhatsApp; Premium = + asistente IA.
Se aplica una vez, **antes de crear negocios** (cada negocio copia los módulos de su plan al crearse). Los reportes de este perfil son de reservas:
clientes que más reservan, nuevos vs recurrentes, por profesional, por servicio, cancelaciones y ocupación. Los precios de los servicios se muestran solo como referencia.
