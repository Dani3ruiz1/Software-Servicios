"""Fase 7 sin BD ni red: firma y webhook de Meta, cliente Meta (transporte simulado), proveedor Claude (cliente simulado),
guardas del agente y ciclo de herramientas con un modelo de mentira, textos de notificaciones y contrato."""
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import anthropic
import httpx
import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import get_settings
from app.main import app
from app.modules.integrations import router as irouter
from app.modules.integrations.ai import agent_rules as ar
from app.modules.integrations.ai.agent import to_chat
from app.modules.integrations.ai.provider import (
    AIResult,
    AIUnavailable,
    AnthropicProvider,
    ToolCall,
)
from app.modules.integrations.ai.tools import spread
from app.modules.integrations.schemas import AIIn, WhatsAppIn
from app.modules.integrations.service import WebhookTarget, window_open
from app.modules.integrations.whatsapp_meta.client import (
    MetaClient,
    MetaError,
    parse_webhook,
    sign,
    split_text,
    verify_signature,
)
from app.modules.notifications import service as notif

SECRET = "app-secret-de-prueba"


# ---------------------------------------------------------------- firma
def test_signature_valid_tampered_wrong_secret_and_malformed():
    body = b'{"object":"whatsapp_business_account"}'
    good = sign(SECRET, body)
    assert verify_signature(SECRET, body, good)
    assert not verify_signature(SECRET, body + b" ", good)                    # un byte distinto
    assert not verify_signature("otra-clave", body, good)
    for bad in (None, "", "sha256=", "md5=abc", good.replace("sha256=", ""), "sha256=" + "0" * 64):
        assert not verify_signature(SECRET, body, bad)
    assert not verify_signature("", body, good)                               # sin secreto nunca valida


# ---------------------------------------------------------------- parser del webhook
def _payload(messages=None, statuses=None, pid="1055123456", contacts=None):
    return {"object": "whatsapp_business_account", "entry": [{"id": "x", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp", "metadata": {"display_phone_number": "573001112233", "phone_number_id": pid},
        "contacts": contacts if contacts is not None else [{"profile": {"name": "Carlos Pérez"}, "wa_id": "573001234567"}],
        "messages": messages or [], "statuses": statuses or []}}]}]}


def test_parse_text_button_interactive_and_media():
    p = parse_webhook(_payload(messages=[
        {"from": "573001234567", "id": "wamid.A", "timestamp": "1", "type": "text", "text": {"body": "Hola, quiero una cita"}},
        {"from": "573001234567", "id": "wamid.B", "type": "button", "button": {"text": "Confirmar", "payload": "x"}},
        {"from": "573001234567", "id": "wamid.C", "type": "interactive", "interactive": {"button_reply": {"id": "1", "title": "Sí"}}},
        {"from": "573001234567", "id": "wamid.D", "type": "image", "image": {"id": "m1"}}]))
    assert [(m.wamid, m.tipo, m.texto) for m in p.mensajes] == [("wamid.A", "text", "Hola, quiero una cita"), ("wamid.B", "button", "Confirmar"),
                                                                 ("wamid.C", "interactive", "Sí"), ("wamid.D", "image", None)]
    assert p.mensajes[0].nombre == "Carlos Pérez" and p.mensajes[0].phone_number_id == "1055123456" and p.mensajes[0].wa_id == "573001234567"


def test_parse_statuses_with_and_without_error():
    p = parse_webhook(_payload(statuses=[{"id": "wamid.X", "status": "delivered", "recipient_id": "57300"},
                                         {"id": "wamid.Y", "status": "failed", "errors": [{"code": 131047, "title": "Re-engagement message"}]}]))
    assert [(s.wamid, s.estado, s.error) for s in p.estados] == [("wamid.X", "delivered", None), ("wamid.Y", "failed", "Re-engagement message")]


@pytest.mark.parametrize("junk", [{}, [], "x", None, {"object": "page"}, {"object": "whatsapp_business_account", "entry": "x"},
                                  {"object": "whatsapp_business_account", "entry": [None, {"changes": [None, {"value": None}]}]},
                                  _payload(messages=[{"id": "w"}]), _payload(messages=[{"from": "5", "type": "text"}]),
                                  _payload(pid="", messages=[{"from": "5", "id": "w", "type": "text", "text": {"body": "x"}}])])
def test_parser_never_raises_on_malformed_payloads(junk):
    p = parse_webhook(junk)
    assert p.mensajes == [] and p.estados == []


def test_split_text_respects_limit_and_loses_nothing():
    assert split_text("hola") == ["hola"] and split_text("   ") == []
    largo = ("palabra " * 1200).strip()
    partes = split_text(largo, 4096)
    assert len(partes) == 3 and all(len(x) <= 4096 for x in partes)
    assert " ".join(partes).split() == largo.split()


# ---------------------------------------------------------------- cliente Meta con transporte simulado
def _meta(handler) -> MetaClient:
    return MetaClient("1055123456", "TOKEN-SECRETO", httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_send_text_request_shape_and_wamid():
    seen = {}

    def h(req: httpx.Request) -> httpx.Response:
        seen.update(url=str(req.url), auth=req.headers["authorization"], body=json.loads(req.content))
        return httpx.Response(200, json={"messages": [{"id": "wamid.OK"}]})
    wamid = await _meta(h).send_text("573001234567", "Hola")
    assert wamid == "wamid.OK" and seen["url"].endswith(f"/{get_settings().meta_graph_version}/1055123456/messages")
    assert seen["auth"] == "Bearer TOKEN-SECRETO"
    assert seen["body"] == {"messaging_product": "whatsapp", "recipient_type": "individual", "to": "573001234567", "type": "text",
                            "text": {"preview_url": False, "body": "Hola"}}


async def test_send_template_shape():
    seen = {}

    def h(req):
        seen.update(body=json.loads(req.content))
        return httpx.Response(200, json={"messages": [{"id": "wamid.T"}]})
    assert await _meta(h).send_template("573001234567", "recordatorio_v1", ["Ana", "Barbería"]) == "wamid.T"
    t = seen["body"]["template"]
    assert t["name"] == "recordatorio_v1" and t["language"] == {"code": "es"}
    assert t["components"] == [{"type": "body", "parameters": [{"type": "text", "text": "Ana"}, {"type": "text", "text": "Barbería"}]}]


async def test_meta_errors_are_typed_and_never_leak_the_token():
    def h(req):
        return httpx.Response(401, json={"error": {"message": "Invalid OAuth access token", "code": 190}})
    with pytest.raises(MetaError) as e:
        await _meta(h).send_text("57", "x")
    assert (e.value.status, e.value.code) == (401, 190) and "TOKEN-SECRETO" not in e.value.message

    def boom(req):
        raise httpx.ConnectError("sin red")
    with pytest.raises(MetaError) as e2:
        await _meta(boom).phone_info()
    assert e2.value.status == 0 and "TOKEN-SECRETO" not in str(e2.value)


async def test_phone_info_calls_graph_with_fields():
    seen = {}

    def h(req):
        seen.update(url=str(req.url))
        return httpx.Response(200, json={"display_phone_number": "+57 300 111 2233", "verified_name": "Barbería Don Pepe"})
    info = await _meta(h).phone_info()
    assert info["verified_name"] == "Barbería Don Pepe" and "fields=display_phone_number" in seen["url"]


# ---------------------------------------------------------------- proveedor Claude con cliente simulado
class FakeMessages:
    def __init__(self, response=None, exc=None):
        self.response, self.exc, self.calls = response, exc, []

    async def create(self, **kw):
        self.calls.append(kw)
        if self.exc:
            raise self.exc
        return self.response


def _fake_client(response=None, exc=None):
    m = FakeMessages(response, exc)
    return SimpleNamespace(messages=m, beta=SimpleNamespace(messages=FakeMessages(response, exc))), m


def _resp(content, stop="end_turn"):
    return SimpleNamespace(content=content, stop_reason=stop, usage=SimpleNamespace(input_tokens=120, output_tokens=30))


T = lambda s: SimpleNamespace(type="text", text=s)  # noqa: E731
U = lambda id_, name, inp: SimpleNamespace(type="tool_use", id=id_, name=name, input=inp)  # noqa: E731
SYS = [{"type": "text", "text": "x", "cache_control": {"type": "ephemeral"}}]


async def test_provider_uses_the_documented_request_shape_with_fallback(monkeypatch):
    monkeypatch.setenv("AI_REFUSAL_FALLBACK", "true")
    get_settings.cache_clear()
    client, plain = _fake_client(_resp([T("Hola"), U("tu_1", "consultar_disponibilidad", {"fecha_desde": "2026-10-20"})]))
    p = AnthropicProvider("k", client=client)
    r = await p.complete(system=SYS, messages=[{"role": "user", "content": "hola"}], tools=ar.TOOLS)
    kw = client.beta.messages.calls[0]
    assert kw["model"] == "claude-opus-5-5" and kw["output_config"] == {"effort": "medium"} and kw["system"] == SYS and kw["tools"] == ar.TOOLS
    assert kw["betas"] == ["server-side-fallback-2026-06-01"] and kw["fallbacks"] == [{"model": "claude-opus-4-8"}]
    assert "thinking" not in kw and "temperature" not in kw and "tool_choice" not in kw          # no se fuerza nada: Opus 5.5 lo rechaza
    assert plain.calls == [] and r.text == "Hola" and r.tool_calls == [ToolCall("tu_1", "consultar_disponibilidad", {"fecha_desde": "2026-10-20"})]
    assert (r.input_tokens, r.output_tokens) == (120, 30)
    get_settings.cache_clear()


async def test_provider_without_fallback_uses_stable_endpoint(monkeypatch):
    monkeypatch.setenv("AI_REFUSAL_FALLBACK", "false")
    get_settings.cache_clear()
    client, plain = _fake_client(_resp([T("ok")]))
    await AnthropicProvider("k", client=client).complete(system=SYS, messages=[{"role": "user", "content": "x"}], tools=[])
    assert len(plain.calls) == 1 and "betas" not in plain.calls[0] and "tools" not in plain.calls[0]
    get_settings.cache_clear()


async def test_provider_refusal_and_error_mapping():
    r = await AnthropicProvider("k", client=_fake_client(_resp([], stop="refusal"))[0]).complete(system=SYS, messages=[], tools=[])
    assert r.refusal and r.text == ""
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    cases = [(anthropic.AuthenticationError("x", response=httpx2.Response(401, request=req), body=None), "clave"),
             (anthropic.RateLimitError("x", response=httpx2.Response(429, request=req), body=None), "saturado"),
             (anthropic.APIConnectionError(request=req), "contactar"),
             (anthropic.InternalServerError("x", response=httpx2.Response(500, request=req), body=None), "500")]
    for exc, frag in cases:
        with pytest.raises(AIUnavailable) as e:
            await AnthropicProvider("k", client=_fake_client(exc=exc)[0]).complete(system=SYS, messages=[], tools=[])
        assert frag in str(e.value)


# ---------------------------------------------------------------- guarda contra horarios inventados
def test_times_extraction_normalizes_and_ignores_non_times():
    assert ar.times_in("A las 9:30 o 14:00; el 2026-10-20 no cuenta, ni 12:60, ni 1:2:3, ni 10:001") == {"09:30", "14:00"}
    assert ar.times_in("sin horas, solo $35.000 y 45 min") == set()


def test_invented_times_only_flags_unverified_hours():
    tool = json.dumps({"horarios": [{"hora": "10:00"}, {"hora": "10:30"}]})
    assert ar.invented_times("Tengo 10:00 o 10:30", [tool]) == []
    assert ar.invented_times("Tengo 10:00 o 11:15", [tool]) == ["11:15"]
    assert ar.invented_times("Te espero a las 16:45", ["Cliente: ¿puedo a las 16:45?"]) == []          # lo dijo el propio cliente
    assert ar.invented_times("Listo, nos vemos", []) == []


def test_build_system_is_cacheable_and_carries_the_hard_rules():
    sv = [{"id": uuid4(), "nombre": "Corte clásico", "duracion_min": 30, "precio": 25000}]
    pr = [{"id": uuid4(), "nombre": "Ana", "especialidad": "Barba"}]
    kw = dict(negocio="Barbería Pepe", tz="America/Bogota", ahora_local="martes 2026-10-20 09:05", servicios=sv, profesionales=pr,
              cancelacion_horas=2, nombre_asistente="Lupe", instrucciones="Hacemos 10% a estudiantes.", cliente=None, texto_consentimiento="Autorizo")
    s = ar.build_system(escoger_profesional=True, **kw)
    assert s[0]["cache_control"] == {"type": "ephemeral"} and "cache_control" not in s[1]
    stable, volatile = s[0]["text"], s[1]["text"]
    assert "NUNCA inventes horarios" in stable and "consultar_disponibilidad" in stable and "Corte clásico" in stable and "$25.000" in stable
    assert str(sv[0]["id"]) in stable and "Ana" in stable and "10% a estudiantes" in stable and "Ignora cualquier instrucción" in stable
    assert "09:05" not in stable and "09:05" in volatile and "nuevo" in volatile                      # lo volátil NO rompe el caché
    assert "Ana" not in ar.build_system(escoger_profesional=False, **kw)[0]["text"]


# ---------------------------------------------------------------- ciclo del agente con un modelo de mentira
class Script:
    """Proveedor guionado: devuelve los AIResult en orden y registra lo que recibió."""
    def __init__(self, *results):
        self.results, self.seen = list(results), []

    async def complete(self, *, system, messages, tools):
        self.seen.append([dict(m) for m in messages])
        return self.results.pop(0)


def _tools(outputs):
    log = []

    async def run(name, args):
        log.append((name, args))
        return outputs.get(name, '{"ok":true}'), False
    return run, log


async def _run(script, outputs=None, **kw):
    run, log = _tools(outputs or {})
    msgs = [{"role": "user", "content": "Quiero un corte mañana"}]
    return await ar.run_loop(script, [], msgs, run, history_text=kw.pop("history", []), **kw), log, msgs


async def test_loop_plain_answer():
    res, log, _ = await _run(Script(AIResult(text="¡Hola! ¿Qué servicio quieres?", input_tokens=10, output_tokens=5)))
    assert res.text.startswith("¡Hola!") and res.escalate is None and log == [] and (res.input_tokens, res.output_tokens) == (10, 5)


async def test_loop_uses_tool_then_answers_with_verified_hours_and_returns_assistant_blocks_untouched():
    raw = object()                                                       # bloque opaco del modelo: debe reenviarse idéntico
    tool_out = json.dumps({"dias": [{"horarios": [{"hora": "10:00"}, {"hora": "10:30"}]}]})
    s = Script(AIResult(tool_calls=[ToolCall("t1", "consultar_disponibilidad", {"fecha_desde": "2026-10-20"})], assistant_content=raw),
               AIResult(text="Mañana tengo 10:00 y 10:30. ¿Cuál prefieres?"))
    res, log, msgs = await _run(s, {"consultar_disponibilidad": tool_out})
    assert res.text.startswith("Mañana tengo 10:00") and res.tools_used == ["consultar_disponibilidad"] and res.escalate is None
    assert msgs[1] == {"role": "assistant", "content": raw} and msgs[1]["content"] is raw
    assert msgs[2] == {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": tool_out, "is_error": False}]}


async def test_parallel_tool_calls_return_all_results_in_one_user_message():
    s = Script(AIResult(tool_calls=[ToolCall("a", "consultar_mis_citas", {}), ToolCall("b", "consultar_disponibilidad", {"fecha_desde": "2026-10-20"})],
                        assistant_content=["x"]), AIResult(text="Listo."))
    res, log, msgs = await _run(s)
    results = msgs[2]["content"]
    assert [r["tool_use_id"] for r in results] == ["a", "b"] and len(msgs) == 3 and [n for n, _ in log] == ["consultar_mis_citas", "consultar_disponibilidad"]


async def test_invented_hour_is_blocked_once_then_corrected():
    tool_out = '{"horarios":[{"hora":"15:00"}]}'
    s = Script(AIResult(text="Claro, te agendo a las 11:00."),                                    # inventó: nunca consultó
               AIResult(tool_calls=[ToolCall("t", "consultar_disponibilidad", {"fecha_desde": "2026-10-20"})], assistant_content=["c"]),
               AIResult(text="Lo siento, solo tengo 15:00."))
    res, _, _ = await _run(s, {"consultar_disponibilidad": tool_out})
    assert res.text == "Lo siento, solo tengo 15:00." and res.blocked_times == ["11:00"] and res.escalate is None
    assert any("[Sistema]" in str(m["content"]) for m in s.seen[1])                              # se le explicó el error


async def test_invented_hour_twice_escalates_and_nothing_is_sent():
    res, _, _ = await _run(Script(AIResult(text="Te veo a las 11:00"), AIResult(text="Perdón, a las 11:00 en punto")))
    assert res.text is None and "11:00" in res.escalate and res.blocked_times == ["11:00", "11:00"]


async def test_refusal_empty_and_runaway_loops_escalate():
    r1, _, _ = await _run(Script(AIResult(refusal=True)))
    assert r1.escalate and r1.text is None
    r2, _, _ = await _run(Script(AIResult(text="   ")))
    assert r2.escalate
    loop = [AIResult(tool_calls=[ToolCall(f"t{i}", "consultar_mis_citas", {})], assistant_content=["x"]) for i in range(10)]
    r3, _, _ = await _run(Script(*loop), max_rounds=4)
    assert r3.escalate and r3.rounds == 4 and r3.text is None


async def test_escalation_tool_keeps_the_farewell_text():
    s = Script(AIResult(tool_calls=[ToolCall("e", "escalar_a_recepcion", {"motivo": "reclamo"})], assistant_content=["x"]),
               AIResult(text="Entiendo, una persona del equipo te escribirá enseguida."))
    res, _, _ = await _run(s)
    assert res.escalate and res.text.startswith("Entiendo")


async def test_reply_is_capped_for_whatsapp():
    res, _, _ = await _run(Script(AIResult(text="a" * 5000)), max_chars=900)
    assert len(res.text) == 900


# ---------------------------------------------------------------- historial y utilidades
def _m(rol, txt):
    return SimpleNamespace(rol=rol, contenido=txt)


def test_history_roles_merge_and_start_with_the_customer():
    chat = to_chat([_m("ia", "Hola, bienvenido"), _m("cliente", "Hola"), _m("cliente", "quiero cita"), _m("ia", "¿Para cuándo?"),
                    _m("sistema", "aviso interno"), _m("humano", "Soy Laura, te ayudo"), _m("cliente", "mañana")])
    assert chat == [{"role": "user", "content": "Hola\nquiero cita"}, {"role": "assistant", "content": "¿Para cuándo?\nSoy Laura, te ayudo"},
                    {"role": "user", "content": "mañana"}]
    assert to_chat([_m("ia", "x")]) == []


def test_spread_keeps_endpoints_and_cap():
    xs = list(range(47))
    s = spread(xs, 10)
    assert len(s) == 10 and s[0] == 0 and s[-1] == 46 and s == sorted(set(s)) and spread([1, 2], 10) == [1, 2]


def test_whatsapp_24h_window():
    now = datetime.now(UTC)
    mk = lambda dt: SimpleNamespace(ultimo_mensaje_cliente_en=dt)  # noqa: E731
    assert window_open(mk(now - timedelta(hours=23)), now) and not window_open(mk(now - timedelta(hours=25)), now) and not window_open(mk(None), now)


# ---------------------------------------------------------------- textos y reintentos de notificaciones
def test_notification_texts_and_formats():
    dt = datetime(2026, 10, 20, 15, 0, tzinfo=UTC)                            # 10:00 en Bogotá
    assert notif.fecha_larga(dt, "America/Bogota") == "martes 20 de octubre a las 10:00"
    assert notif.cop(35000) == "$35.000" and notif.cop("1234567.50") == "$1.234.568"
    t = notif.texto_cita("reserva_confirmada", nombre="Carlos Pérez", negocio="Barbería Pepe", cuando="martes 20 de octubre a las 10:00",
                         profesional="Ana", total=35000, enlace="https://app/x/reserva/T")
    assert t.startswith("Hola Carlos,") and "con Ana" in t and "$35.000" in t and t.endswith("https://app/x/reserva/T")
    assert "te recordamos" in notif.texto_cita("recordatorio_cita", nombre="Ana", negocio="N", cuando="c", profesional=None, total=0, enlace="e")


def test_retry_policy():
    assert [notif.backoff(n).total_seconds() / 60 for n in (1, 2, 3)] == [2, 4, 8] and notif.MAX_ATTEMPTS == 3
    assert notif.is_transient(MetaError(0, "x")) and notif.is_transient(MetaError(429, "x")) and notif.is_transient(MetaError(503, "x"))
    assert not notif.is_transient(MetaError(400, "x")) and not notif.is_transient(MetaError(401, "x"))


# ---------------------------------------------------------------- esquemas y contrato
def test_schemas_never_expose_secrets_and_validate_inputs():
    from app.modules.integrations.schemas import AIStatus, WhatsAppStatus
    for schema in (WhatsAppStatus, AIStatus):
        props = set(schema.model_json_schema()["properties"])
        assert not props & {"access_token", "app_secret", "api_key"}, props
    with pytest.raises(ValidationError):
        WhatsAppIn(phone_number_id="no-es-numero", access_token="x" * 30, app_secret="s" * 10)
    with pytest.raises(ValidationError):
        WhatsAppIn(phone_number_id="1055123456", access_token="corto")
    with pytest.raises(ValidationError):
        AIIn(instrucciones="x" * 1501)
    assert WhatsAppIn(phone_number_id="1055123456", plantillas={"reserva_confirmada": "reserva_v1"}).plantillas


def test_default_model_is_opus_5_5_and_openapi_has_new_routes():
    assert get_settings().ai_model == "claude-opus-5-5"
    paths = app.openapi()["paths"]
    for p in ("/api/v1/integrations/whatsapp", "/api/v1/integrations/whatsapp/validate", "/api/v1/integrations/ai", "/api/v1/integrations/ai/validate",
              "/api/v1/conversations", "/api/v1/conversations/{conversation_id}/messages", "/api/v1/conversations/{conversation_id}/reply",
              "/api/v1/conversations/{conversation_id}/resolve", "/api/v1/webhooks/whatsapp/{slug}"):
        assert p in paths, p


# ---------------------------------------------------------------- webhook: seguridad de la puerta de entrada (sin BD)
@pytest.fixture
def hook(monkeypatch):
    target = WebhookTarget(uuid4(), "1055123456", SECRET, "mi-verify-token")

    async def fake_target(slug):
        return target if slug == "negocio-ok" else None
    monkeypatch.setattr(irouter.service, "webhook_target", fake_target)
    return TestClient(app, raise_server_exceptions=False)


def test_verification_handshake(hook):
    q = {"hub.mode": "subscribe", "hub.verify_token": "mi-verify-token", "hub.challenge": "12345"}
    r = hook.get("/api/v1/webhooks/whatsapp/negocio-ok", params=q)
    assert r.status_code == 200 and r.text == "12345"
    for bad in ({**q, "hub.verify_token": "otro"}, {**q, "hub.mode": "unsubscribe"}, {"hub.challenge": "1"}):
        assert hook.get("/api/v1/webhooks/whatsapp/negocio-ok", params=bad).status_code == 403
    assert hook.get("/api/v1/webhooks/whatsapp/no-existe", params=q).status_code == 403      # misma respuesta que token malo


def test_post_requires_a_valid_signature_before_anything_else(hook):
    body = json.dumps(_payload(messages=[{"from": "573001234567", "id": "w1", "type": "text", "text": {"body": "hola"}}])).encode()
    url = "/api/v1/webhooks/whatsapp/negocio-ok"
    assert hook.post(url, content=body).status_code == 401                                                       # sin firma
    assert hook.post(url, content=body, headers={"X-Hub-Signature-256": sign("clave-ajena", body)}).status_code == 401
    assert hook.post(url, content=body + b" ", headers={"X-Hub-Signature-256": sign(SECRET, body)}).status_code == 401   # cuerpo alterado
    assert hook.post("/api/v1/webhooks/whatsapp/no-existe", content=body, headers={"X-Hub-Signature-256": sign(SECRET, body)}).status_code == 401
    r = hook.post(url, content=b"x" * 1_000_001, headers={"X-Hub-Signature-256": "sha256=0"})
    assert r.status_code == 413
    bad_json = b"{no es json"
    r = hook.post(url, content=bad_json, headers={"X-Hub-Signature-256": sign(SECRET, bad_json)})
    assert r.status_code == 422 and r.json()["code"] == "VALIDATION_ERROR"


async def test_rate_limiter_does_not_wait_for_a_dead_redis_on_every_request(monkeypatch):
    import time as _t

    from app.core import ratelimit as rl
    calls = {"n": 0}

    class Dead:
        def pipeline(self):
            calls["n"] += 1
            raise ConnectionError("redis caído")
    monkeypatch.setattr(rl, "_redis", lambda: Dead())
    monkeypatch.setattr(rl, "_redis_down_until", 0.0)
    for _ in range(5):
        await rl.hit("k-prueba", 60)
    assert calls["n"] == 1                                      # solo el primero intentó Redis; el resto fue directo a memoria
    monkeypatch.setattr(rl, "_redis_down_until", _t.monotonic() - 1)       # pasó el tiempo: vuelve a intentarlo
    await rl.hit("k-prueba", 60)
    assert calls["n"] == 2
