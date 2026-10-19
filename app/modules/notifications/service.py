"""Cola de notificaciones: confirmaciones, recordatorios, comprobantes y avisos de suscripción.

Un worker (Celery beat, cada minuto) toma las pendientes y las envía por WhatsApp o correo.
Reglas de WhatsApp: texto libre solo dentro de las 24 h siguientes al último mensaje del cliente; fuera de esa ventana
únicamente PLANTILLAS APROBADAS por Meta (se configuran por negocio). Sin ninguna de las dos, la notificación falla con
un motivo claro en vez de reintentar para siempre.
"""
import asyncio
import json
import smtplib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.modules.booking import rules as booking_rules
from app.modules.integrations import service as integ
from app.modules.integrations.whatsapp_meta.client import MetaError

MAX_ATTEMPTS = 3
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


# ---------------------------------------------------------------- textos (puros)
def fecha_larga(dt: datetime, tz: str) -> str:
    d = dt.astimezone(ZoneInfo(tz))
    return f"{DIAS[d.weekday()]} {d.day} de {MESES[d.month - 1]} a las {d:%H:%M}"


def cop(v: Any) -> str:
    return "$" + f"{int(round(float(v))):,}".replace(",", ".")


def primer_nombre(n: str) -> str:
    return (n or "").strip().split(" ")[0] or "hola"


def texto_cita(plantilla: str, *, nombre: str, negocio: str, cuando: str, profesional: str | None, total: Any, enlace: str) -> str:
    con = f" con {profesional}" if profesional else ""
    if plantilla == "recordatorio_cita":
        return (f"Hola {primer_nombre(nombre)}, te recordamos tu cita en {negocio}{con}: {cuando}. "
                f"Si necesitas cancelarla o cambiarla, hazlo aquí: {enlace}")
    return (f"Hola {primer_nombre(nombre)}, tu cita en {negocio}{con} quedó para {cuando} (total aprox. {cop(total)}). "
            f"Puedes verla, cancelarla o cambiarla aquí: {enlace}")


def texto_comprobante(*, negocio: str, numero: str | None, total: Any) -> str:
    return f"Gracias por tu visita a {negocio}. Comprobante {numero or ''} por {cop(total)}."


def texto_suscripcion(*, dias: int, fecha_fin: str) -> str:
    return (f"Tu suscripción vence en {dias} días (el {fecha_fin}). Para evitar que tu cuenta se bloquee, "
            "comunícate con soporte para renovarla.")


def backoff(intentos: int) -> timedelta:
    return timedelta(minutes=2 ** intentos)          # 2, 4, 8 min...


def is_transient(e: MetaError) -> bool:
    return e.status in (0, 429) or e.status >= 500   # red caída, límite de velocidad o falla de Meta: vale reintentar


# ---------------------------------------------------------------- correo
async def send_email(to: str, subject: str, body: str) -> None:
    s = get_settings()
    if not s.smtp_host or not s.smtp_from:
        raise PermanentFailure("El correo no está configurado en el servidor (SMTP)")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = s.smtp_from, to, subject
    msg.set_content(body)

    def _send() -> None:
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as smtp:
            smtp.starttls()
            if s.smtp_user:
                smtp.login(s.smtp_user, s.smtp_password)
            smtp.send_message(msg)
    try:
        await asyncio.to_thread(_send)
    except (smtplib.SMTPException, OSError) as e:
        raise TransientFailure(f"Correo no enviado ({type(e).__name__})") from e


class PermanentFailure(Exception):
    pass


class TransientFailure(Exception):
    pass


@dataclass
class Rendered:
    texto: str
    asunto: str
    params: list[str]            # para la plantilla aprobada de Meta, en orden


# ---------------------------------------------------------------- armado según la plantilla
async def _appointment_data(db: AsyncSession, tenant_id: UUID, appt_id: str) -> Any:
    return (await db.execute(text(
        "SELECT a.id, a.inicio, a.estado, c.nombre AS cliente, t.nombre AS negocio, t.zona_horaria AS tz, "
        "coalesce(b.slug, t.slug) AS slug, sp.nombre_publico AS profesional, "
        "coalesce((SELECT sum(precio_pactado) FROM appointment_services s WHERE s.tenant_id = a.tenant_id AND s.appointment_id = a.id), 0) AS total "
        "FROM appointments a JOIN customers c ON c.tenant_id = a.tenant_id AND c.id = a.customer_id "
        "JOIN tenants t ON t.id = a.tenant_id LEFT JOIN booking_settings b ON b.tenant_id = t.id "
        "LEFT JOIN staff_profiles sp ON sp.tenant_id = a.tenant_id AND sp.id = a.staff_id "
        "WHERE a.tenant_id = :t AND a.id = CAST(:a AS uuid)"), {"t": tenant_id, "a": appt_id})).first()


async def render(db: AsyncSession, n: Any) -> Rendered | None:
    """None = ya no tiene sentido enviarla (p. ej. la cita se canceló): se descarta."""
    payload = n.payload or {}
    if n.plantilla in ("reserva_confirmada", "recordatorio_cita"):
        a = await _appointment_data(db, n.tenant_id, payload.get("appointment_id", ""))
        if a is None or a.estado not in ("pendiente", "confirmada"):
            return None
        enlace = f"{get_settings().public_base_url.rstrip('/')}/{a.slug}/reserva/{booking_rules.make_token(a.id, n.tenant_id, a.inicio)}"
        cuando = fecha_larga(a.inicio, a.tz)
        txt = texto_cita(n.plantilla, nombre=a.cliente, negocio=a.negocio, cuando=cuando, profesional=a.profesional, total=a.total, enlace=enlace)
        asunto = ("Recordatorio de tu cita en " if n.plantilla == "recordatorio_cita" else "Tu cita en ") + a.negocio
        return Rendered(txt, asunto, [primer_nombre(a.cliente), a.negocio, cuando, enlace])
    if n.plantilla == "comprobante":
        s = (await db.execute(text("SELECT s.comprobante_numero, s.total, t.nombre FROM sales s JOIN tenants t ON t.id = s.tenant_id "
                                   "WHERE s.tenant_id = :t AND s.id = CAST(:s AS uuid) AND s.estado = 'pagada'"),
                              {"t": n.tenant_id, "s": payload.get("sale_id", "")})).first()
        if s is None:
            return None
        return Rendered(texto_comprobante(negocio=s.nombre, numero=s.comprobante_numero, total=s.total), f"Comprobante {s.comprobante_numero}",
                        [s.nombre, s.comprobante_numero or "", cop(s.total)])
    if n.plantilla == "suscripcion_por_vencer":
        return Rendered(texto_suscripcion(dias=int(payload.get("dias", 0)), fecha_fin=str(payload.get("fecha_fin", ""))),
                        "Tu suscripción está por vencer", [str(payload.get("dias", "")), str(payload.get("fecha_fin", ""))])
    raise PermanentFailure(f"Plantilla desconocida: {n.plantilla}")


async def _send_whatsapp(db: AsyncSession, n: Any, r: Rendered, meta_factory: Callable[[Any], Any]) -> None:
    i = await integ.get_integration(db, n.tenant_id, integ.WA)
    if i is None or not i.credenciales_cifradas:
        raise PermanentFailure("WhatsApp no está configurado para este negocio")
    conv_last = (await db.execute(text("SELECT ultimo_mensaje_cliente_en FROM conversations WHERE tenant_id = :t AND canal = 'whatsapp' "
                                       "AND contacto_externo = :c"), {"t": n.tenant_id, "c": n.destinatario})).scalar()
    cliente = meta_factory(i)
    try:
        if conv_last and datetime.now(UTC) - conv_last <= timedelta(hours=get_settings().whatsapp_window_hours):
            await cliente.send_text(n.destinatario, r.texto)
            return
        plantilla = ((i.config or {}).get("plantillas") or {}).get(n.plantilla)
        if not plantilla:
            raise PermanentFailure("Fuera de la ventana de 24 h y sin plantilla aprobada de Meta configurada para este aviso")
        await cliente.send_template(n.destinatario, plantilla, r.params)
    except MetaError as e:
        raise (TransientFailure if is_transient(e) else PermanentFailure)(f"Meta: {e.message}") from e


async def deliver(db: AsyncSession, n: Any, *, meta_factory: Callable[[Any], Any] = integ.meta_client_for,
                  mailer: Callable[..., Any] = send_email) -> str:
    """Devuelve 'enviada' | 'descartada' | 'reintento' | 'fallida' y deja la fila actualizada."""
    intentos = int((n.payload or {}).get("intentos", 0))
    try:
        r = await render(db, n)
        if r is None:
            await _mark(db, n, "cancelada", {"motivo": "ya no aplica"})
            return "descartada"
        if n.canal == "whatsapp":
            await _send_whatsapp(db, n, r, meta_factory)
        elif n.canal == "email":
            await mailer(n.destinatario, r.asunto, r.texto)
        else:
            raise PermanentFailure(f"Canal no soportado: {n.canal}")
    except PermanentFailure as e:
        await _mark(db, n, "fallida", {"error": str(e), "intentos": intentos + 1})
        return "fallida"
    except TransientFailure as e:
        if intentos + 1 >= MAX_ATTEMPTS:
            await _mark(db, n, "fallida", {"error": str(e), "intentos": intentos + 1})
            return "fallida"
        await db.execute(text("UPDATE notifications SET payload = payload || CAST(:p AS jsonb), programada_para = :when WHERE id = :i"),
                         {"p": json.dumps({"error": str(e), "intentos": intentos + 1}), "when": datetime.now(UTC) + backoff(intentos + 1), "i": n.id})
        return "reintento"
    await _mark(db, n, "enviada", {"intentos": intentos + 1})
    return "enviada"


async def _mark(db: AsyncSession, n: Any, estado: str, extra: dict[str, Any]) -> None:
    await db.execute(text("UPDATE notifications SET estado = :e, enviada_en = CASE WHEN :e = 'enviada' THEN now() ELSE enviada_en END, "
                          "payload = payload || CAST(:p AS jsonb) WHERE id = :i"), {"e": estado, "p": json.dumps(extra), "i": n.id})


async def process_pending(db: AsyncSession, limit: int = 50, **kw: Any) -> dict[str, int]:
    """Sesión de plataforma. SKIP LOCKED: varios workers a la vez no envían dos veces la misma notificación."""
    rows = (await db.execute(text(
        "SELECT id, tenant_id, canal, destinatario, plantilla, payload FROM notifications "
        "WHERE estado = 'pendiente' AND programada_para <= now() AND canal <> 'interna' "
        "ORDER BY programada_para LIMIT :n FOR UPDATE SKIP LOCKED"), {"n": limit})).all()
    out: dict[str, int] = {}
    for n in rows:
        r = await deliver(db, n, **kw)
        out[r] = out.get(r, 0) + 1
    return out


async def queue_reminders(db: AsyncSession) -> int:
    """Recordatorio ~24 h antes. Idempotente: una sola vez por cita. WhatsApp si el negocio lo tiene activo; si no, correo."""
    r = await db.execute(text(
        "INSERT INTO notifications (tenant_id, canal, destinatario, plantilla, payload) "
        "SELECT a.tenant_id, CASE WHEN w.ok THEN 'whatsapp' ELSE 'email' END, "
        "       CASE WHEN w.ok THEN CASE WHEN length(d.digits) = 10 THEN '57' || d.digits ELSE d.digits END ELSE c.email::text END, "
        "       'recordatorio_cita', jsonb_build_object('appointment_id', a.id) "
        "FROM appointments a JOIN customers c ON c.tenant_id = a.tenant_id AND c.id = a.customer_id "
        "CROSS JOIN LATERAL (SELECT regexp_replace(coalesce(c.telefono, ''), '\\D', '', 'g') AS digits) d "
        "CROSS JOIN LATERAL (SELECT EXISTS (SELECT 1 FROM integrations i WHERE i.tenant_id = a.tenant_id AND i.tipo = 'meta_whatsapp' "
        "                    AND i.estado = 'activa') AS ok) w "
        "WHERE a.deleted_at IS NULL AND a.estado IN ('pendiente', 'confirmada') AND c.deleted_at IS NULL "
        "AND a.inicio >= now() + interval '23 hours' AND a.inicio < now() + interval '25 hours' "
        "AND ((w.ok AND length(d.digits) >= 7) OR (NOT w.ok AND c.email IS NOT NULL)) "
        "AND NOT EXISTS (SELECT 1 FROM notifications n WHERE n.tenant_id = a.tenant_id AND n.plantilla = 'recordatorio_cita' "
        "                AND n.payload->>'appointment_id' = a.id::text)"))
    return int(getattr(r, "rowcount", 0) or 0)

