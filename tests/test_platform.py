"""Fase 3 sin BD: reglas puras de suscripción/precio, guardas de acceso y contrato OpenAPI."""
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_access_token, decode_access_token
from app.core.tenancy import evaluate_access
from app.main import app
from app.modules.platform.service import compute_billing, estado_por_dias

client = TestClient(app, raise_server_exceptions=False)
HOY = date(2026, 10, 15)


@pytest.mark.parametrize("dias,grace,esperado", [
    (30, 0, "activa"), (8, 0, "activa"), (7, 0, "por_vencer"), (0, 0, "por_vencer"),
    (-1, 0, "bloqueada"), (-1, 3, "vencida"), (-3, 3, "vencida"), (-4, 3, "bloqueada"),
])
def test_estado_por_dias(dias, grace, esperado):
    assert estado_por_dias(dias, grace) == esperado


def test_billing_plan_mas_adicionales_con_override():
    b = compute_billing("Básico", Decimal("79000"), [
        ("Reportes", Decimal("20000"), None),                # precio base
        ("Asistente IA", Decimal("50000"), Decimal("35000")),  # precio manual del Super Admin
        ("Inventario", Decimal("20000"), Decimal("0")),      # override 0 = regalo (no cae al base)
    ])
    assert b.total_mensual == Decimal("134000")
    assert [i.origen_precio for i in b.items] == ["plan", "base", "manual", "manual"]


@pytest.mark.parametrize("kw,bloqueado,motivo", [
    (dict(bloqueo_manual=False, fecha_fin=HOY), False, None),                               # vence hoy: aún activo
    (dict(bloqueo_manual=False, fecha_fin=HOY - timedelta(days=1)), True, "vencimiento"),  # ayer: bloqueado
    (dict(bloqueo_manual=True, fecha_fin=HOY + timedelta(days=90)), True, "manual"),
    (dict(bloqueo_manual=False, fecha_fin=None), True, "sin_suscripcion"),
])
def test_evaluate_access(kw, bloqueado, motivo):
    a = evaluate_access(estado="activa", today=HOY, grace_days=0, **kw)
    assert (a.bloqueado, a.motivo) == (bloqueado, motivo)


def test_evaluate_access_grace_days():
    ayer = HOY - timedelta(days=1)
    assert not evaluate_access(bloqueo_manual=False, estado="vencida", fecha_fin=ayer, today=HOY, grace_days=2).bloqueado
    assert evaluate_access(bloqueo_manual=False, estado="vencida", fecha_fin=ayer, today=HOY, grace_days=0).bloqueado


def test_impersonation_token_carries_imp_and_short_ttl():
    sa, admin, tid = uuid4(), uuid4(), uuid4()
    tok, ttl = create_access_token(sub=admin, kind="user", tenant_id=tid, rol="admin", imp=sa, ttl_minutes=30)
    c = decode_access_token(tok)
    assert c["imp"] == str(sa) and c["tid"] == str(tid) and ttl == 1800


def test_platform_routes_reject_tenant_users_and_anonymous():
    assert client.get("/api/v1/platform/dashboard").status_code == 401
    tok, _ = create_access_token(sub=uuid4(), kind="user", tenant_id=uuid4(), rol="admin")
    r = client.get("/api/v1/platform/dashboard", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 403 and r.json()["code"] == "FORBIDDEN"      # sin tocar la BD


def test_every_platform_route_is_superadmin_only():
    tok, _ = create_access_token(sub=uuid4(), kind="user", tenant_id=uuid4(), rol="admin")
    h = {"Authorization": f"Bearer {tok}"}
    tid = uuid4()
    for method, path in [("get", "/tenants"), ("post", f"/tenants/{tid}/impersonate"), ("get", "/plans"),
                         ("post", f"/tenants/{tid}/payments"), ("put", f"/tenants/{tid}/plan"),
                         ("post", f"/tenants/{tid}/estado/bloquear")]:
        r = getattr(client, method)(f"/api/v1/platform{path}", headers=h)
        assert r.status_code == 403, (method, path, r.status_code)


def test_openapi_builds_with_platform_and_pagination():
    spec = app.openapi()
    assert "/api/v1/platform/tenants" in spec["paths"] and "/api/v1/platform/tenants/{tenant_id}/payments" in spec["paths"]
    assert any(k.startswith("Page_TenantListItem") for k in spec["components"]["schemas"])
