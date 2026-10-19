from celery import Celery
from celery.schedules import crontab

from app.core.config import get_settings

celery_app = Celery("agenda", broker=get_settings().redis_url, backend=get_settings().redis_url,
                    include=["app.workers.tasks"])
celery_app.conf.timezone = "UTC"
celery_app.conf.beat_schedule = {
    # 06:00 America/Bogotá (UTC-5, sin horario de verano) = 11:00 UTC
    "vencimientos-diarios": {"task": "app.workers.tasks.daily_subscription_check",
                             "schedule": crontab(hour=11, minute=0)},
    "enviar-notificaciones": {"task": "app.workers.tasks.send_pending_notifications", "schedule": 60.0},
    "recordatorios-de-citas": {"task": "app.workers.tasks.queue_appointment_reminders", "schedule": crontab(minute="*/15")},
    "barrido-de-mensajes-whatsapp": {"task": "app.workers.tasks.sweep_incoming_messages", "schedule": 60.0},
    "facturas-electronicas-pendientes": {"task": "app.workers.tasks.process_pending_invoices", "schedule": 60.0},
}
