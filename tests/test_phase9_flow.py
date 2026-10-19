"""Fase 9 contra Postgres real: RLS en TODAS las tablas, privilegios del rol de la API, arranque seguro, flujo de autenticación completo,
rotación de claves, alta de super admin y capacidad bajo concurrencia."""
import asyncio
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pyotp
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_phase4_flow import _setup
from test_phase5_flow import _svc
from test_phase6_flow import _pro, _tenant

from app.core import ratelimit as rl
from app.core import startup
from app.core.config import get_settings
from app.core.db import scoped_session
from app.core.errors import AppError
from app.core.pagination import PageParams
from app.core.security import hash_password, hash_token
from app.core.tenancy import Principal
from app.main import app
from app.modules.auth import service as auth
from app.modules.auth.models import PasswordResetToken
from app.modules.customers import service as customers
from app.modules.customers.schemas import CustomerIn, CustomerPatch
from app.modules.integrations import service as integ
from app.modules.integrations.schemas import WhatsAppIn
from app.scripts import create_superadmin, rotate_keys

PASSWORD = "Clave.Larga.123"


@pytest.fixture(autouse=True)
async def _needs_db():
    try:
        async with scoped_session(platform=True) as db:
            await db.execute(text("SELECT 1 FROM plans LIMIT 1"))
    except Exception:                                                              # noqa: BLE001
        pytest.skip("Postgres no disponible")


async def _sql(sql: str, **prm):
    async with scoped_session(platform=True) as db:
        res = await db.execute(text(sql), prm)
        return res.all() if res.returns_rows else []


async def _user(plan="Básico"):
    p = await _setup(plan)
    slug, email = (await _sql("SELECT t.slug, u.email FROM tenants t JOIN users u ON u.tenant_id = t.id WHERE t.id = :t", t=p.tenant_id))[0]
    return p, str(slug), str(email)


# ================================================================== aislamiento: ninguna tabla se escapa
PUBLIC_CATALOG = {"plans", "modules", "plan_modules", "business_types", "business_type_templates"}      # catálogo global, sin datos de ningún negocio


async def test_every_table_is_either_global_catalog_or_protected_by_forced_rls():
    rows = await _sql("""
        SELECT c.relname, c.relrowsecurity AS rls, c.relforcerowsecurity AS forzada,
               EXISTS (SELECT 1 FROM information_schema.columns k WHERE k.table_schema = 'public' AND k.table_name = c.relname AND k.column_name = 'tenant_id') AS con_tenant,
               (SELECT count(*) FROM pg_policies p WHERE p.schemaname = 'public' AND p.tablename = c.relname) AS politicas
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND c.relkind = 'r' ORDER BY 1""")
    assert len(rows) >= 38
    sin_rls = {r.relname for r in rows if not (r.rls and r.forzada and r.politicas >= 1)}
    assert sin_rls == PUBLIC_CATALOG, f"Tablas SIN RLS que no son catálogo global (¡fuga entre negocios!): {sorted(sin_rls - PUBLIC_CATALOG)}; faltan del catálogo: {sorted(PUBLIC_CATALOG - sin_rls)}"
    assert all(r.rls and r.forzada for r in rows if r.con_tenant)                      # toda tabla con tenant_id está protegida y FORZADA (aplica al dueño también)
    assert {r.relname for r in rows if r.relname in ("tenants", "platform_users")} == {"tenants", "platform_users"}


async def test_api_role_cannot_escalate_or_tamper_with_audit_and_has_the_configured_limits():
    async with scoped_session(platform=True) as db:
        r = (await db.execute(text("SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolreplication FROM pg_roles WHERE rolname = current_user"))).one()
        assert not any(r), "El rol de la API tiene privilegios que anulan RLS o permiten escalar"
        assert (await db.execute(text("SHOW statement_timeout"))).scalar() == f"{get_settings().db_statement_timeout_ms // 1000}s"
        assert (await db.execute(text("SHOW application_name"))).scalar() == "agenda-saas"
    for sql in ("CREATE TABLE intruso (id int)", "ALTER TABLE users DISABLE ROW LEVEL SECURITY", "DROP TABLE audit_logs", "CREATE ROLE otro SUPERUSER",
                "UPDATE audit_logs SET accion = 'borrado'", "DELETE FROM audit_logs", "SET ROLE postgres", "TRUNCATE TABLE audit_logs"):
        with pytest.raises(DBAPIError):                                                  # permiso denegado: cada intento en su propia transacción
            async with scoped_session(platform=True) as db:
                await db.execute(text(sql))


# ================================================================== arranque seguro y salud
async def test_startup_checks_flag_the_dev_superadmin_and_abort_in_production(monkeypatch):
    problems = await startup.startup_checks()                                           # entorno de pruebas: solo avisa
    assert any("super admin de desarrollo" in p for p in problems) and not any("BYPASSRLS" in p for p in problems)
    monkeypatch.setattr(startup, "get_settings", lambda: SimpleNamespace(is_production=True))
    with pytest.raises(RuntimeError, match="Arranque abortado"):
        await startup.startup_checks()
    await _sql("UPDATE platform_users SET email = 'dev-renombrado@agenda-saas.co' WHERE email = :e", e=startup.DEV_ADMIN_EMAIL)
    try:
        assert await startup.startup_checks() == []                                     # sin el usuario por defecto, producción arranca
    finally:
        await _sql("UPDATE platform_users SET email = :e WHERE email = 'dev-renombrado@agenda-saas.co'", e=startup.DEV_ADMIN_EMAIL)


async def test_readiness_reports_the_database_and_http_health_works():
    ok, detail = await startup.readiness()
    assert ok and detail["db"] is True
    rl_state = rl._redis_down_until
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/health/ready")
        assert r.status_code == 200 and r.json()["db"] is True and r.json()["status"] in ("ok", "degraded")
        assert (await c.get("/health")).json() == {"status": "ok"}
    rl._redis_down_until = rl_state


# ================================================================== autenticación de punta a punta (antes solo probada a mano)
async def test_login_errors_do_not_reveal_which_part_was_wrong():
    p, slug, email = await _user()
    msgs = set()
    for args in ((slug, email, "mala-clave"), (slug, "nadie@x.co", PASSWORD), ("negocio-que-no-existe", email, PASSWORD)):
        with pytest.raises(AppError) as e:
            await auth.login(*args, None, "127.0.0.1")
        assert e.value.code == "INVALID_CREDENTIALS" and e.value.status == 401
        msgs.add(e.value.message)
    assert len(msgs) == 1                                                                # el mismo mensaje en los tres casos
    ok = await auth.login(slug, email, PASSWORD, None, "127.0.0.1")
    assert ok.access_token and ok.refresh_token and ok.expires_in == get_settings().jwt_access_minutes * 60
    assert (await _sql("SELECT count(*) AS n FROM audit_logs WHERE tenant_id = :t AND accion = 'login_fallido'", t=p.tenant_id))[0].n == 2   # el negocio inexistente no tiene dónde auditarse


async def test_lockout_after_repeated_failures_even_with_the_right_password_then_recovers():
    p, slug, email = await _user()
    for _ in range(get_settings().login_max_attempts):
        with pytest.raises(AppError):
            await auth.login(slug, email, "mala", None, None)
    with pytest.raises(AppError) as e:
        await auth.login(slug, email, PASSWORD, None, None)                              # la clave correcta tampoco entra mientras dure el bloqueo
    assert e.value.code == "ACCOUNT_LOCKED" and e.value.status == 423 and "bloqueado_hasta" in e.value.details
    await _sql("UPDATE users SET bloqueado_hasta = now() - interval '1 minute' WHERE tenant_id = :t", t=p.tenant_id)
    await auth.login(slug, email, PASSWORD, None, None)
    row = (await _sql("SELECT intentos_fallidos, bloqueado_hasta FROM users WHERE tenant_id = :t", t=p.tenant_id))[0]
    assert (row.intentos_fallidos, row.bloqueado_hasta) == (0, None)


async def test_refresh_rotation_reuse_detection_and_logout():
    _, slug, email = await _user()
    first = await auth.login(slug, email, PASSWORD, None, None)
    second = await auth.refresh(first.refresh_token, None)
    assert second.refresh_token != first.refresh_token and second.access_token
    with pytest.raises(AppError) as e:                                                   # alguien reutiliza el token viejo: robo probable
        await auth.refresh(first.refresh_token, None)
    assert e.value.code == "REFRESH_REUSED"
    with pytest.raises(AppError) as e:                                                   # y se mata TODA la cadena, incluido el token nuevo
        await auth.refresh(second.refresh_token, None)
    assert e.value.code == "REFRESH_REUSED"
    third = await auth.login(slug, email, PASSWORD, None, None)
    await auth.logout(third.refresh_token)
    with pytest.raises(AppError):
        await auth.refresh(third.refresh_token, None)
    with pytest.raises(AppError) as e:
        await auth.refresh("token-inventado-" + uuid4().hex, None)
    assert e.value.code == "TOKEN_EXPIRED"


async def test_change_password_closes_every_other_session():
    p, slug, email = await _user()
    a, b = await auth.login(slug, email, PASSWORD, None, None), await auth.login(slug, email, PASSWORD, None, None)
    uid = (await _sql("SELECT id FROM users WHERE tenant_id = :t", t=p.tenant_id))[0].id
    me = Principal(uid, "user", p.tenant_id, "admin")
    async with scoped_session(tenant_id=p.tenant_id) as db:
        with pytest.raises(AppError) as e:
            await auth.change_password(db, me, "no-es-la-actual", "Nueva.Clave.456!", None)
        assert e.value.code == "INVALID_CREDENTIALS"
    async with scoped_session(tenant_id=p.tenant_id) as db:
        await auth.change_password(db, me, PASSWORD, "Nueva.Clave.456!", None)
    for pair in (a, b):
        with pytest.raises(AppError):
            await auth.refresh(pair.refresh_token, None)
    with pytest.raises(AppError):
        await auth.login(slug, email, PASSWORD, None, None)
    await auth.login(slug, email, "Nueva.Clave.456!", None, None)


async def test_password_reset_is_single_use_expires_and_never_reveals_accounts():
    p, slug, email = await _user()
    await auth.forgot_password(slug, "nadie@x.co")                                       # cuenta inexistente: responde igual y no crea nada
    await auth.forgot_password("negocio-que-no-existe", email)
    assert (await _sql("SELECT count(*) AS n FROM password_reset_tokens WHERE tenant_id = :t", t=p.tenant_id))[0].n == 0
    await auth.forgot_password(slug, email)
    assert (await _sql("SELECT count(*) AS n FROM password_reset_tokens WHERE tenant_id = :t", t=p.tenant_id))[0].n == 1
    uid = (await _sql("SELECT id FROM users WHERE tenant_id = :t", t=p.tenant_id))[0].id

    async def issue(raw, minutes=60):
        async with scoped_session(platform=True) as db:
            db.add(PasswordResetToken(sujeto_tipo="user", sujeto_id=uid, tenant_id=p.tenant_id, token_hash=hash_token(raw),
                                      expira_en=datetime.now(UTC) + timedelta(minutes=minutes)))
    old = await auth.login(slug, email, PASSWORD, None, None)
    await issue("tok-valido-" + uuid4().hex[:12])
    raw = "tok-uno-" + uuid4().hex
    await issue(raw)
    await auth.reset_password(raw, "Restablecida.789!", None)
    with pytest.raises(AppError) as e:                                                   # un enlace sirve UNA vez
        await auth.reset_password(raw, "Otra.Clave.000!", None)
    assert e.value.code == "UNAUTHORIZED"
    with pytest.raises(AppError):
        await auth.refresh(old.refresh_token, None)                                      # reiniciar la clave cierra las sesiones abiertas
    await auth.login(slug, email, "Restablecida.789!", None, None)
    expired = "tok-vencido-" + uuid4().hex
    await issue(expired, minutes=-5)
    with pytest.raises(AppError) as e:
        await auth.reset_password(expired, "Cualquiera.123456!", None)
    assert e.value.code == "UNAUTHORIZED"


async def test_two_factor_setup_enable_login_and_admin_only():
    p, slug, email = await _user("Básico")
    uid = (await _sql("SELECT id FROM users WHERE tenant_id = :t", t=p.tenant_id))[0].id
    admin = Principal(uid, "user", p.tenant_id, "admin")
    async with scoped_session(tenant_id=p.tenant_id) as db:
        secret, uri = await auth.totp_setup(db, admin)
    assert uri.startswith("otpauth://totp/") and "Agenda" in uri
    raw = (await _sql("SELECT totp_secret, totp_habilitado FROM users WHERE tenant_id = :t", t=p.tenant_id))[0]
    assert secret not in raw.totp_secret and not raw.totp_habilitado                    # cifrado en la BD y aún sin activar
    await auth.login(slug, email, PASSWORD, None, None)                                  # sin activar no se pide código
    async with scoped_session(tenant_id=p.tenant_id) as db:
        with pytest.raises(AppError) as e:
            await auth.totp_enable(db, admin, "000000", None)
        assert e.value.code == "INVALID_TOTP"
    async with scoped_session(tenant_id=p.tenant_id) as db:
        await auth.totp_enable(db, admin, pyotp.TOTP(secret).now(), None)
    with pytest.raises(AppError) as e:
        await auth.login(slug, email, PASSWORD, None, None)
    assert e.value.code == "TOTP_REQUIRED"
    with pytest.raises(AppError) as e:
        await auth.login(slug, email, PASSWORD, "000000", None)
    assert e.value.code == "INVALID_CREDENTIALS"                                         # el código malo cuenta como intento fallido y no da pistas
    assert (await auth.login(slug, email, PASSWORD, pyotp.TOTP(secret).now(), None)).access_token
    rec = Principal(uuid4(), "user", p.tenant_id, "recepcionista")
    async with scoped_session(tenant_id=p.tenant_id) as db:
        with pytest.raises(AppError):
            await auth.totp_setup(db, rec)


async def test_support_sessions_cannot_change_credentials_and_blocked_tenants_can_still_read_me():
    p, slug, email = await _user()
    uid = (await _sql("SELECT id FROM users WHERE tenant_id = :t", t=p.tenant_id))[0].id
    imp = Principal(uid, "user", p.tenant_id, "admin", impersonator_id=uuid4())
    async with scoped_session(tenant_id=p.tenant_id) as db:
        for call in (lambda: auth.change_password(db, imp, PASSWORD, "Otra.Clave.123456!", None), lambda: auth.totp_setup(db, imp)):
            with pytest.raises(AppError) as e:
                await call()
            assert e.value.code == "FORBIDDEN"
    await _sql("UPDATE tenants SET bloqueo_manual = true, estado = 'suspendida' WHERE id = :t", t=p.tenant_id)
    async with scoped_session(tenant_id=p.tenant_id) as db:
        me = await auth.me(db, Principal(uid, "user", p.tenant_id, "admin"))
    assert me.tenant.bloqueado and me.tenant.motivo_bloqueo == "manual" and me.rol == "admin" and "users:manage" in me.permisos


async def test_platform_and_tenant_tokens_cannot_cross_over():
    from app.core.security import create_access_token
    p, _, _ = await _user()
    plat, _ = create_access_token(sub=uuid4(), kind="platform_user", tenant_id=None, rol="super_admin")
    ten, _ = create_access_token(sub=uuid4(), kind="user", tenant_id=p.tenant_id, rol="admin")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/customers", headers={"Authorization": f"Bearer {plat}"})
        assert r.status_code == 403 and r.json()["code"] == "FORBIDDEN"                  # el Super Admin no opera datos de un negocio sin impersonar
        r2 = await c.get("/api/v1/platform/tenants", headers={"Authorization": f"Bearer {ten}"})
        assert r2.status_code == 403 and r2.json()["code"] == "FORBIDDEN"


# ================================================================== operación: claves y super admin
async def test_key_rotation_reencrypts_everything_and_reports_unreadable_values(monkeypatch):
    old = get_settings().fernet_key
    from cryptography.fernet import Fernet
    new = Fernet.generate_key().decode()
    p = await _setup("Premium")
    pid = str(uuid4().int)[:12]
    async with scoped_session(tenant_id=p.tenant_id) as db:
        await integ.put_whatsapp(db, p, WhatsAppIn(phone_number_id=pid, access_token="EAAG" + "x" * 40, app_secret="secreto-de-la-app-1"))
    bad_id = (await _sql("INSERT INTO integrations (tenant_id, tipo, credenciales_cifradas, estado) VALUES (:t, 'ai', 'esto-no-es-un-token-fernet', 'pendiente') RETURNING id", t=p.tenant_id))[0].id
    try:
        monkeypatch.setenv("FERNET_KEY", f"{new},{old}")
        get_settings.cache_clear()
        res = await rotate_keys.rotate()
        mine = [x for x in res["ok"] if x.startswith("integrations.")]
        assert len(mine) >= 1 and any(f.endswith(str(bad_id)) for f in res["fallidos"])           # el ilegible se informa, no aborta al resto
        monkeypatch.setenv("FERNET_KEY", new)                                                        # ya se puede retirar la clave vieja
        get_settings.cache_clear()
        async with scoped_session(tenant_id=p.tenant_id) as db:
            c = integ.creds_of(await integ.get_integration(db, p.tenant_id, integ.WA))
        assert c["access_token"].startswith("EAAG") and c["app_secret"] == "secreto-de-la-app-1"
    finally:
        monkeypatch.setenv("FERNET_KEY", old)
        get_settings.cache_clear()
        await _sql("DELETE FROM integrations WHERE id = :i", i=bad_id)
        async with scoped_session(platform=True) as db:                                              # re-cifrar de vuelta con la clave de pruebas para no dejar basura ilegible
            monkeypatch.setenv("FERNET_KEY", f"{old},{new}")
            get_settings.cache_clear()
        await rotate_keys.rotate()
        monkeypatch.setenv("FERNET_KEY", old)
        get_settings.cache_clear()


async def test_create_superadmin_script_creates_updates_and_revokes_sessions():
    email = f"op-{uuid4().hex[:8]}@agenda-saas.co"
    assert await create_superadmin.upsert(email, "Operador Uno", "Clave.Larga.Segura.1") is True
    tokens = await auth.platform_login(email, "Clave.Larga.Segura.1", None, "127.0.0.1")
    assert tokens.access_token
    assert await create_superadmin.upsert(email, "Operador Uno", "Otra.Clave.Segura.2") is False            # actualizar, no duplicar
    with pytest.raises(AppError):
        await auth.refresh(tokens.refresh_token, None)                                                       # sesiones anteriores cerradas
    with pytest.raises(AppError):
        await auth.platform_login(email, "Clave.Larga.Segura.1", None, None)
    assert (await auth.platform_login(email, "Otra.Clave.Segura.2", None, None)).access_token
    assert (await _sql("SELECT count(*) AS n FROM platform_users WHERE email = :e", e=email))[0].n == 1
    assert hash_password("x") != hash_password("x")                                                          # sal distinta en cada hash


# ================================================================== capacidad bajo concurrencia (humo; la medición fina es scripts/load_test.py)
async def test_the_connection_pool_survives_a_burst_of_public_traffic(monkeypatch):
    async def no_limit(key, window):
        return 1, window
    monkeypatch.setattr(rl, "hit", no_limit)                                                                 # aquí se mide capacidad, no el limitador
    p, slug = await _tenant()
    sv = await _svc(p, "Corte", 30000)
    await _pro(p, "Ana", sv)
    await _pro(p, "Beto", sv)
    day = (datetime.now(UTC) + timedelta(days=3)).date().isoformat()
    t0 = time.perf_counter()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=60) as c:
        avail = await asyncio.gather(*[c.get(f"/api/v1/public/{slug}/availability", params={"fecha_desde": day, "service_ids": str(sv)}) for _ in range(60)])
        assert {r.status_code for r in avail} == {200}
        slots = avail[0].json()["dias"][0]["horarios"]

        async def book(i):
            return await c.post(f"/api/v1/public/{slug}/bookings", json={
                "service_ids": [str(sv)], "inicio": slots[i * 2], "cliente": {"nombre": f"Cliente {i}", "telefono": f"31200000{i:02d}"}, "consentimiento_datos": True})
        booked = await asyncio.gather(*[book(i) for i in range(20)])
    elapsed = time.perf_counter() - t0
    assert [r.status_code for r in booked].count(201) == 20, [r.json() for r in booked if r.status_code != 201][:2]
    n = (await _sql("SELECT count(*) AS n FROM appointments WHERE tenant_id = :t", t=p.tenant_id))[0].n
    assert n == 20 and elapsed < 60, (n, elapsed)


async def test_customer_phone_is_stored_like_the_public_booking_does_and_search_ignores_spaces():
    p = await _setup()
    async with scoped_session(tenant_id=p.tenant_id) as db:
        c = await customers.create_customer(db, p, CustomerIn(nombre="Julián Herrera", telefono="+57 311 555 0199"))
        assert c.telefono == "3115550199"                                              # igual que la reserva en línea: no se duplica al reservar luego
        c2 = await customers.patch_customer(db, p, c.id, CustomerPatch(telefono="311-555-0199"))
        assert c2.telefono == "3115550199"
        assert (await customers.list_customers(db, p, PageParams(1, 20, None), "311 555")).total == 1
        with pytest.raises(AppError) as e:
            await customers.create_customer(db, p, CustomerIn(nombre="Sin celular valido", telefono="abc"))
        assert e.value.code == "VALIDATION_ERROR"
        sin = await customers.create_customer(db, p, CustomerIn(nombre="Sin celular", telefono="  "))
        assert sin.telefono is None
