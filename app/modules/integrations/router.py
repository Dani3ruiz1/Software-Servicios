import hmac
import json
import logging
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import scoped_session
from app.core.errors import AppError
from app.core.pagination import Page, PageParams, page_params
from app.core.ratelimit import rate_limit
from app.core.rbac import require_permission
from app.core.tenancy import Principal, get_db, require_module
from app.modules.integrations import service
from app.modules.integrations.ai import agent
from app.modules.integrations.schemas import (
    AIIn,
    AIStatus,
    ConversationItem,
    MessageOut,
    ReplyIn,
    ReplyOut,
    ValidationOut,
    WhatsAppIn,
    WhatsAppStatus,
)
from app.modules.integrations.whatsapp_meta.client import parse_webhook, verify_signature

log = logging.getLogger("app.webhook")
_manage = require_permission("integrations:manage")
_chat = require_permission("conversations:manage")

# ================================================================== administración
router = APIRouter(tags=["integrations (admin)"])


@router.get("/integrations/whatsapp", response_model=WhatsAppStatus, dependencies=[Depends(require_module("whatsapp"))],
            summary="Estado de WhatsApp y URL del webhook que se configura en Meta (los secretos nunca se devuelven)")
async def get_whatsapp(p: Principal = Depends(_manage), db: AsyncSession = Depends(get_db)) -> WhatsAppStatus:
    return await service.whatsapp_status(db, p)


@router.put("/integrations/whatsapp", response_model=WhatsAppStatus, dependencies=[Depends(require_module("whatsapp"))],
            summary="Guarda credenciales cifradas. La respuesta trae el `verify_token` UNA vez: cópialo en Meta")
async def put_whatsapp(body: WhatsAppIn, p: Principal = Depends(_manage), db: AsyncSession = Depends(get_db)) -> WhatsAppStatus:
    return await service.put_whatsapp(db, p, body)


@router.post("/integrations/whatsapp/validate", response_model=ValidationOut, dependencies=[Depends(require_module("whatsapp"))],
             summary="Prueba el token y el número contra Meta")
async def validate_whatsapp(p: Principal = Depends(_manage), db: AsyncSession = Depends(get_db)) -> ValidationOut:
    return await service.validate_whatsapp(db, p)


@router.delete("/integrations/whatsapp", status_code=204, dependencies=[Depends(require_module("whatsapp"))])
async def delete_whatsapp(p: Principal = Depends(_manage), db: AsyncSession = Depends(get_db)) -> Response:
    await service.delete_integration(db, p, service.WA)
    return Response(status_code=204)


@router.get("/integrations/ai", response_model=AIStatus, dependencies=[Depends(require_module("ai"))],
            summary="Asistente de IA: configuración y consumo de mensajes del mes frente al límite del plan")
async def get_ai(p: Principal = Depends(_manage), db: AsyncSession = Depends(get_db)) -> AIStatus:
    return await service.ai_status(db, p)


@router.put("/integrations/ai", response_model=AIStatus, dependencies=[Depends(require_module("ai"))])
async def put_ai(body: AIIn, p: Principal = Depends(_manage), db: AsyncSession = Depends(get_db)) -> AIStatus:
    return await service.put_ai(db, p, body)


@router.post("/integrations/ai/validate", response_model=ValidationOut, dependencies=[Depends(require_module("ai"))],
             summary="Hace una llamada mínima al proveedor de IA con la clave que se usará")
async def validate_ai(p: Principal = Depends(_manage), db: AsyncSession = Depends(get_db)) -> ValidationOut:
    return await service.validate_ai(db, p)


@router.get("/conversations", response_model=Page[ConversationItem], dependencies=[Depends(require_module("whatsapp"))],
            summary="Bandeja de WhatsApp. `escalada=true` = las que esperan a una persona")
async def list_conversations(escalada: bool | None = None, pp: PageParams = Depends(page_params), p: Principal = Depends(_chat),
                             db: AsyncSession = Depends(get_db)) -> Page[ConversationItem]:
    return await service.list_conversations(db, p, pp, escalada)


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageOut], dependencies=[Depends(require_module("whatsapp"))])
async def messages(conversation_id: UUID, p: Principal = Depends(_chat), db: AsyncSession = Depends(get_db)) -> list[MessageOut]:
    return await service.list_messages(db, p, conversation_id)


@router.post("/conversations/{conversation_id}/reply", response_model=ReplyOut, status_code=201, dependencies=[Depends(require_module("whatsapp"))],
             summary="Una persona responde. El bot se calla hasta `resolve`. 409 si pasaron 24 h desde el último mensaje del cliente")
async def reply(conversation_id: UUID, body: ReplyIn, p: Principal = Depends(_chat), db: AsyncSession = Depends(get_db)) -> ReplyOut:
    return await service.human_reply(db, p, conversation_id, body.texto)


@router.post("/conversations/{conversation_id}/resolve", status_code=204, dependencies=[Depends(require_module("whatsapp"))],
             summary="Devuelve la conversación al asistente")
async def resolve(conversation_id: UUID, p: Principal = Depends(_chat), db: AsyncSession = Depends(get_db)) -> Response:
    await service.resolve_conversation(db, p, conversation_id)
    return Response(status_code=204)


# ================================================================== webhook de Meta (público, autenticado por firma)
webhook_router = APIRouter(prefix="/webhooks", tags=["webhooks"])
MAX_BODY = 1_000_000


@webhook_router.get("/whatsapp/{slug}", response_class=PlainTextResponse, include_in_schema=True,
                    summary="Verificación del webhook (Meta llama aquí al registrarlo)")
async def verify(slug: str, hub_mode: str = Query("", alias="hub.mode"), hub_verify_token: str = Query("", alias="hub.verify_token"),
                 hub_challenge: str = Query("", alias="hub.challenge")) -> str:
    t = await service.webhook_target(slug)
    if t is None or hub_mode != "subscribe" or not t.verify_token or not hmac.compare_digest(t.verify_token, hub_verify_token):
        raise AppError("FORBIDDEN", "Verificación rechazada")           # misma respuesta con slug inexistente o token malo
    return hub_challenge


async def run_incoming(tenant_id: UUID, message_id: UUID) -> None:
    """Se ejecuta fuera de la petición (Meta ya recibió su 200). Si falla, el barrido de Celery lo reintenta."""
    try:
        async with scoped_session(tenant_id=tenant_id) as db:
            outcome = await agent.process_message(db, tenant_id, message_id)
        log.info("mensaje_procesado", extra={"tenant": str(tenant_id), "resultado": outcome})
    except Exception:                                                   # noqa: BLE001 - nunca romper la tarea de fondo
        log.exception("fallo_procesando_mensaje", extra={"tenant": str(tenant_id), "mensaje": str(message_id)})


@webhook_router.post("/whatsapp/{slug}", dependencies=[Depends(rate_limit("wa-hook", 600, 60))],
                     summary="Mensajes y estados de entrega. Exige firma X-Hub-Signature-256 válida; responde 200 enseguida")
async def receive(slug: str, request: Request, background: BackgroundTasks) -> dict[str, bool]:
    raw = await request.body()
    if len(raw) > MAX_BODY:
        raise AppError("VALIDATION_ERROR", "Cuerpo demasiado grande", status=413)
    t = await service.webhook_target(slug)
    if t is None or not verify_signature(t.app_secret, raw, request.headers.get("x-hub-signature-256")):
        raise AppError("UNAUTHORIZED", "Firma inválida")                # el cuerpo NO se lee antes de validar la firma
    try:
        parsed = parse_webhook(json.loads(raw))
    except (ValueError, UnicodeDecodeError) as e:
        raise AppError("VALIDATION_ERROR", "JSON inválido") from e
    pendientes: list[UUID] = []
    async with scoped_session(tenant_id=t.tenant_id) as db:
        for st in parsed.estados:
            if st.phone_number_id == t.phone_number_id:
                await service.apply_status(db, t.tenant_id, st)
        for m in parsed.mensajes:
            if m.phone_number_id == t.phone_number_id:                  # un número ajeno dentro de un payload firmado se ignora
                mid = await service.ingest(db, t.tenant_id, m)
                if mid:
                    pendientes.append(mid)
    for mid in pendientes:
        background.add_task(run_incoming, t.tenant_id, mid)
    return {"ok": True}
