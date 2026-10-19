"""Tareas programadas (Celery beat) y de fondo.

Cada tarea corre en su propio `asyncio.run`: el pool de conexiones es de ese event loop, así que se crea limpio al
empezar y se cierra al terminar (`dispose_engine`). Sin esto, la segunda ejecución en el mismo proceso fallaba.
"""
import asyncio
import logging
from collections.abc import Awaitable
from typing import Any
from uuid import UUID

from app.core.db import dispose_engine, scoped_session
from app.modules.integrations.ai import agent
from app.modules.integrations.einvoice import service as einvoice
from app.modules.notifications import service as notifications
from app.modules.platform import service as platform
from app.workers.celery_app import celery_app

log = logging.getLogger("app.workers")


def run_async(coro: Awaitable[Any]) -> Any:
    async def main() -> Any:
        try:
            return await coro
        finally:
            await dispose_engine()
    return asyncio.run(main())


async def run_subscription_check() -> dict[str, int]:
    """Una sola transacción de plataforma: estados + avisos. Idempotente (se puede relanzar)."""
    async with scoped_session(platform=True) as db:
        cambios = await platform.sync_subscription_states(db)
        avisos = await platform.queue_expiry_reminders(db)
    log.info("vencimientos", extra={"cambios": len(cambios), "avisos": avisos})
    return {"cambios_estado": len(cambios), "avisos_encolados": avisos}


async def run_send_notifications() -> dict[str, int]:
    async with scoped_session(platform=True) as db:
        out = await notifications.process_pending(db)
    if out:
        log.info("notificaciones", extra=out)
    return out


async def run_reminders() -> int:
    async with scoped_session(platform=True) as db:
        return await notifications.queue_reminders(db)


async def run_invoices() -> dict[str, int]:
    async with scoped_session(platform=True) as db:
        return await einvoice.process_pending(db)


@celery_app.task(name="app.workers.tasks.daily_subscription_check")
def daily_subscription_check() -> dict[str, int]:
    return run_async(run_subscription_check())  # type: ignore[no-any-return]


@celery_app.task(name="app.workers.tasks.send_pending_notifications")
def send_pending_notifications() -> dict[str, int]:
    return run_async(run_send_notifications())  # type: ignore[no-any-return]


@celery_app.task(name="app.workers.tasks.queue_appointment_reminders")
def queue_appointment_reminders() -> int:
    return run_async(run_reminders())  # type: ignore[no-any-return]


@celery_app.task(name="app.workers.tasks.sweep_incoming_messages")
def sweep_incoming_messages() -> int:
    return run_async(agent.sweep_unanswered())  # type: ignore[no-any-return]


@celery_app.task(name="app.workers.tasks.process_incoming_message")
def process_incoming_message(tenant_id: str, message_id: str) -> str:
    async def go() -> str:
        async with scoped_session(tenant_id=UUID(tenant_id)) as db:
            return await agent.process_message(db, UUID(tenant_id), UUID(message_id))
    return run_async(go())  # type: ignore[no-any-return]


@celery_app.task(name="app.workers.tasks.process_pending_invoices")
def process_pending_invoices() -> dict[str, int]:
    return run_async(run_invoices())  # type: ignore[no-any-return]
