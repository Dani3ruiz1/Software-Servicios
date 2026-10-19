"""Fase 4 sin BD: guardas de acceso en TODAS las rutas, RBAC por rol, validaciones y reglas puras."""
import re
from datetime import date, time
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.security import create_access_token
from app.main import app
from app.modules.appointments.schemas import AppointmentIn
from app.modules.appointments.service import range_for
from app.modules.catalog.service import normalize_iva
from app.modules.staff.schemas import BlockIn, ScheduleIn, ScheduleItem
from app.modules.users.service import seats_available

client = TestClient(app, raise_server_exceptions=False)
PUBLIC = {"/api/v1/auth/login", "/api/v1/auth/platform/login", "/api/v1/auth/refresh", "/api/v1/auth/logout",
          "/api/v1/auth/forgot-password", "/api/v1/auth/reset-password", "/api/v1/meta/error-codes"}


def _tok(rol: str) -> dict[str, str]:
    t, _ = create_access_token(sub=uuid4(), kind="user", tenant_id=uuid4(), rol=rol)
    return {"Authorization": f"Bearer {t}"}


def _routes():
    for path, ops in app.openapi()["paths"].items():
        if path in PUBLIC or not path.startswith("/api/v1") or path.startswith(("/api/v1/public/", "/api/v1/webhooks/")):   # públicas: ver test_phase6 y test_phase7
            continue
        for method in ops:
            yield method, re.sub(r"\{[^}]+\}", str(uuid4()), path)


def test_every_protected_route_rejects_anonymous():
    checked = 0
    for method, url in _routes():
        r = client.request(method.upper(), url, json={})
        assert r.status_code == 401, (method, url, r.status_code)
        checked += 1
    assert checked >= 60            # auth + plataforma + fases 3 y 4


@pytest.mark.parametrize("method,url", [
    ("post", "/api/v1/users"), ("get", "/api/v1/users"), ("post", "/api/v1/staff"),
    ("post", "/api/v1/catalog/services"), ("post", "/api/v1/catalog/categories"),
    ("post", "/api/v1/customers"), (  "put", f"/api/v1/staff/{uuid4()}/schedule"),
])
def test_profesional_cannot_write_or_manage(method, url):
    r = client.request(method.upper(), url, headers=_tok("profesional"), json={})
    assert r.status_code == 403 and r.json()["code"] == "FORBIDDEN", (url, r.status_code)


@pytest.mark.parametrize("method,url", [
    ("post", "/api/v1/users"), ("post", "/api/v1/staff"), ("post", "/api/v1/catalog/services"),
])
def test_recepcionista_cannot_manage_users_staff_or_catalog(method, url):
    assert client.request(method.upper(), url, headers=_tok("recepcionista"), json={}).status_code == 403


def test_appointment_requires_service_or_duration_and_one_assignment_mode():
    base = {"customer_id": str(uuid4()), "inicio": "2026-10-20T10:00:00-05:00"}
    with pytest.raises(ValidationError):
        AppointmentIn(**base)
    with pytest.raises(ValidationError):
        AppointmentIn(**base, duracion_min=30, staff_id=str(uuid4()), asignar_automatico=True)
    with pytest.raises(ValidationError):
        AppointmentIn(customer_id=str(uuid4()), inicio="2026-10-20T10:00:00", duracion_min=30)   # sin zona horaria
    assert AppointmentIn(**base, duracion_min=30).staff_id is None


def test_schedule_validation():
    ok = ScheduleIn(tramos=[ScheduleItem(dia_semana=0, hora_inicio=time(9), hora_fin=time(13)),
                            ScheduleItem(dia_semana=0, hora_inicio=time(14), hora_fin=time(19))])
    assert len(ok.tramos) == 2
    with pytest.raises(ValidationError):
        ScheduleIn(tramos=[ScheduleItem(dia_semana=1, hora_inicio=time(9), hora_fin=time(13)),
                           ScheduleItem(dia_semana=1, hora_inicio=time(12), hora_fin=time(15))])
    with pytest.raises(ValidationError):
        ScheduleItem(dia_semana=7, hora_inicio=time(9), hora_fin=time(10))
    with pytest.raises(ValidationError):
        ScheduleItem(dia_semana=2, hora_inicio=time(10), hora_fin=time(10))


def test_block_requires_timezone_and_order():
    with pytest.raises(ValidationError):
        BlockIn(inicio="2026-10-20T10:00:00", fin="2026-10-20T11:00:00")
    with pytest.raises(ValidationError):
        BlockIn(inicio="2026-10-20T11:00:00-05:00", fin="2026-10-20T10:00:00-05:00")


def test_calendar_ranges():
    assert range_for("dia", date(2026, 10, 15)) == (date(2026, 10, 15), date(2026, 10, 16))
    assert range_for("semana", date(2026, 10, 15)) == (date(2026, 10, 12), date(2026, 10, 19))   # lunes a lunes
    assert range_for("mes", date(2026, 12, 15)) == (date(2026, 12, 1), date(2027, 1, 1))
    assert range_for("mes", date(2026, 2, 28)) == (date(2026, 2, 1), date(2026, 3, 1))


def test_iva_normalization_and_seats():
    assert normalize_iva(False, Decimal("19")) == 0 and normalize_iva(True, None) == 19 and normalize_iva(True, Decimal("5")) == 5
    assert seats_available(4, 5) and not seats_available(5, 5) and not seats_available(1, 1)
