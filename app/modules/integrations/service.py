"""Integraciones: credenciales cifradas (Meta, IA), conversaciones, mensajes entrantes/salientes de WhatsApp.

Los secretos se guardan cifrados (Fernet) y NUNCA salen por la API: solo se informa si existen.
"""
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import scoped_session
from app.core.errors import AppError
from app.core.pagination import Page, PageParams
from app.core.repository import get_or_404, paginate
from app.core.security import decrypt_secret, encrypt_secret
from app.core.tenancy import Principal
from app.modules.audit import service as audit
from app.modules.integrations.ai.provider import AIUnavailable, provider_for
from app.modules.integrations.models import Conversation, Integration, Message
from app.modules.integrations.schemas import (
    AIIn,
    AIStatus,
    ConversationItem,
    MessageOut,
    ReplyOut,
    ValidationOut,
    WhatsAppIn,
    WhatsAppStatus,
)
from app.modules.integrations.whatsapp_meta.client import (
    Inbound,
    MetaClient,
    MetaError,
    StatusUpdate,
    split_text,
)

WA, AI = "meta_whatsapp", "ai"


def _aud(db: AsyncSession, p: Principal, accion: str, i: Integration, despues: dict[str, Any] | None = None) -> None:
    audit.record(db, actor_tipo="user", actor_id=p.id, tenant_id=p.tid, accion=accion, entidad="integration",
                 entidad_id=i.id, despues=despues)       # jamás se audita un secreto


def _seal(d: dict[str, Any]) -> str:
    return encrypt_secret(json.dumps(d))


def creds_of(i: Integration | None) -> dict[str, Any]:
    return json.loads(decrypt_secret(i.credenciales_cifradas)) if i and i.credenciales_cifradas else {}


async def get_integration(db: AsyncSession, tenant_id: UUID, tipo: str) -> Integration | None:
    return (await db.execute(select(Integration).where(Integration.tenant_id == tenant_id, Integration.tipo == tipo))).scalar_one_or_none()


async def _upsert(db: AsyncSession, tenant_id: UUID, tipo: str) -> Integration:
    i = await get_integration(db, tenant_id, tipo)
    if i is None:
        i = Integration(tenant_id=tenant_id, tipo=tipo, config={}, estado="pendiente")
        db.add(i)
    return i


# ================================================================== WhatsApp (admin)
async def _webhook_url(db: AsyncSession, tenant_id: UUID) -> str:
    slug = (await db.execute(text("SELECT slug FROM tenants WHERE id = :t"), {"t": tenant_id})).scalar_one()
    return f"{get_settings().api_public_url.rstrip('/')}/api/v1/webhooks/whatsapp/{slug}"


async def whatsapp_status(db: AsyncSession, p: Principal, *, verify_token: str | None = None) -> WhatsAppStatus:
    i = await get_integration(db, p.tid, WA)
    if i is None:
        return WhatsAppStatus(configurado=False, webhook_url=await _webhook_url(db, p.tid))
    c = creds_of(i)
    return WhatsAppStatus(configurado=True, phone_number_id=i.identificador_externo, estado=i.estado, ultima_verificacion=i.ultima_verificacion,
                          webhook_url=await _webhook_url(db, p.tid), tiene_token=bool(c.get("access_token")),
                          tiene_app_secret=bool(c.get("app_secret")), plantillas=(i.config or {}).get("plantillas", {}), verify_token=verify_token)


async def put_whatsapp(db: AsyncSession, p: Principal, d: WhatsAppIn) -> WhatsAppStatus:
    i = await _upsert(db, p.tid, WA)
    c = creds_of(i)
    c["access_token"] = d.access_token or c.get("access_token")
    c["app_secret"] = d.app_secret or c.get("app_secret")
    c["verify_token"] = d.verify_token or c.get("verify_token") or secrets.token_urlsafe(16)
    for campo in ("access_token", "app_secret"):
        if not c.get(campo):
            raise AppError("VALIDATION_ERROR", f"Falta {campo}", {"campo": campo})
    i.credenciales_cifradas, i.identificador_externo = _seal(c), d.phone_number_id
    i.config = {**(i.config or {}), "plantillas": d.plantillas}
    i.estado = "pendiente"                                 # hay que volver a validar con Meta
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError as e:
        raise AppError("CONFLICT", "Ese número de WhatsApp ya está conectado a otro negocio") from e
    _aud(db, p, "whatsapp_configurado", i, {"phone_number_id": d.phone_number_id})
    return await whatsapp_status(db, p, verify_token=c["verify_token"])


async def validate_whatsapp(db: AsyncSession, p: Principal, http: httpx.AsyncClient | None = None) -> ValidationOut:
    i = await get_integration(db, p.tid, WA)
    if i is None:
        raise AppError("INTEGRATION_NOT_CONFIGURED", "Primero guarda las credenciales de WhatsApp")
    c = creds_of(i)
    i.ultima_verificacion = datetime.now(UTC)
    try:
        info = await MetaClient(i.identificador_externo or "", c.get("access_token", ""), http).phone_info()
    except MetaError as e:
        i.estado = "error"
        return ValidationOut(valida=False, detalle=f"Meta respondió: {e.message}")
    i.estado = "activa"
    _aud(db, p, "whatsapp_validado", i)
    return ValidationOut(valida=True, numero=info.get("display_phone_number"), nombre_verificado=info.get("verified_name"))


async def delete_integration(db: AsyncSession, p: Principal, tipo: str) -> None:
    i = await get_integration(db, p.tid, tipo)
    if i is None:
        raise AppError("NOT_FOUND", "Integración no configurada")
    _aud(db, p, "integracion_eliminada", i, {"tipo": tipo})
    await db.delete(i)


# ================================================================== IA (admin)
async def ai_usage(db: AsyncSession, tenant_id: UUID) -> tuple[int, int | None]:
    """(mensajes de IA enviados este mes, límite del plan). El mes se cuenta en la zona horaria del negocio."""
    tz = (await db.execute(text("SELECT zona_horaria FROM tenants WHERE id = :t"), {"t": tenant_id})).scalar_one()
    hoy = datetime.now(ZoneInfo(tz))
    inicio = hoy.replace(day=1, hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
    usados = (await db.execute(select(func.count()).select_from(Message).where(
        Message.tenant_id == tenant_id, Message.rol == "ia", Message.created_at >= inicio))).scalar_one()
    limite = (await db.execute(text(
        "SELECT p.max_mensajes_ia_mes FROM tenant_subscriptions s JOIN plans p ON p.id = s.plan_id "
        "WHERE s.tenant_id = :t AND s.estado = 'vigente'"), {"t": tenant_id})).scalar()
    return usados, limite


async def ai_status(db: AsyncSession, p: Principal) -> AIStatus:
    i = await get_integration(db, p.tid, AI)
    cfg = (i.config if i else {}) or {}
    usados, limite = await ai_usage(db, p.tid)
    return AIStatus(activo=bool(cfg.get("activo")), nombre_asistente=cfg.get("nombre_asistente", "Asistente"),
                    instrucciones=cfg.get("instrucciones", ""), usa_clave_propia=bool(creds_of(i).get("api_key")),
                    estado=i.estado if i else None, ultima_verificacion=i.ultima_verificacion if i else None,
                    mensajes_usados_mes=usados, limite_mensual=limite)


async def put_ai(db: AsyncSession, p: Principal, d: AIIn) -> AIStatus:
    i = await _upsert(db, p.tid, AI)
    c = creds_of(i)
    if d.quitar_api_key:
        c.pop("api_key", None)
    elif d.api_key:
        c["api_key"] = d.api_key
    i.credenciales_cifradas = _seal(c) if c else None
    i.config = {"activo": d.activo, "nombre_asistente": d.nombre_asistente.strip(), "instrucciones": d.instrucciones.strip()}
    i.estado = "pendiente" if d.activo else "desactivada"
    await db.flush()
    _aud(db, p, "ia_configurada", i, {"activo": d.activo, "clave_propia": bool(c.get("api_key"))})
    return await ai_status(db, p)


async def validate_ai(db: AsyncSession, p: Principal, provider: Any = None) -> ValidationOut:
    i = await get_integration(db, p.tid, AI)
    if i is None:
        raise AppError("INTEGRATION_NOT_CONFIGURED", "Primero configura el asistente")
    i.ultima_verificacion = datetime.now(UTC)
    try:
        await (provider or provider_for(creds_of(i).get("api_key"))).ping()
    except AIUnavailable as e:
        i.estado = "error"
        return ValidationOut(valida=False, detalle=str(e))
    i.estado = "activa" if (i.config or {}).get("activo") else "desactivada"
    return ValidationOut(valida=True)


# ================================================================== webhook (público, firmado)
@dataclass(frozen=True)
class WebhookTarget:
    tenant_id: UUID
    phone_number_id: str
    app_secret: str
    verify_token: str


async def webhook_target(slug: str) -> WebhookTarget | None:
    """Resuelve el negocio por la dirección del webhook. Sin tenant todavía: modo plataforma, solo lectura."""
    async with scoped_session(platform=True) as db:
        row = (await db.execute(text("SELECT id FROM tenants WHERE slug = :s AND deleted_at IS NULL"), {"s": slug})).first()
        if row is None:
            return None
        i = await get_integration(db, row.id, WA)
        c = creds_of(i)
        if i is None or not c.get("app_secret") or not i.identificador_externo:
            return None
        return WebhookTarget(row.id, i.identificador_externo, c["app_secret"], c.get("verify_token", ""))


_RANK = {"fallido": 0, "recibido": 1, "pendiente": 1, "enviado": 2, "entregado": 3, "leido": 4}
_STATUS = {"sent": "enviado", "delivered": "entregado", "read": "leido", "failed": "fallido"}


async def apply_status(db: AsyncSession, tenant_id: UUID, st: StatusUpdate) -> None:
    m = (await db.execute(select(Message).where(Message.tenant_id == tenant_id, Message.id_externo == st.wamid))).scalar_one_or_none()
    nuevo = _STATUS.get(st.estado)
    if m is None or nuevo is None:
        return
    if nuevo == "fallido" or _RANK.get(nuevo, 0) > _RANK.get(m.estado, 0):       # nunca se retrocede (leído → entregado)
        m.estado = nuevo
        if st.error:
            m.meta = {**(m.meta or {}), "error_entrega": st.error}


async def ingest(db: AsyncSession, tenant_id: UUID, m: Inbound) -> UUID | None:
    """Guarda el mensaje entrante (idempotente por wamid). Devuelve su id si hay que procesarlo, None si es repetido."""
    now = datetime.now(UTC)
    cid = (await db.execute(text(
        "INSERT INTO conversations (tenant_id, canal, contacto_externo, nombre_contacto, ultimo_mensaje_en, ultimo_mensaje_cliente_en) "
        "VALUES (:t, 'whatsapp', :c, :n, :now, :now) ON CONFLICT (tenant_id, canal, contacto_externo) DO UPDATE SET "
        "nombre_contacto = coalesce(EXCLUDED.nombre_contacto, conversations.nombre_contacto), "
        "ultimo_mensaje_en = EXCLUDED.ultimo_mensaje_en, ultimo_mensaje_cliente_en = EXCLUDED.ultimo_mensaje_cliente_en RETURNING id"),
        {"t": tenant_id, "c": m.wa_id, "n": m.nombre, "now": now})).scalar_one()
    mid: UUID | None = (await db.execute(text(
        "INSERT INTO messages (tenant_id, conversation_id, rol, contenido, estado, id_externo, meta) "
        "VALUES (:t, :c, 'cliente', :x, 'recibido', :w, CAST(:meta AS jsonb)) "
        "ON CONFLICT (tenant_id, id_externo) WHERE id_externo IS NOT NULL DO NOTHING RETURNING id"),
        {"t": tenant_id, "c": cid, "x": (m.texto or f"[{m.tipo}]")[:4000], "w": m.wamid, "meta": json.dumps({"tipo": m.tipo})})).scalar()
    if mid is None:
        return None                                              # Meta reintentó: ya lo teníamos
    phone = "".join(ch for ch in m.wa_id if ch.isdigit())
    phone = phone[2:] if len(phone) == 12 and phone.startswith("57") else phone
    await db.execute(text(
        "UPDATE conversations SET customer_id = (SELECT id FROM customers WHERE tenant_id = :t AND deleted_at IS NULL "
        "AND regexp_replace(coalesce(telefono, ''), '\\D', '', 'g') IN (:d, :d57) ORDER BY created_at LIMIT 1) "
        "WHERE id = :c AND customer_id IS NULL"), {"t": tenant_id, "c": cid, "d": phone, "d57": "57" + phone})
    return mid


# ================================================================== envío saliente
def meta_client_for(i: Integration, http: httpx.AsyncClient | None = None) -> MetaClient:
    return MetaClient(i.identificador_externo or "", creds_of(i).get("access_token", ""), http)


async def send_to_conversation(db: AsyncSession, tenant_id: UUID, conv: Conversation, texto: str, rol: str, meta: Any,
                               extra: dict[str, Any] | None = None) -> Message:
    """Envía por WhatsApp y guarda el mensaje. Si Meta falla, el mensaje queda `fallido` (y se relanza el error)."""
    partes = split_text(texto) or [texto]
    last: Message | None = None
    for parte in partes:
        msg = Message(tenant_id=tenant_id, conversation_id=conv.id, rol=rol, contenido=parte, estado="pendiente", meta=extra or {})
        db.add(msg)
        await db.flush()
        try:
            msg.id_externo, msg.estado = await meta.send_text(conv.contacto_externo, parte) or None, "enviado"
        except MetaError as e:
            msg.estado, msg.meta = "fallido", {**(extra or {}), "error_envio": e.message}
            await db.flush()
            raise AppError("WHATSAPP_SEND_FAILED", f"WhatsApp no aceptó el mensaje: {e.message}") from e
        last = msg
    conv.ultimo_mensaje_en = datetime.now(UTC)
    await db.flush()
    assert last is not None
    return last


# ================================================================== conversaciones (admin)
async def list_conversations(db: AsyncSession, p: Principal, pp: PageParams, escalada: bool | None) -> Page[ConversationItem]:
    stmt = select(Conversation).where(Conversation.tenant_id == p.tid)
    if escalada is not None:
        stmt = stmt.where(Conversation.escalado_a_humano.is_(escalada))
    rows, total = await paginate(db, stmt, pp, {"ultimo_mensaje_en": Conversation.ultimo_mensaje_en}, "-ultimo_mensaje_en")
    items = []
    for c in rows:
        last = (await db.execute(select(Message).where(Message.tenant_id == p.tid, Message.conversation_id == c.id)
                                 .order_by(Message.created_at.desc()).limit(1))).scalar_one_or_none()
        items.append(ConversationItem(id=c.id, contacto=c.contacto_externo, nombre=c.nombre_contacto, customer_id=c.customer_id,
                                      escalado=c.escalado_a_humano, motivo=c.escalado_motivo, ultimo_mensaje_en=c.ultimo_mensaje_en,
                                      ultimo_texto=last.contenido[:140] if last else None, ultimo_rol=last.rol if last else None,
                                      sin_responder=bool(last and last.rol == "cliente")))
    return Page[ConversationItem](items=items, page=pp.page, page_size=pp.page_size, total=total)


async def list_messages(db: AsyncSession, p: Principal, cid: UUID, limit: int = 100) -> list[MessageOut]:
    await get_or_404(db, Conversation, cid, p.tid, "Conversación")
    rows = (await db.execute(select(Message).where(Message.tenant_id == p.tid, Message.conversation_id == cid)
                             .order_by(Message.created_at.desc()).limit(limit))).scalars().all()
    return [MessageOut.model_validate(m) for m in reversed(rows)]


def window_open(conv: Conversation, now: datetime | None = None) -> bool:
    """WhatsApp solo permite texto libre dentro de las 24 h siguientes al último mensaje del cliente."""
    last = conv.ultimo_mensaje_cliente_en
    return bool(last and (now or datetime.now(UTC)) - last <= timedelta(hours=get_settings().whatsapp_window_hours))


async def human_reply(db: AsyncSession, p: Principal, cid: UUID, texto: str, meta: Any = None, http: httpx.AsyncClient | None = None) -> ReplyOut:
    conv = await get_or_404(db, Conversation, cid, p.tid, "Conversación")
    i = await get_integration(db, p.tid, WA)
    if i is None:
        raise AppError("INTEGRATION_NOT_CONFIGURED", "WhatsApp no está configurado")
    if not window_open(conv):
        raise AppError("WHATSAPP_WINDOW_CLOSED", "Pasaron más de 24 h desde el último mensaje del cliente: WhatsApp solo permite plantillas aprobadas")
    msg = await send_to_conversation(db, p.tid, conv, texto, "humano", meta or meta_client_for(i, http), {"por": str(p.id)})
    conv.escalado_a_humano, conv.escalado_motivo = True, conv.escalado_motivo or "atendida por una persona"   # el bot se calla mientras una persona escribe
    return ReplyOut(mensaje=MessageOut.model_validate(msg), escalado=True)


async def resolve_conversation(db: AsyncSession, p: Principal, cid: UUID) -> None:
    conv = await get_or_404(db, Conversation, cid, p.tid, "Conversación")
    conv.escalado_a_humano, conv.escalado_motivo = False, None
