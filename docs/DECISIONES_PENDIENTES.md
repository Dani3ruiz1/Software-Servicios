# Decisiones tuyas antes de salir a producción

Lo técnico está hecho y probado; esto es lo que **no puedo decidir por ti** (dinero, contratos con terceros, responsabilidad legal). Ordenado por urgencia.

## Bloquean el lanzamiento

| # | Decisión | Mi recomendación | Por qué importa |
|---|---|---|---|
| 1 | **Proveedor real de facturación electrónica DIAN** | Elegir un proveedor tecnológico autorizado (con API y ambiente de pruebas) y pedirme integrarlo detrás de la interfaz que ya existe. Habilitación ante la DIAN y resolución de numeración a nombre de cada negocio. | Hoy solo hay un proveedor **simulado**: documentos marcados "SIN VALIDEZ FISCAL"; en producción se niega a operar. Consulta con un contador si el negocio debe emitir factura electrónica o documento equivalente POS. |
| 2 | **WhatsApp: ¿una app de Meta por negocio o una de la plataforma?** | Una app de la plataforma con número por negocio (menos fricción) si aceptas ser el responsable ante Meta. | Define quién verifica el negocio, quién paga conversaciones y quién aprueba **plantillas** (necesarias fuera de la ventana de 24 h). |
| 3 | **Modelo de IA y su costo** | Empezar con `claude-opus-5-5` (calidad) y **medir ~50 conversaciones reales**; si el costo por mensaje no cuadra con el precio del plan, bajar a Sonnet/Haiku cambiando solo `AI_MODEL`. | La estimación (US$0,03–0,06 por mensaje) **no está medida**. Cada plan ya limita los mensajes de IA al mes. |
| 4 | **Proveedor de correo (SMTP)** | Cualquiera transaccional (SES, Postmark, Resend). | Sin él no salen recuperación de clave ni avisos por correo (fallan con mensaje claro). |
| 5 | **Tu super admin y 2FA** | Crear tu usuario con `create_superadmin` y activar 2FA obligatorio para super admins. | Tiene acceso a todos los negocios y puede impersonar (queda auditado). |

## Conviene decidir pronto

| # | Decisión | Estado actual |
|---|---|---|
| 6 | Límite de peticiones por IP compartida (varios clientes en la misma red/WiFi) | 40 consultas de disponibilidad/min por IP; ajustable. Si un local grande reserva desde un mismo WiFi, súbelo. |
| 7 | CAPTCHA en la reserva pública | Hay trampa anti-bots + límites + token firmado. Añade CAPTCHA solo si ves abuso real. |
| 8 | Días de gracia tras vencer la suscripción | `SUBSCRIPTION_GRACE_DAYS=0` (bloqueo inmediato). |
| 9 | Definición de **MRR** y de "negocio activo" en el panel de plataforma | Revisa la definición implementada en `app/modules/platform/service.py` y confirma que es la que quieres reportar. |
| 10 | Una sola caja abierta por negocio | Regla actual. Si un negocio tiene varios puestos de cobro, hay que permitir una caja por sede/usuario. |
| 11 | Retención de datos y política de privacidad (Ley 1581, Habeas Data) | Se guarda el consentimiento de cada cliente; falta tu texto legal y política de borrado/anonimización por solicitud. |
| 12 | Precios y límites de planes | Precios manuales desde la API de plataforma; no hay pasarela de pago automática (los pagos de suscripción se registran a mano). |

## Lo que NO se pudo verificar aquí (hazlo en tu entorno)

- Proveedores reales: Meta (WhatsApp), Claude (API de pago), DIAN/proveedor tecnológico — solo se probaron con simulaciones.
- Redis y Celery reales (workers/beat) — la lógica de las tareas está probada, pero no el despliegue con `docker compose`.
- `docker-compose.prod.yml`, Caddy y el certificado HTTPS — revisados, no ejecutados.
- Capacidad en tu servidor real (ver `docs/OPERACION.md`, sección 5).
