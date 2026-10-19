"""Fase 9 sin BD: configuración segura de producción, cifrado con rotación, cabeceras/límites/logs, salud, contrato OpenAPI fiel,
semillas y guardas estáticas que impiden reintroducir clases de fallos."""
import json
import logging
import re
import time
from pathlib import Path
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core import ratelimit as rl
from app.core.config import Settings, get_settings
from app.core.errors import ERROR_CATALOG, ERROR_HELP, AppError
from app.core.security import decrypt_secret, encrypt_secret, rotate_secret
from app.core.tenancy import Principal
from app.main import app
from app.scripts.create_superadmin import check_password

ROOT = Path(__file__).resolve().parents[1]
client = TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------- configuración de producción
def prod(**kw):
    base = dict(environment="production", database_url="postgresql+asyncpg://agenda_app:Qx7-largaYaleatoria-91@db.interno:5432/agenda", redis_url="redis://redis:6379/0",
                jwt_secret="k3J9-Zq1vT8xWm2Rb7LcN4pYd6Hs0Gf5Ua1Ee3Oi9Xt2Vr8Mn", fernet_key=Fernet.generate_key().decode(),
                cors_origins="https://app.negocio.co", public_base_url="https://app.negocio.co", api_public_url="https://api.negocio.co")
    return Settings(_env_file=None, **{**base, **kw})


def test_a_correct_production_configuration_is_accepted():
    s = prod()
    assert s.is_production and not s.docs_on and s.cors_list == ["https://app.negocio.co"]


@pytest.mark.parametrize("override,fragment", [
    ({"jwt_secret": "corto"}, "JWT_SECRET"), ({"jwt_secret": "cambia-esto-por-un-secreto-largo-ya-ya-ya-ya"}, "JWT_SECRET"),
    ({"jwt_secret": "x" * 40 + "test"}, "JWT_SECRET"),
    ({"fernet_key": ""}, "FERNET_KEY es obligatoria"), ({"fernet_key": "no-es-una-clave"}, "inválida"),
    ({"cors_origins": ""}, "CORS_ORIGINS"), ({"cors_origins": "*"}, "CORS_ORIGINS"), ({"cors_origins": "http://app.negocio.co"}, "CORS_ORIGINS"),
    ({"database_url": "postgresql+asyncpg://postgres:Zz9-claveLarga-77@db:5432/agenda"}, "superusuario"),
    ({"database_url": "postgresql+asyncpg://agenda_app:agenda_app_dev@db:5432/agenda"}, "superusuario"),
    ({"database_url": "postgresql+asyncpg://agenda_app@db:5432/agenda"}, "superusuario"),
    ({"public_base_url": "http://app.negocio.co"}, "PUBLIC_BASE_URL"), ({"api_public_url": "http://api.negocio.co"}, "API_PUBLIC_URL"),
])
def test_insecure_production_configuration_refuses_to_start(override, fragment):
    with pytest.raises(ValidationError) as e:
        prod(**override)
    assert fragment in str(e.value)


def test_all_problems_are_reported_at_once_and_development_stays_permissive():
    with pytest.raises(ValidationError) as e:
        prod(jwt_secret="x", fernet_key="", cors_origins="*")
    msg = str(e.value)
    assert "JWT_SECRET" in msg and "FERNET_KEY" in msg and "CORS_ORIGINS" in msg
    dev = Settings(_env_file=None, environment="development", database_url="postgresql+asyncpg://postgres:postgres@x/x", redis_url="redis://x", jwt_secret="x")
    assert dev.docs_on and not dev.is_production
    assert prod(docs_enabled=True).docs_on is True and Settings(_env_file=None, environment="test", database_url="x", redis_url="x", jwt_secret="x").docs_on


# ---------------------------------------------------------------- cifrado con rotación de claves
def test_encryption_rotation_flow(monkeypatch):
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()

    def use(keys):
        monkeypatch.setenv("FERNET_KEY", keys)
        get_settings.cache_clear()
    try:
        use(old)
        token_old = encrypt_secret("credencial-de-meta")
        use(f"{new},{old}")                                       # paso 1: la nueva va primera
        assert decrypt_secret(token_old) == "credencial-de-meta"  # sigue descifrando lo viejo
        rotated = rotate_secret(token_old)                        # paso 3: re-cifrar
        assert rotated != token_old and decrypt_secret(encrypt_secret("x")) == "x"
        use(new)                                                  # paso 4: se retira la vieja
        assert decrypt_secret(rotated) == "credencial-de-meta"
        with pytest.raises(Exception):                            # noqa: B017 - InvalidToken: lo no rotado ya no se puede leer
            decrypt_secret(token_old)
        use("")
        with pytest.raises(RuntimeError):
            encrypt_secret("x")
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


# ---------------------------------------------------------------- cabeceras, límites, request-id, logs
def test_security_headers_and_request_id_handling():
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    assert r.headers["X-Content-Type-Options"] == "nosniff" and r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Referrer-Policy"] == "no-referrer" and r.headers["Cache-Control"] == "no-store"
    assert client.get("/health", headers={"X-Request-ID": "abc-12345678"}).headers["X-Request-ID"] == "abc-12345678"
    for bad in ("corto", "con espacios y <script>", "x" * 200):
        rid = client.get("/health", headers={"X-Request-ID": bad}).headers["X-Request-ID"]
        assert rid != bad and re.fullmatch(r"[0-9a-f]{32}", rid)         # no se propaga basura a los logs


def test_oversized_requests_are_rejected_before_any_work():
    r = client.post("/api/v1/auth/login", content=b"x" * (get_settings().max_body_bytes + 1), headers={"Content-Type": "application/json"})
    assert r.status_code == 413 and r.json()["code"] == "VALIDATION_ERROR" and "X-Request-ID" in r.headers


def test_access_log_records_the_route_template_never_the_secret_in_the_url(caplog, monkeypatch):
    from app.modules.booking import router as brouter

    async def nope(slug, **kw):
        raise AppError("NOT_FOUND", "no disponible")
    monkeypatch.setattr(brouter.service, "resolve", nope)
    rl._memory._hits.clear()
    monkeypatch.setattr(rl, "_redis_down_until", time.monotonic() + 600)
    secret = "TOKEN-SECRETO-DE-RESERVA-" + uuid4().hex
    with caplog.at_level(logging.INFO, logger="app.access"):
        r = client.get(f"/api/v1/public/negocio-x/bookings/{secret}")
    assert r.status_code == 404
    recs = [x for x in caplog.records if x.name == "app.access"]
    assert recs and recs[-1].ruta.endswith("/public/{slug}/bookings/{token}") and recs[-1].estado == 404
    assert secret not in json.dumps([x.__dict__ for x in recs], default=str) and recs[-1].ms >= 0


def test_health_endpoints(monkeypatch):
    from app import main as m

    async def fake(db_ok, redis_ok):
        return db_ok, {"db": db_ok, "redis": redis_ok}
    for db_ok, redis_ok, code, status in ((True, True, 200, "ok"), (True, False, 200, "degraded"), (False, True, 503, "down")):
        async def ready(d=db_ok, r=redis_ok):
            return await fake(d, r)
        monkeypatch.setattr(m, "readiness", ready)
        r = client.get("/health/ready")
        assert (r.status_code, r.json()["status"], r.json()["db"], r.json()["redis"]) == (code, status, db_ok, redis_ok)


def test_login_is_rate_limited_per_ip_with_retry_after(monkeypatch):
    rl._memory._hits.clear()
    monkeypatch.setattr(rl, "_redis_down_until", time.monotonic() + 600)       # sin Redis en la prueba: usa memoria sin esperar
    codes = [client.post("/api/v1/auth/login", json={"email": "no-es-correo"}).status_code for _ in range(11)]
    assert codes[:10] == [422] * 10 and codes[10] == 429                      # 10 por minuto
    r = client.post("/api/v1/auth/login", json={})
    assert r.status_code == 429 and r.json()["code"] == "RATE_LIMITED" and int(r.headers["Retry-After"]) >= 1
    rl._memory._hits.clear()


# ---------------------------------------------------------------- el contrato dice la verdad
def test_every_error_code_is_documented_and_the_exported_files_are_current():
    assert set(ERROR_HELP) == set(ERROR_CATALOG) and all(all(t.strip() for t in v) for v in ERROR_HELP.values())
    import importlib.util
    spec = importlib.util.spec_from_file_location("export_openapi", ROOT / "scripts" / "export_openapi.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    stale = [p.name for p, text in mod.build().items() if not p.exists() or p.read_text(encoding="utf-8") != text]
    assert not stale, f"Regenera con `python scripts/export_openapi.py`: {stale}"


def test_openapi_describes_our_real_error_format_and_has_examples_everywhere():
    sp = app.openapi()
    schemas = sp["components"]["schemas"]
    assert "HTTPValidationError" not in schemas and set(schemas["ErrorResponse"]["properties"]) == {"code", "message", "details"}
    assert set(schemas["ErrorResponse"]["properties"]["code"]["enum"]) == set(ERROR_CATALOG)
    n = 0
    for path, ops in sp["paths"].items():
        for method, op in ops.items():
            n += 1
            assert op.get("summary") and "422" in op["responses"], (method, path)
            assert op["responses"]["422"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/ErrorResponse"}
            if "security" in op:
                assert {"401", "403"} <= set(op["responses"]), (method, path)
            body = op.get("requestBody", {}).get("content", {}).get("application/json")
            if body:
                assert "example" in body, (method, path)
            for code, r in op["responses"].items():
                m = r.get("content", {}).get("application/json")
                if code.startswith("2") and m and "schema" in m:
                    assert "example" in m, (method, path, code)
    assert n >= 120
    validator = pytest.importorskip("openapi_spec_validator")
    validator.validate(sp)


def test_public_routes_are_the_only_ones_without_security():
    sp = app.openapi()
    open_routes = {p for p, ops in sp["paths"].items() for o in ops.values() if "security" not in o}
    assert all(p.startswith(("/api/v1/public/", "/api/v1/webhooks/", "/api/v1/auth/", "/api/v1/meta/", "/health")) for p in open_routes), open_routes
    assert not any(p.startswith(("/api/v1/platform", "/api/v1/sales", "/api/v1/cash", "/api/v1/reports", "/api/v1/invoices")) for p in open_routes)


# ---------------------------------------------------------------- semillas y scripts de operación
def test_production_seed_has_no_known_credentials_and_matches_the_dev_seed():
    dev = (ROOT / "sql" / "seed.sql").read_text(encoding="utf-8")
    cat = (ROOT / "sql" / "seed_catalog.sql").read_text(encoding="utf-8")
    assert "platform_users" not in cat and "argon2" not in cat and "Cambiar.Ya" not in cat
    assert "platform_users" in dev
    body = lambda t: t[t.index("INSERT INTO modules"):]                     # noqa: E731 - el catálogo es idéntico en ambas
    assert body(dev) == body(cat), "seed.sql y seed_catalog.sql se desfasaron: el catálogo debe ser idéntico"


@pytest.mark.parametrize("pw,ok", [("Corta1A", False), ("todominusculas1234567", False), ("TODOMAYUSCULAS1234567", False), ("SinNumerosEnAbsoluto", False),
                                   ("aaaaaaaaaaaaaaAAAA1", False), ("Cl4ve-Larga-Y-Segura!", True)])
def test_superadmin_password_policy(pw, ok):
    assert (check_password(pw) is None) is ok


def test_principal_tid_fails_loudly_for_platform_users():
    ok = Principal(uuid4(), "user", uuid4(), "admin")
    assert ok.tid == ok.tenant_id
    with pytest.raises(AppError) as e:
        _ = Principal(uuid4(), "platform_user", None, "super_admin").tid
    assert e.value.code == "FORBIDDEN"


def _working_bash() -> str | None:
    """Un bash que de verdad funcione (en Windows, `bash.exe` suele ser el lanzador de WSL sin distribución instalada)."""
    import shutil
    import subprocess
    for cand in (shutil.which("bash"), r"C:\Program Files\Gitinash.exe", "/bin/bash"):
        if cand and Path(cand).exists():
            try:
                if subprocess.run([cand, "-c", "exit 0"], capture_output=True, timeout=20).returncode == 0:   # noqa: S603
                    return cand
            except OSError:
                continue
    return None


def test_ops_scripts_are_valid_shell_and_never_embed_secrets():
    import subprocess
    bash = _working_bash()
    for f in ("backup.sh", "restore.sh", "init_db.sh"):
        text = (ROOT / "scripts" / f).read_text(encoding="utf-8")
        assert text.startswith("#!/bin/sh") and "set -eu" in text and "\r" not in text
        assert not re.search(r"postgres(ql)?://\w+:(?!clave|CAMBIAR)[^@\s$]+@", text)     # ninguna URL con clave real escrita (los ejemplos usan "clave")
        if bash:
            r = subprocess.run([bash, "-n", (ROOT / "scripts" / f).as_posix()], capture_output=True, text=True)   # noqa: S603
            assert r.returncode == 0, r.stderr


# ---------------------------------------------------------------- guardas estáticas (impiden reintroducir clases enteras de fallos)
APP = ROOT / "app"
PY = [p for p in APP.rglob("*.py")]


def test_no_sql_is_built_by_concatenating_or_formatting_user_data():
    bad = re.compile(r"""(f|rf|fr)(["']{1,3})[^\n]*\b(SELECT|INSERT INTO|UPDATE|DELETE FROM)\b[^\n]*\{""", re.I)
    hits = [f"{p.relative_to(ROOT)}:{i}" for p in PY for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
            if bad.search(line) and "noqa: S608" not in line]
    assert hits == [], f"SQL armado con f-string (usa parámetros): {hits}"


def test_no_hardcoded_secrets_or_debug_leftovers_in_the_application():
    secret = re.compile(r"""(password|secret|api_key|token)\w*\s*=\s*["'][^"'\s]{8,}["']""", re.I)
    ok_words = ("noqa: s105", "noqa: s106", "bearer", "booking")
    hits = [f"{p.relative_to(ROOT)}:{i}" for p in PY for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
            if secret.search(line) and not any(w in line.lower() for w in ok_words)]
    assert hits == [], hits
    leftovers = [p.name for p in PY if re.search(r"^\s*(print\(|breakpoint\(|import pdb)", p.read_text(encoding="utf-8"), re.M) and "scripts" not in str(p)]
    assert leftovers == [], leftovers


def test_every_business_service_uses_the_safe_tenant_accessor():
    """`p.tenant_id` (opcional) solo se usa en el núcleo de tenancy/auth; los servicios usan `p.tid` (falla claro si no hay negocio)."""
    offenders = [str(p.relative_to(ROOT)) for p in (APP / "modules").rglob("*.py")
                 if re.search(r"\bp\.tenant_id\b", p.read_text(encoding="utf-8")) and "platform" not in p.parts and p.name != "service.py" or False]
    assert offenders == [], offenders
    for p in (APP / "modules").rglob("*.py"):
        if "platform" in p.parts or p.parts[-2] == "auth":
            continue
        assert not re.search(r"\bp\.tenant_id\b", p.read_text(encoding="utf-8")), f"{p.relative_to(ROOT)} usa p.tenant_id (opcional)"


def test_docker_and_compose_files_follow_the_safety_basics():
    docker = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "USER app" in docker and "HEALTHCHECK" in docker and "--proxy-headers" in docker and "--reload" not in docker
    prod_compose = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    assert "pgadmin" not in prod_compose and "ports:" in prod_compose and prod_compose.count("ports:") == 1          # solo Caddy publica puertos
    assert "restart: unless-stopped" in prod_compose and "healthcheck" in prod_compose
    dev_compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "127.0.0.1:5432" in dev_compose and '"5432:5432"' not in dev_compose and '"8000:8000"' not in dev_compose   # nada de desarrollo expuesto a la red
    env_prod = (ROOT / ".env.production.example").read_text(encoding="utf-8")
    assert "ENVIRONMENT=production" in env_prod and "DOCS_ENABLED=false" in env_prod and "agenda_app_dev" not in env_prod
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in gitignore.splitlines()

