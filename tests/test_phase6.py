"""Fase 6 sin BD: token firmado, teléfono, formulario personalizado, limitador, esquemas y contrato público."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest
from pydantic import ValidationError

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.ratelimit import MemoryCounter
from app.core.security import create_access_token, decode_access_token
from app.main import app
from app.modules.booking import rules
from app.modules.booking.schemas import BookingIn, FieldIn, RuleIn, SettingsIn

FIELD_TEXT, FIELD_SEL, FIELD_BOX, FIELD_MAIL, FIELD_NUM, FIELD_DATE = (uuid4() for _ in range(6))
DEFS = [
    rules.FieldDef(FIELD_TEXT, "¿Alergias?", "texto", True, None),
    rules.FieldDef(FIELD_SEL, "Cómo nos conoció", "seleccion", False, ["Instagram", "Amigo"]),
    rules.FieldDef(FIELD_BOX, "Acepto políticas", "casilla", True, None),
    rules.FieldDef(FIELD_MAIL, "Correo factura", "email", False, None),
    rules.FieldDef(FIELD_NUM, "Edad", "numero", False, None),
    rules.FieldDef(FIELD_DATE, "Cumpleaños", "fecha", False, None),
]
OK = {FIELD_TEXT: "Ninguna", FIELD_BOX: True}


# ---------------------------------------------------------------- token firmado
def test_token_roundtrip_and_binding_to_start_time():
    a, t = uuid4(), uuid4()
    ini = datetime.now(UTC) + timedelta(days=3)
    c = rules.read_token(rules.make_token(a, t, ini))
    assert (c.appointment_id, c.tenant_id, c.inicio_ts) == (a, t, int(ini.timestamp()))
    assert rules.read_token(rules.make_token(a, t, ini + timedelta(hours=1))).inicio_ts != c.inicio_ts   # otro horario => otro token


def test_token_tampering_expiry_and_wrong_type_are_rejected():
    a, t = uuid4(), uuid4()
    tok = rules.make_token(a, t, datetime.now(UTC) + timedelta(days=1))
    with pytest.raises(AppError) as e:
        rules.read_token(tok[:-4] + "abcd")
    assert e.value.code == "UNAUTHORIZED"
    with pytest.raises(AppError) as e:
        rules.read_token(rules.make_token(a, t, datetime.now(UTC) - timedelta(days=3)))     # cita de hace 3 días: venció (gracia 1 día)
    assert e.value.code == "TOKEN_EXPIRED"
    forged = jwt.encode({"typ": "booking", "sub": str(a), "tid": str(t), "ini": 1, "exp": datetime.now(UTC) + timedelta(days=1)},
                        "otra-clave-otra-clave-otra-clave-xx", algorithm="HS256")
    with pytest.raises(AppError):
        rules.read_token(forged)
    with pytest.raises(AppError):
        rules.read_token("no.es.un.token")


def test_booking_token_is_not_a_session_and_session_is_not_a_booking_token():
    a, t = uuid4(), uuid4()
    tok = rules.make_token(a, t, datetime.now(UTC) + timedelta(days=1))
    with pytest.raises(AppError):
        decode_access_token(tok)                                       # un token de reserva no abre sesión
    access, _ = create_access_token(sub=a, kind="user", tenant_id=t, rol="admin")
    with pytest.raises(AppError):
        rules.read_token(access)                                       # ni una sesión sirve para gestionar reservas
    assert get_settings().jwt_secret                                   # misma clave, tipos distintos (claim `typ`)


# ---------------------------------------------------------------- teléfono
@pytest.mark.parametrize("raw", ["3001234567", "300 123 4567", "+57 300-123-4567", "(57) 3001234567", "573001234567"])
def test_phone_normalization_makes_same_customer(raw):
    assert rules.normalize_phone(raw) == "3001234567"


@pytest.mark.parametrize("raw", ["123", "abc", "", "+57 12", "1" * 16])
def test_phone_rejects_garbage(raw):
    with pytest.raises(AppError):
        rules.normalize_phone(raw)


# ---------------------------------------------------------------- formulario
def test_form_answers_valid_and_formatted():
    out = rules.validate_answers(DEFS, {**OK, FIELD_SEL: "Instagram", FIELD_NUM: "31", FIELD_DATE: "1990-05-17", FIELD_MAIL: "a@b.co"})
    assert ("¿Alergias?", "Ninguna") in out and ("Acepto políticas", "Sí") in out and ("Cómo nos conoció", "Instagram") in out
    assert rules.notes_from_answers(out).startswith("¿Alergias?: Ninguna")


@pytest.mark.parametrize("extra,msg", [
    ({FIELD_TEXT: "  "}, "obligatorio"), ({FIELD_BOX: False}, "marcarse"), ({FIELD_SEL: "Radio"}, "opción"),
    ({FIELD_MAIL: "no-es-correo"}, "correo"), ({FIELD_NUM: "muchos"}, "número"), ({FIELD_DATE: "17/05/1990"}, "fecha"),
])
def test_form_answers_rejected(extra, msg):
    with pytest.raises(AppError) as e:
        rules.validate_answers(DEFS, {**OK, **extra})
    assert e.value.code == "VALIDATION_ERROR" and msg in e.value.message


def test_form_rejects_unknown_fields_and_strips_control_chars_and_caps_length():
    with pytest.raises(AppError):
        rules.validate_answers(DEFS, {**OK, uuid4(): "x"})
    out = dict(rules.validate_answers(DEFS, {**OK, FIELD_TEXT: "hola\x00\x07 mundo" + "x" * 500}))
    assert "\x00" not in out["¿Alergias?"] and len(out["¿Alergias?"]) == 200
    assert len(rules.notes_from_answers([("k", "v" * 600)] * 5)) == 1000     # cabe en el límite de notas


# ---------------------------------------------------------------- limitador
def test_memory_rate_limiter_window_and_reset():
    now = [1000.0]
    m = MemoryCounter(clock=lambda: now[0])
    assert [m.hit("ip", 60)[0] for _ in range(4)] == [1, 2, 3, 4]
    assert m.hit("otra-ip", 60)[0] == 1                                       # cada clave cuenta aparte
    now[0] += 59
    count, retry = m.hit("ip", 60)
    assert count == 5 and retry == 1
    now[0] += 2                                                                # ventana vencida: se reinicia
    assert m.hit("ip", 60)[0] == 1


# ---------------------------------------------------------------- esquemas
def _booking(**kw):
    base = dict(inicio="2026-10-20T10:00:00-05:00", cliente={"nombre": "Ana Ruiz", "telefono": "3001234567"}, consentimiento_datos=True)
    return BookingIn(**{**base, **kw})


def test_booking_input_validation():
    assert _booking().website == ""
    with pytest.raises(ValidationError):
        _booking(inicio="2026-10-20T10:00:00")                                 # sin zona horaria
    with pytest.raises(ValidationError):
        _booking(cliente={"nombre": "A", "telefono": "3001234567"})
    with pytest.raises(ValidationError):
        _booking(cliente={"nombre": "Ana", "telefono": "12"})
    with pytest.raises(ValidationError):
        _booking(service_ids=[str(uuid4()) for _ in range(11)])
    with pytest.raises(ValidationError):
        _booking(consentimiento_datos="quizás")


def test_settings_validation():
    assert SettingsIn().modo_asignacion == "cliente_elige" and SettingsIn().cancelacion_min_horas == 2
    for bad in ({"slug": "api"}, {"slug": "A B"}, {"color_primario": "rojo"}, {"logo_url": "http://x.co/l.png"},
                {"textos": {"hack": "x"}}, {"anticipacion_max_dias": 0}, {"modo_asignacion": "azar"}):
        with pytest.raises(ValidationError):
            SettingsIn(**bad)
    assert SettingsIn(slug="barberia-don-pepe", logo_url="https://cdn.co/l.png", textos={"titulo": "Hola"}).slug == "barberia-don-pepe"


def test_field_and_rule_validation():
    with pytest.raises(ValidationError):
        FieldIn(etiqueta="Pelo", tipo="seleccion")                              # selección sin opciones
    with pytest.raises(ValidationError):
        FieldIn(etiqueta="Pelo", tipo="texto", opciones=["a"])
    assert FieldIn(etiqueta="Pelo", tipo="seleccion", opciones=["Liso", "Rizado"]).obligatorio is False
    with pytest.raises(ValidationError):
        RuleIn(staff_id=uuid4())                                                # reglas por profesional: aún no
    with pytest.raises(ValidationError):
        RuleIn(cupos_simultaneos=0)
    assert RuleIn(cupos_simultaneos=2, duracion_turno_min=15).staff_id is None


# ---------------------------------------------------------------- contrato
def test_public_routes_are_exactly_the_documented_ones_and_need_no_auth_header():
    spec = app.openapi()
    pub = {(m.upper(), p) for p, ops in spec["paths"].items() if p.startswith("/api/v1/public/") for m in ops}
    assert pub == {("GET", "/api/v1/public/{slug}/config"), ("GET", "/api/v1/public/{slug}/availability"),
                   ("POST", "/api/v1/public/{slug}/bookings"), ("GET", "/api/v1/public/{slug}/bookings/{token}"),
                   ("POST", "/api/v1/public/{slug}/bookings/{token}/cancel"), ("POST", "/api/v1/public/{slug}/bookings/{token}/reschedule")}
    for p, ops in spec["paths"].items():
        if p.startswith("/api/v1/public/"):
            assert all("security" not in op for op in ops.values())


def test_public_responses_never_expose_customer_contact_data():
    schemas = app.openapi()["components"]["schemas"]
    for name in ("BookingOut", "BookingView", "PublicConfig", "PublicAvailability"):
        props = set(schemas[name]["properties"])
        assert not props & {"telefono", "email", "documento", "customer_id", "cliente_id", "notas"}, (name, props)
    assert "token" in schemas["BookingOut"]["properties"]


def test_public_endpoint_with_bad_slug_shape_is_rejected_before_touching_the_db():
    from fastapi.testclient import TestClient
    r = TestClient(app, raise_server_exceptions=False).get("/api/v1/public/a/config")      # slug de 1 letra
    assert r.status_code == 422 and r.json()["code"] == "VALIDATION_ERROR"
