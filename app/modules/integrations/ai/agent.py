"""Procesa UN mensaje entrante de WhatsApp: identifica contexto, aplica el límite del plan, corre el agente y responde.

Corre dentro de una sesión del negocio (RLS). Un candado por conversación evita respuestas dobles si el cliente escribe
varios mensajes seguidos: solo responde la ejecución del mensaje más reciente.
"""
import json
import logging
from datetime import datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import scoped_session
from app.core.errors import AppError
from app.core.repository import scoped
from app.modules.booking import rules as booking_rules
from app.modules.booking import service as booking
from app.modules.catalog.models import Service
from app.modules.integrations import service as integ
from app.modules.integrations.ai import agent_rules as rules
from app.modules.integrations.ai.provider import AIProvider, AIUnavailable, provider_for
from app.modules.integrations.ai.tools import ToolBox
from app.modules.integrations.models import Conversation, Message
from app.modules.staff.models import StaffProfile

log = logging.getLogger("app.ai")

TEXTO_LIMITE = ("Hemos llegado al límite de mensajes automáticos de este mes. Una persona del equipo te responderá en cuanto pueda. ¡Gracias por tu paciencia!")
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
TEXTO_NO_TEXTO = "Por ahora solo puedo leer mensajes de texto. ¿Me cuentas por escrito en qué te ayudo?"


def to_chat(history: list[Message]) -> list[dict[str, Any]]:
    """Mensajes del cliente -> user; IA y personas del equipo -> assistant. Se fusionan turnos seguidos del mismo rol y se
    garantiza que empiece con el cliente (requisito de la API)."""
    out: list[dict[str, Any]] = []
    for m in history:
        role = "user" if m.rol == "cliente" else "assistant" if m.rol in ("ia", "humano") else None
        if role is None or not m.contenido.strip():
            continue
        if out and out[-1]["role"] == role:
            out[-1]["content"] += "\n" + m.contenido
        else:
            out.append({"role": role, "content": m.contenido})
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


async def _lock(db: AsyncSession, conv_id: UUID) -> None:
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"conv:{conv_id}"})


async def process_message(db: AsyncSession, tenant_id: UUID, message_id: UUID, *, provider: AIProvider | None = None,
                          meta: Any = None) -> str:
    """Devuelve qué pasó: replied | escalated | limit | no_text | sin_ia | skipped_* — útil para logs y pruebas.
    Deja una marca `procesado` en el mensaje: el barrido de Celery solo reintenta los que quedaron sin marca (fallos reales)."""
    outcome = await _process(db, tenant_id, message_id, provider=provider, meta=meta)
    m = await db.get(Message, message_id)
    if m is not None:
        m.meta = {**(m.meta or {}), "procesado": outcome}
    return outcome


async def _process(db: AsyncSession, tenant_id: UUID, message_id: UUID, *, provider: AIProvider | None = None,
                   meta: Any = None) -> str:
    msg = await db.get(Message, message_id)
    if msg is None or msg.tenant_id != tenant_id or msg.rol != "cliente":
        return "skipped_invalid"
    conv = await db.get(Conversation, msg.conversation_id)
    assert conv is not None
    await _lock(db, conv.id)
    await db.refresh(conv)
    if conv.escalado_a_humano:
        return "skipped_escalated"                       # una persona atiende: el bot no interviene
    last_client = (await db.execute(select(Message.id).where(Message.tenant_id == tenant_id, Message.conversation_id == conv.id,
                                                             Message.rol == "cliente").order_by(Message.created_at.desc()).limit(1))).scalar_one()
    if last_client != msg.id:
        return "skipped_superseded"                      # llegó otro mensaje después: lo responde su propia ejecución
    answered = (await db.execute(select(Message.id).where(Message.tenant_id == tenant_id, Message.conversation_id == conv.id,
                                                          Message.rol.in_(("ia", "sistema", "humano")),
                                                          Message.created_at > msg.created_at).limit(1))).first()
    if answered:
        return "skipped_answered"                        # reintento de Celery: ya respondido

    wa = await integ.get_integration(db, tenant_id, integ.WA)
    ai = await integ.get_integration(db, tenant_id, integ.AI)
    if wa is None or ai is None or not (ai.config or {}).get("activo"):
        return "sin_ia"                                  # sin asistente activo: el mensaje queda en la bandeja para una persona
    usados, limite = await integ.ai_usage(db, tenant_id)
    if limite is None:
        return "sin_ia"
    cliente = meta or integ.meta_client_for(wa)

    async def say(texto: str, rol: str, extra: dict[str, Any] | None = None) -> None:
        try:
            await integ.send_to_conversation(db, tenant_id, conv, texto, rol, cliente, extra)
        except AppError as e:                            # WhatsApp no aceptó el mensaje: una persona debe verlo
            conv.escalado_a_humano, conv.escalado_motivo = True, f"no se pudo enviar la respuesta: {e.message}"[:300]

    if usados >= limite:                                 # límite mensual del plan: se avisa UNA vez y se pasa a una persona
        conv.escalado_a_humano, conv.escalado_motivo = True, "límite mensual de mensajes de IA alcanzado"
        await say(TEXTO_LIMITE, "sistema")
        return "limit"
    if (msg.meta or {}).get("tipo", "text") != "text":
        await say(TEXTO_NO_TEXTO, "sistema")
        return "no_text"

    ctx = await booking.load_ctx(db, tenant_id)
    tz = ZoneInfo(ctx.zona_horaria)
    n_ = datetime.now(tz)
    ahora = f"{DIAS[n_.weekday()]} {n_:%Y-%m-%d %H:%M}"
    toolbox = ToolBox(db, ctx, conv)
    cust = await toolbox._customer()
    services = [{"id": s.id, "nombre": s.nombre, "duracion_min": s.duracion_min, "precio": s.precio}
                for s in (await db.execute(scoped(Service, tenant_id).where(Service.activo.is_(True)).order_by(Service.nombre))).scalars()]
    escoger = ctx.s.modo_asignacion == "cliente_elige"
    staff = [{"id": s.id, "nombre": s.nombre_publico, "especialidad": s.especialidad}
             for s in (await db.execute(scoped(StaffProfile, tenant_id).where(StaffProfile.activo.is_(True)))).scalars()] if escoger else []
    cfg = ai.config or {}
    system = rules.build_system(
        negocio=ctx.negocio, tz=ctx.zona_horaria, ahora_local=ahora, servicios=services, profesionales=staff, escoger_profesional=escoger,
        cancelacion_horas=ctx.s.cancelacion_min_horas, nombre_asistente=cfg.get("nombre_asistente", "Asistente"),
        instrucciones=cfg.get("instrucciones", ""), cliente=(cust.nombre if cust else conv.nombre_contacto),
        texto_consentimiento=(ctx.s.textos or {}).get("consentimiento") or booking_rules.DEFAULT_CONSENT)

    n = get_settings().ai_history_messages
    history = list(reversed((await db.execute(select(Message).where(Message.tenant_id == tenant_id, Message.conversation_id == conv.id)
                                              .order_by(Message.created_at.desc()).limit(n))).scalars().all()))
    chat = to_chat(history)
    if not chat:
        return "skipped_invalid"
    try:
        prov = provider or provider_for(integ.creds_of(ai).get("api_key"))
        res = await rules.run_loop(prov, system, chat, toolbox.run, history_text=[m.contenido for m in history] + [ahora],
                                   max_rounds=get_settings().ai_max_tool_rounds)
    except AIUnavailable as e:
        log.warning("ia_no_disponible", extra={"tenant": str(tenant_id), "motivo": str(e)})
        conv.escalado_a_humano, conv.escalado_motivo = True, f"asistente no disponible: {e}"[:300]
        await say(rules.SAFE_FALLBACK, "sistema")
        return "escalated"

    extra = {"herramientas": res.tools_used, "rondas": res.rounds, "tokens_in": res.input_tokens, "tokens_out": res.output_tokens,
             "horarios_bloqueados": res.blocked_times}
    motivo = res.escalate or toolbox.escalado
    if motivo and not conv.escalado_a_humano:
        conv.escalado_a_humano, conv.escalado_motivo = True, motivo[:300]
        await db.execute(text("INSERT INTO notifications (tenant_id, canal, destinatario, plantilla, payload) VALUES "
                              "(:t, 'interna', 'recepcion', 'conversacion_escalada', CAST(:p AS jsonb))"),
                         {"t": tenant_id, "p": json.dumps({"conversation_id": str(conv.id), "motivo": motivo})})
    if res.text:
        await say(res.text, "ia", extra)                 # solo respuestas reales de la IA cuentan contra el límite del plan
    elif motivo:
        await say(rules.SAFE_FALLBACK, "sistema", extra)
    return "escalated" if motivo else "replied"



async def sweep_unanswered(limit: int = 50) -> int:
    """Red de seguridad: reprocesa mensajes de clientes que quedaron sin marca (el proceso cayó o falló a mitad).
    Solo mira los de los últimos 2 días y con más de 45 s, para no pisar a la tarea que acaba de lanzar el webhook."""
    async with scoped_session(platform=True) as db:
        rows = (await db.execute(text(
            "SELECT m.id, m.tenant_id FROM messages m JOIN conversations c ON c.tenant_id = m.tenant_id AND c.id = m.conversation_id "
            "WHERE m.rol = 'cliente' AND m.meta->>'procesado' IS NULL AND NOT c.escalado_a_humano "
            "AND m.created_at < now() - interval '45 seconds' AND m.created_at > now() - interval '2 days' "
            "ORDER BY m.created_at LIMIT :n"), {"n": limit})).all()
    hechos = 0
    for mid, tid in rows:
        try:
            async with scoped_session(tenant_id=tid) as db:
                await process_message(db, tid, mid)
            hechos += 1
        except Exception:                                  # noqa: BLE001 - un mensaje malo no frena al resto
            log.exception("barrido_fallo", extra={"tenant": str(tid), "mensaje": str(mid)})
    return hechos
