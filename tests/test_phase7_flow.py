"""Fase 7 contra Postgres real. Meta y la IA son SIMULADOS (no hay red): se prueba todo lo nuestro de punta a punta.

Importante: las pruebas inyectan `FakeMeta` y un modelo guionado; nada llama a graph.facebook.com ni a api.anthropic.com.
"""
import asyncio
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import text
from test_phase4_flow import _customer, _setup
from test_phase5_flow import _svc
from test_phase6_flow import _pro

from app.core.db import scoped_session
from app.core.errors import AppError
from app.core.pagination import PageParams
from app.main import app
from app.modules.appointments import service as apps
from app.modules.appointments.schemas import AppointmentIn
from app.modules.booking import rules as brules
from app.modules.integrations import router as irouter
from app.modules.integrations import service as integ
from app.modules.integrations.ai import agent
from app.modules.integrations.ai.provider import AIResult, AIUnavailable, ToolCall
from app.modules.integrations.schemas import AIIn, WhatsAppIn
from app.modules.integrations.whatsapp_meta.client import Inbound, MetaError, sign
from app.modules.notifications import service as notif

TZ = ZoneInfo("America/Bogota")
TOKEN = "EAAG" + "x" * 40


@pytest.fixture(autouse=True)
async def _needs_db():
    """Si no hay Postgres con el esquema cargado, TODAS las pruebas de este archivo se omiten (no fallan)."""
    try:
        async with scoped_session(platform=True) as db:
            await db.execute(text("SELECT 1 FROM plans LIMIT 1"))
    except Exception:                                                              # noqa: BLE001
        pytest.skip("Postgres no disponible")


class FakeMeta:
    def __init__(self, fail: MetaError | None = None):
        self.sends: list[tuple[str, str]] = []
        self.templates: list[tuple[str, str, list[str]]] = []
        self.fail = fail

    async def send_text(self, to, body):
        if self.fail:
            raise self.fail
        self.sends.append((to, body))
        return f"wamid.OUT{len(self.sends)}"

    async def send_template(self, to, name, params, lang="es"):
        if self.fail:
            raise self.fail
        self.templates.append((to, name, params))
        return f"wamid.TPL{len(self.templates)}"


class Planner:
    """Modelo de mentira que actúa como un buen asistente: consulta → reserva → confirma con datos de las herramientas."""

    def __init__(self, svc: UUID, day, *, consent=True, invent_first=False, nombre="Carlos Pérez"):
        self.svc, self.day, self.consent, self.invent, self.nombre, self.calls = svc, day, consent, invent_first, nombre, 0

    async def complete(self, *, system, messages, tools):
        self.calls += 1
        results = [m["content"][0]["content"] for m in messages if m["role"] == "user" and isinstance(m["content"], list)]
        if self.invent and self.calls == 1:
            return AIResult(text="¡Claro! Te agendo a las 03:17.")                      # horario inventado
        if not results:
            return AIResult(tool_calls=[ToolCall("t1", "consultar_disponibilidad", {"fecha_desde": self.day.isoformat(), "servicio_ids": [str(self.svc)]})],
                            assistant_content=["a"])
        if len(results) == 1:
            slot = json.loads(results[0])["dias"][0]["horarios"][0]
            return AIResult(tool_calls=[ToolCall("t2", "crear_reserva", {
                "servicio_ids": [str(self.svc)], "inicio": slot["inicio"], "nombre_cliente": self.nombre,
                "acepta_tratamiento_datos": self.consent})], assistant_content=["b"])
        out = json.loads(results[1])
        if "error" in out:
            return AIResult(text="Necesito tu autorización para tratar tus datos. ¿Me la das?")
        return AIResult(text=f"Listo, tu cita quedó a las {out['hora']}.", input_tokens=900, output_tokens=40)


class Static:
    def __init__(self, *results):
        self.results = list(results)

    async def complete(self, *, system, messages, tools):
        return self.results.pop(0)


async def _sql(sql: str, **prm):
    async with scoped_session(platform=True) as db:
        return (await db.execute(text(sql), prm)).all()


async def _wa(plan="Premium", ai=True, plantillas=None):
    p = await _setup(plan)
    pid, secret = str(uuid4().int)[:12], f"secreto-{uuid4().hex}"
    async with scoped_session(tenant_id=p.tenant_id) as db:
        await integ.put_whatsapp(db, p, WhatsAppIn(phone_number_id=pid, access_token=TOKEN, app_secret=secret, plantillas=plantillas or {}))
        if ai:
            await integ.put_ai(db, p, AIIn(activo=True, nombre_asistente="Lupe"))
    slug = (await _sql("SELECT slug FROM tenants WHERE id = :t", t=p.tenant_id))[0].slug
    return p, pid, secret, slug


async def _in(p, pid, texto="Hola, quiero un corte", wa="573001234567", tipo="text", wamid=None, nombre="Carlos Pérez"):
    async with scoped_session(tenant_id=p.tenant_id) as db:
        return await integ.ingest(db, p.tenant_id, Inbound(pid, wa, wamid or f"wamid.{uuid4().hex}", tipo, texto if tipo == "text" else None, nombre))


async def _run(p, mid, provider=None, meta=None):
    async with scoped_session(tenant_id=p.tenant_id) as db:
        return await agent.process_message(db, p.tenant_id, mid, provider=provider, meta=meta)


def _day():
    return (datetime.now(UTC) + timedelta(days=3)).astimezone(TZ).date()


# ================================================================== credenciales
async def test_whatsapp_credentials_are_encrypted_and_never_returned():
    p = await _setup("Premium")
    pid = str(uuid4().int)[:12]
    async with scoped_session(tenant_id=p.tenant_id) as db:
        st = await integ.put_whatsapp(db, p, WhatsAppIn(phone_number_id=pid, access_token=TOKEN, app_secret="el-secreto-de-la-app"))
    assert st.configurado and st.tiene_token and st.tiene_app_secret and st.estado == "pendiente" and st.verify_token
    assert st.webhook_url.endswith("/api/v1/webhooks/whatsapp/" + (await _sql("SELECT slug FROM tenants WHERE id = :t", t=p.tenant_id))[0].slug)
    raw = (await _sql("SELECT credenciales_cifradas, identificador_externo FROM integrations WHERE tenant_id = :t AND tipo = 'meta_whatsapp'", t=p.tenant_id))[0]
    assert TOKEN not in raw.credenciales_cifradas and "el-secreto" not in raw.credenciales_cifradas and raw.identificador_externo == pid
    async with scoped_session(tenant_id=p.tenant_id) as db:
        again = await integ.whatsapp_status(db, p)
        assert again.verify_token is None and again.tiene_token                                 # el verify_token solo se ve al guardar
        kept = await integ.put_whatsapp(db, p, WhatsAppIn(phone_number_id=pid))                  # sin secretos nuevos: conserva los viejos
        assert kept.tiene_token and kept.verify_token == st.verify_token
    other = await _setup("Premium")
    with pytest.raises(AppError) as e:                                                          # el mismo número en otro negocio
        async with scoped_session(tenant_id=other.tenant_id) as db:
            await integ.put_whatsapp(db, other, WhatsAppIn(phone_number_id=pid, access_token=TOKEN, app_secret="otro-secreto-123"))
    assert e.value.code == "CONFLICT"
    with pytest.raises(AppError) as e:
        async with scoped_session(tenant_id=other.tenant_id) as db:
            await integ.put_whatsapp(db, other, WhatsAppIn(phone_number_id=str(uuid4().int)[:12]))   # nuevo y sin credenciales
    assert e.value.code == "VALIDATION_ERROR"


async def test_whatsapp_validation_against_meta_marks_state():
    p, pid, _, _ = await _wa()
    ok = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"display_phone_number": "+57 300 111 2233", "verified_name": "Barbería Pepe"})))
    bad = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(401, json={"error": {"message": "Invalid OAuth access token", "code": 190}})))
    async with scoped_session(tenant_id=p.tenant_id) as db:
        v = await integ.validate_whatsapp(db, p, ok)
        assert v.valida and v.nombre_verificado == "Barbería Pepe" and (await integ.whatsapp_status(db, p)).estado == "activa"
        v2 = await integ.validate_whatsapp(db, p, bad)
        assert not v2.valida and "Invalid OAuth" in v2.detalle and TOKEN not in v2.detalle and (await integ.whatsapp_status(db, p)).estado == "error"
    with pytest.raises(AppError) as e:
        p2 = await _setup("Premium")
        async with scoped_session(tenant_id=p2.tenant_id) as db:
            await integ.validate_whatsapp(db, p2)
    assert e.value.code == "INTEGRATION_NOT_CONFIGURED"


async def test_ai_settings_usage_and_own_key():
    p = await _setup("Premium")
    async with scoped_session(tenant_id=p.tenant_id) as db:
        s = await integ.put_ai(db, p, AIIn(activo=True, nombre_asistente="Lupe", instrucciones="Sé breve", api_key="sk-ant-" + "k" * 30))
        assert s.usa_clave_propia and s.activo and s.limite_mensual == 3000 and s.mensajes_usados_mes == 0
        assert "sk-ant" not in json.dumps(s.model_dump(mode="json"))
        s2 = await integ.put_ai(db, p, AIIn(activo=True, quitar_api_key=True))
        assert not s2.usa_clave_propia
    raw = (await _sql("SELECT credenciales_cifradas FROM integrations WHERE tenant_id = :t AND tipo = 'ai'", t=p.tenant_id))[0].credenciales_cifradas
    assert raw is None                                                                          # sin clave propia no queda nada guardado
    async with scoped_session(tenant_id=p.tenant_id) as db:
        class Boom:
            async def ping(self):
                raise AIUnavailable("La clave de IA no es válida")
        v = await integ.validate_ai(db, p, Boom())
        assert not v.valida and "clave" in v.detalle and (await integ.ai_status(db, p)).estado == "error"


# ================================================================== webhook de punta a punta (firma real, sin red)
async def test_webhook_end_to_end_signature_idempotency_status_and_isolation(monkeypatch):
    p, pid, secret, slug = await _wa()
    other, opid, osecret, oslug = await _wa()
    seen = []

    async def spy(tenant_id, mid):
        seen.append((tenant_id, mid))
    monkeypatch.setattr(irouter, "run_incoming", spy)                                              # no se procesa: solo se verifica la entrada

    def payload(pid_, wamid="wamid.IN1", texto="Hola"):
        return json.dumps({"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {
            "metadata": {"phone_number_id": pid_}, "contacts": [{"profile": {"name": "Carlos Pérez"}, "wa_id": "573001234567"}],
            "messages": [{"from": "573001234567", "id": wamid, "type": "text", "text": {"body": texto}}]}}]}]}).encode()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        url = f"/api/v1/webhooks/whatsapp/{slug}"
        body = payload(pid)
        r = await c.post(url, content=body, headers={"X-Hub-Signature-256": sign("clave-equivocada", body)})
        assert r.status_code == 401 and not await _sql("SELECT 1 FROM messages WHERE tenant_id = :t", t=p.tenant_id)      # nada se guarda sin firma
        assert (await c.post(url, content=body, headers={"X-Hub-Signature-256": sign(secret, body)})).json() == {"ok": True}
        assert len(seen) == 1 and seen[0][0] == p.tenant_id
        await c.post(url, content=body, headers={"X-Hub-Signature-256": sign(secret, body)})                                # Meta reintenta
        assert len(seen) == 1 and (await _sql("SELECT count(*) AS n FROM messages WHERE tenant_id = :t", t=p.tenant_id))[0].n == 1
        conv = (await _sql("SELECT contacto_externo, nombre_contacto, ultimo_mensaje_cliente_en FROM conversations WHERE tenant_id = :t", t=p.tenant_id))[0]
        assert conv.contacto_externo == "573001234567" and conv.nombre_contacto == "Carlos Pérez" and conv.ultimo_mensaje_cliente_en

        wrong = payload(opid, "wamid.IN2")                                                                                    # número de OTRO negocio, firmado con la clave de ESTE
        await c.post(url, content=wrong, headers={"X-Hub-Signature-256": sign(secret, wrong)})
        assert len(seen) == 1 and not await _sql("SELECT 1 FROM messages WHERE id_externo = 'wamid.IN2'")
        cross = payload(pid, "wamid.IN3")                                                                                      # firmado con la clave del otro negocio
        assert (await c.post(url, content=cross, headers={"X-Hub-Signature-256": sign(osecret, cross)})).status_code == 401

        async with scoped_session(tenant_id=p.tenant_id) as db:                                                               # estado de entrega
            m = await integ.send_to_conversation(db, p.tenant_id, await db.get(integ.Conversation, (await _sql("SELECT id FROM conversations WHERE tenant_id = :t", t=p.tenant_id))[0].id),
                                                 "Hola", "ia", FakeMeta())
        st = json.dumps({"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {"metadata": {"phone_number_id": pid}, "statuses": [
            {"id": m.id_externo, "status": "read"}]}}]}]}).encode()
        await c.post(url, content=st, headers={"X-Hub-Signature-256": sign(secret, st)})
        assert (await _sql("SELECT estado FROM messages WHERE id = :i", i=m.id))[0].estado == "leido"
        late = st.replace(b'"read"', b'"delivered"')                                                                         # un estado viejo no retrocede
        await c.post(url, content=late, headers={"X-Hub-Signature-256": sign(secret, late)})
        assert (await _sql("SELECT estado FROM messages WHERE id = :i", i=m.id))[0].estado == "leido"


async def test_rls_isolates_conversations_between_businesses():
    a, pa, _, _ = await _wa()
    b, pb, _, _ = await _wa()
    await _in(a, pa)
    async with scoped_session(tenant_id=b.tenant_id) as db:
        assert (await db.execute(text("SELECT count(*) FROM conversations"))).scalar() == 0
        assert (await db.execute(text("SELECT count(*) FROM messages"))).scalar() == 0


# ================================================================== agente
async def test_agent_books_through_the_real_engine_and_replies_with_verified_data():
    p, pid, _, _ = await _wa()
    sv = await _svc(p, "Corte", 30000)
    await _pro(p, "Ana", sv)
    mid = await _in(p, pid)
    meta = FakeMeta()
    assert await _run(p, mid, Planner(sv, _day()), meta) == "replied"
    assert len(meta.sends) == 1 and meta.sends[0][0] == "573001234567" and meta.sends[0][1].startswith("Listo, tu cita quedó a las ")
    ap = (await _sql("SELECT origen, estado, created_by FROM appointments WHERE tenant_id = :t", t=p.tenant_id))[0]
    assert (ap.origen, ap.estado, ap.created_by) == ("whatsapp", "pendiente", None)
    c = (await _sql("SELECT nombre, telefono, origen, consentimiento_datos FROM customers WHERE tenant_id = :t", t=p.tenant_id))[0]
    assert (c.nombre, c.telefono, c.origen, c.consentimiento_datos) == ("Carlos Pérez", "3001234567", "whatsapp", True)
    out = (await _sql("SELECT rol, estado, id_externo, meta FROM messages WHERE tenant_id = :t AND rol = 'ia'", t=p.tenant_id))[0]
    assert out.estado == "enviado" and out.id_externo == "wamid.OUT1" and out.meta["herramientas"] == ["consultar_disponibilidad", "crear_reserva"]
    assert out.meta["tokens_in"] == 900 and (await _sql("SELECT meta->>'procesado' AS p FROM messages WHERE id = :i", i=mid))[0].p == "replied"
    async with scoped_session(tenant_id=p.tenant_id) as db:
        assert (await integ.ai_status(db, p)).mensajes_usados_mes == 1
    assert not await _sql("SELECT 1 FROM notifications WHERE tenant_id = :t AND plantilla = 'reserva_confirmada'", t=p.tenant_id)    # el chat ya confirmó


async def test_agent_blocks_an_invented_hour_before_it_reaches_the_customer():
    p, pid, _, _ = await _wa()
    sv = await _svc(p, "Corte", 30000)
    await _pro(p, "Ana", sv)
    meta = FakeMeta()
    assert await _run(p, await _in(p, pid), Planner(sv, _day(), invent_first=True), meta) == "replied"
    assert all("03:17" not in body for _, body in meta.sends)                                    # lo inventado jamás se envió
    assert meta.sends and "Listo, tu cita quedó a las" in meta.sends[0][1]
    ia = (await _sql("SELECT meta FROM messages WHERE tenant_id = :t AND rol = 'ia'", t=p.tenant_id))[0]
    assert ia.meta["horarios_bloqueados"] == ["03:17"]


async def test_booking_needs_explicit_consent_for_new_customers():
    p, pid, _, _ = await _wa()
    sv = await _svc(p, "Corte", 30000)
    await _pro(p, "Ana", sv)
    meta = FakeMeta()
    await _run(p, await _in(p, pid), Planner(sv, _day(), consent=False), meta)
    assert "autorización" in meta.sends[0][1]
    assert not await _sql("SELECT 1 FROM appointments WHERE tenant_id = :t", t=p.tenant_id)           # sin consentimiento no hay cita
    assert not await _sql("SELECT 1 FROM customers WHERE tenant_id = :t", t=p.tenant_id)              # ni cliente a medias


async def test_monthly_ai_limit_escalates_once_and_stops_the_bot():
    p, pid, _, _ = await _wa()
    plan = (await _sql("INSERT INTO plans (nombre, precio_mensual, max_usuarios, max_mensajes_ia_mes) VALUES (:n, 1, 5, 1) RETURNING id", n=f"limite-{uuid4().hex[:6]}"))[0].id
    async with scoped_session(platform=True) as db:
        await db.execute(text("UPDATE tenant_subscriptions SET plan_id = :p WHERE tenant_id = :t AND estado = 'vigente'"), {"p": plan, "t": p.tenant_id})
    meta = FakeMeta()
    assert await _run(p, await _in(p, pid, "hola"), Static(AIResult(text="¡Hola! ¿En qué te ayudo?")), meta) == "replied"          # 1 de 1
    out = await _run(p, await _in(p, pid, "otra cosa"), Static(AIResult(text="no debería llamarse")), meta)
    assert out == "limit" and "límite" in meta.sends[-1][1]
    assert (await _sql("SELECT escalado_a_humano, escalado_motivo FROM conversations WHERE tenant_id = :t", t=p.tenant_id))[0].escalado_a_humano
    assert await _run(p, await _in(p, pid, "¿hola?"), Static(), FakeMeta()) == "skipped_escalated"                                # ya no responde el bot
    assert (await _sql("SELECT count(*) AS n FROM messages WHERE tenant_id = :t AND rol = 'ia'", t=p.tenant_id))[0].n == 1         # el aviso de límite no cuenta


async def test_escalation_hands_over_notifies_reception_and_the_bot_stays_quiet():
    p, pid, _, _ = await _wa()
    prov = Static(AIResult(tool_calls=[ToolCall("e", "escalar_a_recepcion", {"motivo": "reclamo por cobro"})], assistant_content=["x"]),
                  AIResult(text="Entiendo, una persona del equipo te escribirá enseguida."))
    meta = FakeMeta()
    assert await _run(p, await _in(p, pid, "me cobraron de más!!"), prov, meta) == "escalated"
    assert meta.sends[0][1].startswith("Entiendo")
    c = (await _sql("SELECT escalado_a_humano, escalado_motivo FROM conversations WHERE tenant_id = :t", t=p.tenant_id))[0]
    assert c.escalado_a_humano and c.escalado_motivo == "reclamo por cobro"
    n = (await _sql("SELECT canal, destinatario, payload FROM notifications WHERE tenant_id = :t AND plantilla = 'conversacion_escalada'", t=p.tenant_id))
    assert len(n) == 1 and n[0].canal == "interna" and n[0].payload["motivo"] == "reclamo por cobro"
    assert await _run(p, await _in(p, pid, "¿alguien?"), Static(), FakeMeta()) == "skipped_escalated"


async def test_only_the_latest_message_is_answered_and_retries_do_not_duplicate():
    p, pid, _, _ = await _wa()
    m1, m2 = await _in(p, pid, "Hola"), await _in(p, pid, "quiero una cita")
    meta = FakeMeta()
    assert await _run(p, m1, Static(), meta) == "skipped_superseded"
    assert await _run(p, m2, Static(AIResult(text="¡Claro! ¿Qué servicio?")), meta) == "replied"
    assert await _run(p, m2, Static(), meta) == "skipped_answered"                               # Celery reintentó el mismo mensaje
    assert len(meta.sends) == 1


async def test_non_text_messages_get_a_fixed_reply_without_calling_the_model():
    p, pid, _, _ = await _wa()
    meta = FakeMeta()
    assert await _run(p, await _in(p, pid, tipo="audio"), Static(), meta) == "no_text"            # Static() sin resultados: si se llamara, fallaría
    assert "solo puedo leer mensajes de texto" in meta.sends[0][1]
    assert (await _sql("SELECT count(*) AS n FROM messages WHERE tenant_id = :t AND rol = 'ia'", t=p.tenant_id))[0].n == 0


async def test_provider_outage_and_whatsapp_send_failure_both_hand_over_to_a_person():
    p, pid, _, _ = await _wa()

    class Down:
        async def complete(self, **kw):
            raise AIUnavailable("El proveedor de IA está saturado")
    meta = FakeMeta()
    assert await _run(p, await _in(p, pid), Down(), meta) == "escalated"
    assert "persona del equipo" in meta.sends[0][1]
    c = (await _sql("SELECT escalado_motivo FROM conversations WHERE tenant_id = :t", t=p.tenant_id))[0]
    assert "saturado" in c.escalado_motivo

    p2, pid2, _, _ = await _wa()
    broken = FakeMeta(fail=MetaError(400, "Recipient phone number not in allowed list", 131030))
    await _run(p2, await _in(p2, pid2), Static(AIResult(text="Hola, ¿en qué te ayudo?")), broken)
    m = (await _sql("SELECT estado, meta FROM messages WHERE tenant_id = :t AND rol = 'ia'", t=p2.tenant_id))[0]
    assert m.estado == "fallido" and "allowed list" in m.meta["error_envio"]
    assert (await _sql("SELECT escalado_a_humano FROM conversations WHERE tenant_id = :t", t=p2.tenant_id))[0].escalado_a_humano


async def test_tools_only_touch_the_customers_own_appointments_and_respect_the_cancellation_window():
    p, pid, _, _ = await _wa()
    sv = await _svc(p, "Corte", 30000)
    await _pro(p, "Ana", sv)
    mine, theirs = await _customer(p), await _customer(p)
    async with scoped_session(platform=True) as db:
        await db.execute(text("UPDATE customers SET telefono = '3001234567' WHERE id = :i"), {"i": mine})
    far, near = datetime.now(UTC) + timedelta(days=5), datetime.now(UTC) + timedelta(hours=1, minutes=30)
    async with scoped_session(tenant_id=p.tenant_id) as db:
        own = await apps.create_appointment(db, p, AppointmentIn(customer_id=mine, inicio=far, service_ids=[sv]))
        late = await apps.create_appointment(db, p, AppointmentIn(customer_id=mine, inicio=near, service_ids=[sv]))
        alien = await apps.create_appointment(db, p, AppointmentIn(customer_id=theirs, inicio=far + timedelta(hours=3), service_ids=[sv]))

    def call(name, **a):
        return ToolCall("c", name, a)
    prov = Static(AIResult(tool_calls=[call("consultar_mis_citas"), call("cancelar_cita", cita_id=str(alien.id)), call("cancelar_cita", cita_id=str(late.id)),
                                       call("cancelar_cita", cita_id=str(own.id), motivo="viaje")], assistant_content=["x"]), AIResult(text="Listo, cancelé tu cita del viaje."))
    meta = FakeMeta()
    await _run(p, await _in(p, pid, "cancela mis citas"), prov, meta)
    est = {r.id: r.estado for r in await _sql("SELECT id, estado FROM appointments WHERE tenant_id = :t", t=p.tenant_id)}
    assert est[alien.id] == "pendiente" and est[late.id] == "pendiente" and est[own.id] == "cancelada"       # solo la propia y dentro de la ventana
    ia = (await _sql("SELECT meta FROM messages WHERE tenant_id = :t AND rol = 'ia'", t=p.tenant_id))[0]
    assert ia.meta["herramientas"] == ["consultar_mis_citas", "cancelar_cita", "cancelar_cita", "cancelar_cita"]


async def test_sweeper_reprocesses_only_unmarked_messages_and_never_twice():
    # El barrido es GLOBAL (todos los negocios): se marcan como procesados los mensajes sobrantes de otras pruebas/corridas
    # para que este test solo vea los suyos y nunca dispare envíos de otros negocios.
    async with scoped_session(platform=True) as db:
        await db.execute(text("UPDATE messages SET meta = meta || '{\"procesado\": \"limpieza-de-prueba\"}'::jsonb WHERE meta->>'procesado' IS NULL"))
    p, pid, _, _ = await _wa(ai=False)                           # sin IA activa: el procesamiento no llama a ningún proveedor ni a Meta
    mid = await _in(p, pid)
    assert await agent.sweep_unanswered() == 0                    # muy reciente (<45 s): lo atiende la tarea del webhook
    async with scoped_session(platform=True) as db:
        await db.execute(text("UPDATE messages SET created_at = now() - interval '2 minutes' WHERE id = :i"), {"i": mid})
    assert await agent.sweep_unanswered() == 1
    assert (await _sql("SELECT meta->>'procesado' AS p FROM messages WHERE id = :i", i=mid))[0].p == "sin_ia"
    assert await agent.sweep_unanswered() == 0                    # ya tiene marca: no se reprocesa jamás


# ================================================================== respuesta humana
async def test_human_reply_window_takeover_and_resolve():
    p, pid, _, _ = await _wa()
    mid = await _in(p, pid, "hola")
    cid = (await _sql("SELECT conversation_id FROM messages WHERE id = :i", i=mid))[0].conversation_id
    meta = FakeMeta()
    async with scoped_session(tenant_id=p.tenant_id) as db:
        r = await integ.human_reply(db, p, cid, "Hola Carlos, soy Laura. Te ayudo.", meta)
        assert r.escalado and r.mensaje.rol == "humano" and meta.sends == [("573001234567", "Hola Carlos, soy Laura. Te ayudo.")]
        page = await integ.list_conversations(db, p, PageParams(1, 20, None), True)
        assert page.total == 1 and page.items[0].escalado and not page.items[0].sin_responder
        assert [m.rol for m in await integ.list_messages(db, p, cid)] == ["cliente", "humano"]
        await integ.resolve_conversation(db, p, cid)
        assert (await integ.list_conversations(db, p, PageParams(1, 20, None), True)).total == 0
    async with scoped_session(platform=True) as db:
        await db.execute(text("UPDATE conversations SET ultimo_mensaje_cliente_en = now() - interval '25 hours' WHERE id = :i"), {"i": cid})
    with pytest.raises(AppError) as e:
        async with scoped_session(tenant_id=p.tenant_id) as db:
            await integ.human_reply(db, p, cid, "tarde", meta)
    assert e.value.code == "WHATSAPP_WINDOW_CLOSED" and len(meta.sends) == 1
    other = await _setup("Premium")
    with pytest.raises(AppError) as e:                                              # conversación de otro negocio: no existe para este
        async with scoped_session(tenant_id=other.tenant_id) as db:
            await integ.list_messages(db, other, cid)
    assert e.value.code == "NOT_FOUND"


# ================================================================== notificaciones
async def _clear_queue():
    async with scoped_session(platform=True) as db:
        await db.execute(text("UPDATE notifications SET estado = 'cancelada' WHERE estado = 'pendiente'"))


async def _notify(p, canal, dest, plantilla, payload):
    async with scoped_session(tenant_id=p.tenant_id) as db:
        return (await db.execute(text("INSERT INTO notifications (tenant_id, canal, destinatario, plantilla, payload) "
                                      "VALUES (:t, :c, :d, :p, CAST(:j AS jsonb)) RETURNING id"),
                                 {"t": p.tenant_id, "c": canal, "d": dest, "p": plantilla, "j": json.dumps(payload)})).scalar_one()


async def _drain(meta=None, mailer=None):
    async with scoped_session(platform=True) as db:
        kw = {}
        if meta is not None:
            kw["meta_factory"] = lambda i: meta
        if mailer is not None:
            kw["mailer"] = mailer
        return await notif.process_pending(db, **kw)


async def _state(nid):
    return (await _sql("SELECT estado, payload, programada_para FROM notifications WHERE id = :i", i=nid))[0]


async def _appt_for(p, phone="3001234567", email=None, when=None, svc=None):
    sv = svc or await _svc(p, f"Corte {uuid4().hex[:4]}", 30000)
    async with scoped_session(tenant_id=p.tenant_id) as db:
        from app.modules.customers import service as cs
        from app.modules.customers.schemas import CustomerIn
        c = await cs.create_customer(db, p, CustomerIn(nombre="Carlos Pérez", telefono=phone, email=email, consentimiento_datos=True))
        return await apps.create_appointment(db, p, AppointmentIn(customer_id=c.id, inicio=when or datetime.now(UTC) + timedelta(days=2), service_ids=[sv]))


async def test_confirmation_inside_24h_window_sends_text_with_a_valid_manage_link():
    await _clear_queue()
    p, pid, _, slug = await _wa()
    await _in(p, pid)                                                              # el cliente escribió hace segundos: ventana abierta
    ap = await _appt_for(p)
    nid = await _notify(p, "whatsapp", "573001234567", "reserva_confirmada", {"appointment_id": str(ap.id)})
    meta = FakeMeta()
    assert (await _drain(meta)) == {"enviada": 1}
    to, body = meta.sends[0]
    assert to == "573001234567" and body.startswith("Hola Carlos,")
    link = body.split("aquí: ")[1].strip()
    token = link.rsplit("/", 1)[1]
    assert f"/{slug}/reserva/" in link and brules.read_token(token).appointment_id == ap.id
    assert (await _state(nid)).estado == "enviada"


async def test_outside_window_needs_an_approved_template_or_fails_with_a_clear_reason():
    await _clear_queue()
    p, pid, _, _ = await _wa(plantillas={"recordatorio_cita": "recordatorio_cita_v2"})
    ap = await _appt_for(p)                                                        # nadie ha escrito: sin ventana
    n1 = await _notify(p, "whatsapp", "573001234567", "recordatorio_cita", {"appointment_id": str(ap.id)})
    n2 = await _notify(p, "whatsapp", "573001234567", "reserva_confirmada", {"appointment_id": str(ap.id)})
    meta = FakeMeta()
    assert (await _drain(meta)) == {"enviada": 1, "fallida": 1}
    assert meta.sends == [] and meta.templates[0][1] == "recordatorio_cita_v2" and meta.templates[0][2][0] == "Carlos" and len(meta.templates[0][2]) == 4
    f = await _state(n2)
    assert f.estado == "fallida" and "plantilla aprobada" in f.payload["error"] and (await _state(n1)).estado == "enviada"


async def test_transient_failures_retry_with_backoff_then_give_up():
    await _clear_queue()
    p, pid, _, _ = await _wa()
    await _in(p, pid)
    ap = await _appt_for(p)
    nid = await _notify(p, "whatsapp", "573001234567", "reserva_confirmada", {"appointment_id": str(ap.id)})
    flaky = FakeMeta(fail=MetaError(503, "Service Unavailable"))
    for intento in (1, 2):
        assert (await _drain(flaky)) == {"reintento": 1}
        s = await _state(nid)
        assert s.estado == "pendiente" and s.payload["intentos"] == intento and s.programada_para > datetime.now(UTC)
        async with scoped_session(platform=True) as db:                            # adelantar el reloj para el siguiente intento
            await db.execute(text("UPDATE notifications SET programada_para = now() WHERE id = :i"), {"i": nid})
    assert (await _drain(flaky)) == {"fallida": 1} and (await _state(nid)).estado == "fallida"
    perm = await _notify(p, "whatsapp", "573001234567", "reserva_confirmada", {"appointment_id": str(ap.id)})
    assert (await _drain(FakeMeta(fail=MetaError(400, "Invalid parameter")))) == {"fallida": 1}                    # error permanente: sin reintentos
    assert (await _state(perm)).payload["intentos"] == 1


async def test_cancelled_or_missing_targets_are_discarded_and_email_requires_smtp():
    await _clear_queue()
    p, pid, _, _ = await _wa()
    ap = await _appt_for(p, email="carlos@correo.co")
    async with scoped_session(platform=True) as db:
        await db.execute(text("UPDATE appointments SET estado = 'cancelada' WHERE id = :i"), {"i": ap.id})
    n1 = await _notify(p, "whatsapp", "573001234567", "recordatorio_cita", {"appointment_id": str(ap.id)})
    n2 = await _notify(p, "whatsapp", "573001234567", "comprobante", {"sale_id": str(uuid4())})
    assert (await _drain(FakeMeta())) == {"descartada": 2} and (await _state(n1)).estado == "cancelada" and (await _state(n2)).estado == "cancelada"
    ap2 = await _appt_for(p, email="carlos@correo.co")
    n3 = await _notify(p, "email", "carlos@correo.co", "reserva_confirmada", {"appointment_id": str(ap2.id)})
    assert (await _drain()) == {"fallida": 1} and "SMTP" in (await _state(n3)).payload["error"]                   # SMTP sin configurar: falla con motivo
    sent = []

    async def mailer(to, subject, body):
        sent.append((to, subject, body))
    ap3 = await _appt_for(p, email="ana@correo.co")
    await _notify(p, "email", "ana@correo.co", "reserva_confirmada", {"appointment_id": str(ap3.id)})
    assert (await _drain(mailer=mailer)) == {"enviada": 1} and sent[0][0] == "ana@correo.co" and sent[0][1].startswith("Tu cita en")


async def test_two_workers_never_send_the_same_notification_twice():
    await _clear_queue()
    p, pid, _, _ = await _wa()
    await _in(p, pid)
    ap = await _appt_for(p)
    for _ in range(6):
        await _notify(p, "whatsapp", "573001234567", "reserva_confirmada", {"appointment_id": str(ap.id)})
    meta = FakeMeta()
    r = await asyncio.gather(_drain(meta), _drain(meta), _drain(meta))
    assert sum(x.get("enviada", 0) for x in r) == 6 and len(meta.sends) == 6                       # SKIP LOCKED: cada una, una sola vez


async def test_reminders_are_queued_once_for_the_right_appointments_and_channel():
    await _clear_queue()
    now = datetime.now(UTC)
    p, pid, _, _ = await _wa()
    async with scoped_session(platform=True) as db:
        await db.execute(text("UPDATE integrations SET estado = 'activa' WHERE tenant_id = :t AND tipo = 'meta_whatsapp'"), {"t": p.tenant_id})
    due = await _appt_for(p, when=now + timedelta(hours=24))
    await _appt_for(p, when=now + timedelta(hours=10))                                              # muy pronto: no entra a la ventana
    await _appt_for(p, when=now + timedelta(hours=40))                                              # muy lejos
    cancelled = await _appt_for(p, when=now + timedelta(hours=24, minutes=5))
    async with scoped_session(platform=True) as db:
        await db.execute(text("UPDATE appointments SET estado = 'cancelada' WHERE id = :i"), {"i": cancelled.id})
        assert await notif.queue_reminders(db) >= 1
    rows = await _sql("SELECT canal, destinatario, payload FROM notifications WHERE tenant_id = :t AND plantilla = 'recordatorio_cita'", t=p.tenant_id)
    assert len(rows) == 1 and rows[0].canal == "whatsapp" and rows[0].destinatario == "573001234567" and rows[0].payload["appointment_id"] == str(due.id)
    async with scoped_session(platform=True) as db:
        assert await notif.queue_reminders(db) == 0                                                 # idempotente

    p2, _, _, _ = await _wa()                                                                        # WhatsApp sin validar => correo
    await _appt_for(p2, email="ana@correo.co", when=now + timedelta(hours=24))
    async with scoped_session(platform=True) as db:
        await notif.queue_reminders(db)
    r2 = (await _sql("SELECT canal, destinatario FROM notifications WHERE tenant_id = :t AND plantilla = 'recordatorio_cita'", t=p2.tenant_id))[0]
    assert (r2.canal, r2.destinatario) == ("email", "ana@correo.co")


# ================================================================== tareas de Celery: un event loop nuevo por tarea
def test_each_background_task_gets_a_clean_engine_so_the_second_run_works():
    script = ("import asyncio\nfrom sqlalchemy import text\nfrom app.core.db import scoped_session\nfrom app.workers.tasks import run_async\n"
              "async def q():\n    async with scoped_session(platform=True) as db:\n        return (await db.execute(text('SELECT 1'))).scalar()\n"
              "print(run_async(q()), run_async(q()), run_async(q()))\n")
    r = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=120)
    if r.returncode != 0 and any(x in r.stderr.lower() for x in ("onnect", "conexi", "refused", "does not exist")):
        pytest.skip("Postgres no disponible")
    assert r.returncode == 0 and r.stdout.strip() == "1 1 1", r.stderr[-800:]
