"""Pruebas sin base de datos: errores, seguridad, RBAC, tenancy desde JWT."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pyotp
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.errors import ERROR_CATALOG, AppError
from app.core.rbac import PERMISSIONS, permissions_for
from app.core.security import (
    create_access_token,
    decode_access_token,
    decrypt_secret,
    encrypt_secret,
    hash_password,
    hash_token,
    verify_password,
    verify_totp,
)
from app.main import app

client = TestClient(app, raise_server_exceptions=False)


def test_password_hash_roundtrip():
    h = hash_password("Clave.Segura.1")
    assert h.startswith("$argon2id$") and verify_password(h, "Clave.Segura.1")
    assert not verify_password(h, "otra") and not verify_password("basura", "x")


def test_access_token_carries_tenant_and_rejects_tampering():
    tid, uid = uuid4(), uuid4()
    tok, _ = create_access_token(sub=uid, kind="user", tenant_id=tid, rol="admin")
    c = decode_access_token(tok)
    assert c["tid"] == str(tid) and c["rol"] == "admin"
    forged = jwt.encode({**c, "tid": str(uuid4())}, "otra-clave-otra-clave-otra-clave-xx", algorithm="HS256")
    with pytest.raises(AppError) as e:
        decode_access_token(forged)
    assert e.value.code == "UNAUTHORIZED"


def test_expired_and_alg_none_tokens_rejected():
    s = get_settings()
    old = jwt.encode({"sub": str(uuid4()), "kind": "user", "typ": "access",
                      "exp": datetime.now(UTC) - timedelta(seconds=5)}, s.jwt_secret, algorithm="HS256")
    with pytest.raises(AppError) as e:
        decode_access_token(old)
    assert e.value.code == "TOKEN_EXPIRED"
    none_tok = jwt.encode({"sub": "x", "kind": "user", "exp": 9999999999}, None, algorithm="none")
    with pytest.raises(AppError):
        decode_access_token(none_tok)


def test_fernet_and_totp():
    assert decrypt_secret(encrypt_secret("hola")) == "hola"
    secret = pyotp.random_base32()
    assert verify_totp(encrypt_secret(secret), pyotp.TOTP(secret).now())
    assert not verify_totp(encrypt_secret(secret), "abcdef")


def test_refresh_hash_is_stable_and_not_plain():
    assert hash_token("abc") == hash_token("abc") != "abc"


def test_rbac_matrix():
    assert "reports:read" in permissions_for("admin")
    assert "reports:read" not in permissions_for("recepcionista")
    assert permissions_for("profesional") == ["appointments:read_own", "appointments:status_own"]
    assert "appointments:read_all" not in PERMISSIONS["profesional"]


def test_error_shape_unauthorized():
    r = client.get("/api/v1/auth/me")
    assert r.status_code == 401
    assert set(r.json()) == {"code", "message", "details"} and r.json()["code"] == "UNAUTHORIZED"


def test_error_shape_validation():
    r = client.post("/api/v1/auth/login", json={"email": "no-es-correo"})
    assert r.status_code == 422 and r.json()["code"] == "VALIDATION_ERROR"
    assert r.json()["details"]["campos"]


def test_tampered_bearer_rejected_over_http():
    tok, _ = create_access_token(sub=uuid4(), kind="user", tenant_id=uuid4(), rol="admin")
    r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {tok[:-3]}abc"})
    assert r.status_code == 401


def test_catalog_endpoint_and_request_id():
    r = client.get("/api/v1/meta/error-codes")
    assert r.json() == ERROR_CATALOG and r.headers["X-Request-ID"]
    assert {"TENANT_BLOCKED", "MODULE_NOT_ENABLED", "USER_LIMIT_REACHED", "SLOT_TAKEN"} <= set(ERROR_CATALOG)


def test_client_ip_never_breaks_login_on_odd_hosts():
    from types import SimpleNamespace

    from app.core.http import client_ip
    mk = lambda h: SimpleNamespace(client=SimpleNamespace(host=h) if h else None)  # noqa: E731
    assert client_ip(mk("testclient")) is None and client_ip(mk(None)) is None and client_ip(mk("/run/app.sock")) is None
    assert client_ip(mk("190.24.1.9")) == "190.24.1.9" and client_ip(mk("::1")) == "::1"


def test_seed_superadmin_email_passes_validation():
    import re
    from pathlib import Path

    from pydantic import EmailStr, TypeAdapter
    seed = (Path(__file__).resolve().parents[1] / "sql" / "seed.sql").read_text(encoding="utf8")
    email = re.search(r"INSERT INTO platform_users[^(]*\(\s*'([^']+)'", seed.replace("(email, password_hash, nombre) VALUES", "")).group(1)
    TypeAdapter(EmailStr).validate_python(email)      # un dominio .local/.test haría imposible el login
