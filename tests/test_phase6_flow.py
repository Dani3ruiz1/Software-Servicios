"""Fase 6 contra Postgres real: reservas públicas de punta a punta, concurrencia, token, anti-abuso y aislamiento."""
import asyncio
from datetime import UTC, datetime, time, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from test_phase4_flow import _setup
from test_phase5_flow import _svc

from app.core.db import scoped_session
from app.core.errors import AppError
from app.core.tenancy import Principal
from app.modules.booking import service as booking
from app.modules.booking.schemas import (
    BookingIn,
    CancelIn,
    FieldIn,
    RescheduleIn,
    RuleIn,
    SettingsIn,
)
from app.modules.staff import service as staff
from app.modules.staff.schemas import ScheduleIn, ScheduleItem, StaffIn

T0 = (datetime.now(UTC) + timedelta(days=3)).replace(minute=0, second=0, microsecond=0)


async def _tenant(plan="Básico", **cfg) -> tuple[Principal, str]:
    p = await _setup(plan)
    slug = f"r-{uuid4().hex[:10]}"
    async with scoped_session(tenant_id=p.tenant_id) as db:
        await booking.put_admin_settings(db, p, SettingsIn(slug=slug, activo=True, **{"anticipacion_min_horas": 1, "cancelacion_min_horas": 3, **cfg}))
    return p, slug


async def _pro(p: Principal, nombre: str, *svc_ids: UUID) -> UUID:
    async with scoped_session(tenant_id=p.tenant_id) as db:
        st = await staff.create_staff(db, p, StaffIn(nombre_publico=nombre, service_ids=list(svc_ids)))
        await staff.set_schedule(db, p, st.id, ScheduleIn(tramos=[ScheduleItem(dia_semana=d, hora_inicio=time(0, 0), hora_fin=time(23, 59))
                                                                 for d in range(7)]))
    return st.id


def _in(svc: UUID | None, when: datetime = T0, phone="3001234567", nombre="Ana Ruiz", email=None, staff_id=None, **kw) -> BookingIn:
    return BookingIn(service_ids=[svc] if svc else [], staff_id=staff_id, inicio=when,
                     cliente={"nombre": nombre, "telefono": phone, "email": email}, consentimiento_datos=True, **kw)


async def _book(slug: str, body: BookingIn):
    c = await booking.resolve(slug)
    async with scoped_session(tenant_id=c.tenant_id) as db:
        return await booking.create_booking(db, c, body)


async def _call(slug: str, fn, *args):
    c = await booking.resolve(slug)
    async with scoped_session(tenant_id=c.tenant_id) as db:
        return await fn(db, c, *args)


async def _sql(sql: str, **prm):
    async with scoped_session(platform=True) as db:
        return (await db.execute(text(sql), prm)).all()


async def test_page_unavailable_looks_identical_in_every_case():
    p, slug = await _tenant()
    casos: list[str] = ["no-existe-" + uuid4().hex[:6]]                        # 1) dirección inexistente
    p_off, s_off = await _tenant()
    async with scoped_session(tenant_id=p_off.tenant_id) as db:                # 2) página desactivada
        await booking.put_admin_settings(db, p_off, SettingsIn(slug=s_off, activo=False))
    casos.append(s_off)
    p_sus, s_sus = await _tenant()
    async with scoped_session(platform=True) as db:                            # 3) negocio suspendido
        await db.execute(text("UPDATE tenants SET bloqueo_manual = true, estado = 'suspendida' WHERE id = :t"), {"t": p_sus.tenant_id})
    casos.append(s_sus)
    p_ind = await _setup("Independiente")                                      # 4) plan sin módulo de reservas
    async with scoped_session(platform=True) as db:
        await db.execute(text("INSERT INTO booking_settings (tenant_id, slug, activo) VALUES (:t, :s, true)"),
                         {"t": p_ind.tenant_id, "s": f"ind-{uuid4().hex[:8]}"})
        casos.append((await db.execute(text("SELECT slug FROM booking_settings WHERE tenant_id = :t"), {"t": p_ind.tenant_id})).scalar_one())
    msgs = set()
    for s in casos:
        with pytest.raises(AppError) as e:
            await booking.resolve(s)
        assert e.value.code == "NOT_FOUND" and e.value.status == 404
        msgs.add(e.value.message)
    assert len(msgs) == 1                                                      # mismo mensaje: no se filtra por qué no está disponible
    assert (await booking.resolve(slug)).tenant_id == p.tenant_id              # y la página sana sí responde


async def test_public_config_shows_only_what_is_public():
    p, slug = await _tenant()
    s1 = await _svc(p, "Corte", 30000)
    await _svc(p, "Oculto", 1000)
    async with scoped_session(tenant_id=p.tenant_id) as db:
        await db.execute(text("UPDATE services SET activo = false WHERE nombre = 'Oculto' AND tenant_id = :t"), {"t": p.tenant_id})
        await booking.create_field(db, p, FieldIn(etiqueta="¿Alergias?", tipo="texto", obligatorio=True))
    await _pro(p, "Ana", s1)
    cfg = await _call(slug, booking.public_config)
    nombres = {x.nombre for x in cfg.servicios}
    assert "Corte" in nombres and "Oculto" not in nombres
    assert cfg.escoger_profesional and [x.nombre_publico for x in cfg.profesionales] == ["Ana"] and cfg.campos[0].obligatorio
    assert cfg.consentimiento_texto and "1581" in cfg.consentimiento_texto and cfg.marca.nombre
    async with scoped_session(tenant_id=p.tenant_id) as db:
        await booking.put_admin_settings(db, p, SettingsIn(slug=slug, activo=True, modo_asignacion="automatica"))
    assert (await _call(slug, booking.public_config)).profesionales == []          # en modo automático no se muestra el equipo


async def test_booking_happy_path_creates_customer_appointment_notification_and_token():
    p, slug = await _tenant()
    sv = await _svc(p, "Corte", 30000)
    sid = await _pro(p, "Ana", sv)
    out = await _book(slug, _in(sv, staff_id=sid, phone="+57 300-123-4567", email="ana@correo.co"))
    assert out.estado == "pendiente" and out.profesional == "Ana" and out.total == 30000 and out.token
    assert out.puede_cancelar_hasta == out.inicio - timedelta(hours=3)
    cust = (await _sql("SELECT telefono, origen, consentimiento_datos, consentimiento_version, email FROM customers WHERE tenant_id = :t", t=p.tenant_id))[0]
    assert (cust.telefono, cust.origen, cust.consentimiento_datos, cust.consentimiento_version) == ("3001234567", "reserva_web", True, "v1")
    ap = (await _sql("SELECT origen, estado, created_by FROM appointments WHERE tenant_id = :t", t=p.tenant_id))[0]
    assert (ap.origen, ap.estado, ap.created_by) == ("reserva_web", "pendiente", None)
    n = (await _sql("SELECT canal, destinatario, plantilla FROM notifications WHERE tenant_id = :t", t=p.tenant_id))[0]
    assert (n.canal, n.destinatario, n.plantilla) == ("email", "ana@correo.co", "reserva_confirmada")
    a = (await _sql("SELECT actor_tipo, actor_id FROM audit_logs WHERE tenant_id = :t AND accion = 'cita_creada'", t=p.tenant_id))[0]
    assert (a.actor_tipo, a.actor_id) == ("system", None)
    view = await _call(slug, booking.view_booking, out.token)
    assert view.cliente == "Ana" and view.puede_modificar and view.estado == "pendiente"      # solo el primer nombre


async def test_whatsapp_confirmation_uses_e164_when_plan_has_whatsapp():
    p, slug = await _tenant("Profesional")
    sv = await _svc(p, "Corte", 30000)
    await _pro(p, "Ana", sv)
    await _book(slug, _in(sv))
    n = (await _sql("SELECT canal, destinatario FROM notifications WHERE tenant_id = :t", t=p.tenant_id))[0]
    assert (n.canal, n.destinatario) == ("whatsapp", "573001234567")


async def test_same_phone_reuses_customer_and_four_active_bookings_are_refused():
    p, slug = await _tenant()
    sv = await _svc(p, "Corte", 30000)
    await _pro(p, "Ana", sv)
    for i, fmt in enumerate(["3001234567", "+57 300 123 4567", "300-123-4567"]):
        await _book(slug, _in(sv, T0 + timedelta(hours=2 * i), phone=fmt, nombre=f"Ana {i}"))
    assert (await _sql("SELECT count(*) AS n FROM customers WHERE tenant_id = :t", t=p.tenant_id))[0].n == 1
    with pytest.raises(AppError) as e:
        await _book(slug, _in(sv, T0 + timedelta(hours=10), phone="3001234567"))
    assert e.value.code == "TOO_MANY_BOOKINGS" and e.value.status == 429
    await _book(slug, _in(sv, T0 + timedelta(hours=10), phone="3109998877", nombre="Otra Persona"))       # otro teléfono sí puede


async def test_strict_rules_apply_to_public_bookings():
    p, slug = await _tenant(anticipacion_min_horas=2, anticipacion_max_dias=10)
    sv = await _svc(p, "Corte", 30000)
    sid = await _pro(p, "Ana", sv)
    async with scoped_session(tenant_id=p.tenant_id) as db:                     # horario solo 9-12 los lunes
        await staff.set_schedule(db, p, sid, ScheduleIn(tramos=[ScheduleItem(dia_semana=0, hora_inicio=time(9), hora_fin=time(12))]))
    for when, why in [(datetime.now(UTC) + timedelta(minutes=30), "muy pronto"), (datetime.now(UTC) - timedelta(hours=1), "pasado"),
                      (datetime.now(UTC) + timedelta(days=30), "muy lejos"), (T0.replace(hour=12), "fuera de horario (cualquier día salvo lunes)")]:
        with pytest.raises(AppError) as e:
            await _book(slug, _in(sv, when, staff_id=sid))
        assert e.value.code == "SLOT_UNAVAILABLE", why


async def test_form_consent_honeypot_and_required_fields():
    p, slug = await _tenant()
    sv = await _svc(p, "Corte", 30000)
    await _pro(p, "Ana", sv)
    async with scoped_session(tenant_id=p.tenant_id) as db:
        f = await booking.create_field(db, p, FieldIn(etiqueta="¿Alergias?", tipo="texto", obligatorio=True))
    for body, key in [(_in(sv, website="http://spam.co"), "honeypot"), (_in(sv), "campo obligatorio vacío"),
                      (BookingIn(service_ids=[sv], inicio=T0, cliente={"nombre": "Ana Ruiz", "telefono": "3001234567"},
                                 consentimiento_datos=False, respuestas={f.id: "no"}), "sin consentimiento"),
                      (_in(None, respuestas={f.id: "no"}), "sin servicio")]:
        with pytest.raises(AppError) as e:
            await _book(slug, body)
        assert e.value.code == "VALIDATION_ERROR", key
    assert (await _sql("SELECT count(*) AS n FROM customers WHERE tenant_id = :t", t=p.tenant_id))[0].n == 0   # rechazar no deja basura
    out = await _book(slug, _in(sv, respuestas={f.id: "Ninguna"}))
    assert "¿Alergias?: Ninguna" in (await _sql("SELECT notas FROM appointments WHERE tenant_id = :t", t=p.tenant_id))[0].notas and out.token


async def test_parallel_bookings_for_the_same_slot_only_one_wins():
    p, slug = await _tenant()
    sv = await _svc(p, "Corte", 30000)
    sid = await _pro(p, "Ana", sv)
    res = await asyncio.gather(*[_book(slug, _in(sv, staff_id=sid, phone=f"31099900{i:02d}", nombre=f"Cliente {i}")) for i in range(6)],
                               return_exceptions=True)
    ok = [r for r in res if not isinstance(r, Exception)]
    assert len(ok) == 1, [repr(r)[:160] for r in res if isinstance(r, Exception)]
    assert all(isinstance(r, AppError) and r.code in ("SLOT_TAKEN", "SLOT_UNAVAILABLE") and r.status == 409 for r in res if isinstance(r, Exception))
    assert (await _sql("SELECT count(*) AS n FROM appointments WHERE tenant_id = :t", t=p.tenant_id))[0].n == 1


async def test_automatic_assignment_fills_each_professional_once():
    p, slug = await _tenant(modo_asignacion="automatica")
    sv = await _svc(p, "Corte", 30000)
    a, b = await _pro(p, "Ana", sv), await _pro(p, "Beto", sv)
    res = await asyncio.gather(*[_book(slug, _in(sv, staff_id=uuid4(), phone=f"31099911{i:02d}", nombre=f"Cliente {i}")) for i in range(4)],
                               return_exceptions=True)          # el staff_id que manda el cliente se ignora en modo automático
    ok = [r for r in res if not isinstance(r, Exception)]
    assert len(ok) == 2 and {r.profesional for r in ok} == {"Ana", "Beto"}
    assert a != b


async def test_reception_mode_leaves_staff_empty_and_respects_capacity():
    p, slug = await _tenant(modo_asignacion="en_recepcion")
    sv = await _svc(p, "Corte", 30000)
    await _pro(p, "Ana", sv)                                    # 1 profesional libre => capacidad 1
    first = await _book(slug, _in(sv, phone="3101110001"))
    assert first.profesional is None
    with pytest.raises(AppError) as e:                          # la cita sin profesional ya "consume" la capacidad
        await _book(slug, _in(sv, phone="3101110002", nombre="Otra Persona"))
    assert e.value.code in ("SLOT_UNAVAILABLE", "SLOT_TAKEN")


async def test_global_capacity_rule_limits_simultaneous_bookings():
    p, slug = await _tenant()
    sv = await _svc(p, "Corte", 30000)
    a, b = await _pro(p, "Ana", sv), await _pro(p, "Beto", sv)
    async with scoped_session(tenant_id=p.tenant_id) as db:
        await booking.create_rule(db, p, RuleIn(cupos_simultaneos=1))
        with pytest.raises(ValidationError):                    # reglas por profesional: todavía no
            RuleIn(staff_id=a)
    await _book(slug, _in(sv, staff_id=a, phone="3101110003"))
    with pytest.raises(AppError) as e:                          # B está libre, pero el negocio solo admite 1 a la vez
        await _book(slug, _in(sv, staff_id=b, phone="3101110004", nombre="Otra Persona"))
    assert e.value.code == "SLOT_UNAVAILABLE"


async def test_cancel_frees_the_slot_and_respects_the_window():
    p, slug = await _tenant(cancelacion_min_horas=3, anticipacion_min_horas=1)
    sv = await _svc(p, "Corte", 30000)
    sid = await _pro(p, "Ana", sv)
    out = await _book(slug, _in(sv, staff_id=sid))
    v = await _call(slug, booking.cancel_booking, out.token, CancelIn(motivo="me surgió algo"))
    assert v.estado == "cancelada" and not v.puede_modificar
    await _book(slug, _in(sv, staff_id=sid, phone="3101110005", nombre="Otra Persona"))        # el hueco quedó libre
    with pytest.raises(AppError) as e:
        await _call(slug, booking.cancel_booking, out.token, CancelIn())
    assert e.value.code == "INVALID_STATE_TRANSITION"
    cerca = datetime.now(UTC) + timedelta(hours=2)
    if cerca.astimezone(ZoneInfo("America/Bogota")).hour == 23:     # a las 23:xx el turno cruzaría la medianoche (horario 00:00-23:59)
        cerca = datetime.now(UTC) + timedelta(hours=1, minutes=15)                                          # sigue estando entre 1 h (anticipación) y 3 h (cancelación)
    soon = await _book(slug, _in(sv, cerca, staff_id=sid, phone="3101110006", nombre="Tercera Persona"))
    with pytest.raises(AppError) as e:                           # a 2 h de la cita no se puede (mínimo 3 h)
        await _call(slug, booking.cancel_booking, soon.token, CancelIn())
    assert e.value.code == "CANCELLATION_WINDOW_CLOSED" and e.value.status == 409
    assert (await _call(slug, booking.view_booking, soon.token)).puede_modificar is False


async def test_reschedule_issues_new_token_and_never_collides_with_itself():
    p, slug = await _tenant()
    sv = await _svc(p, "Corte", 30000)
    sid = await _pro(p, "Ana", sv)
    out = await _book(slug, _in(sv, staff_id=sid))
    other = await _book(slug, _in(sv, T0 + timedelta(hours=5), staff_id=sid, phone="3101110007", nombre="Otra Persona"))
    new = await _call(slug, booking.reschedule_booking, out.token, RescheduleIn(inicio=T0 + timedelta(minutes=15)))   # se solapa con SU propio hueco
    assert new.inicio == T0 + timedelta(minutes=15) and new.token != out.token
    with pytest.raises(AppError) as e:                           # el enlace viejo ya no vale
        await _call(slug, booking.view_booking, out.token)
    assert e.value.code == "UNAUTHORIZED"
    with pytest.raises(AppError) as e:                           # a un horario ocupado por otra reserva
        await _call(slug, booking.reschedule_booking, new.token, RescheduleIn(inicio=other.inicio))
    assert e.value.code in ("SLOT_UNAVAILABLE", "SLOT_TAKEN")
    assert (await _call(slug, booking.view_booking, new.token)).inicio == new.inicio           # sigue donde estaba
    again = await _call(slug, booking.reschedule_booking, new.token, RescheduleIn(inicio=T0 + timedelta(days=1)))
    assert again.inicio == T0 + timedelta(days=1)


async def test_tokens_do_not_cross_businesses_and_rls_isolates_public_sessions():
    pa, sa = await _tenant()
    pb, sb = await _tenant()
    sva, svb = await _svc(pa, "Corte", 30000), await _svc(pb, "Corte", 30000)
    await _pro(pa, "Ana", sva)
    await _pro(pb, "Beto", svb)
    out_a = await _book(sa, _in(sva))
    with pytest.raises(AppError) as e:                           # token de A usado en la dirección de B
        await _call(sb, booking.view_booking, out_a.token)
    assert e.value.code == "UNAUTHORIZED"
    with pytest.raises(AppError):
        await _call(sb, booking.cancel_booking, out_a.token, CancelIn())
    cb = await booking.resolve(sb)
    async with scoped_session(tenant_id=cb.tenant_id) as db:      # la sesión pública de B no ve nada de A
        assert (await db.execute(text("SELECT count(*) FROM appointments"))).scalar() == 0
        assert (await db.execute(text("SELECT count(*) FROM customers"))).scalar() == 0


async def test_admin_settings_defaults_slug_conflict_and_form_fields():
    p = await _setup("Básico")
    async with scoped_session(tenant_id=p.tenant_id) as db:
        d = await booking.get_admin_settings(db, p)
        assert d.configurado is False and d.activo is False and d.modo_asignacion == "cliente_elige"
    p2, slug2 = await _tenant()
    with pytest.raises(AppError) as e:
        async with scoped_session(tenant_id=p.tenant_id) as db:
            await booking.put_admin_settings(db, p, SettingsIn(slug=slug2))
    assert e.value.code == "CONFLICT"
    async with scoped_session(tenant_id=p.tenant_id) as db:
        s = await booking.put_admin_settings(db, p, SettingsIn(activo=True, color_primario="#112233", textos={"titulo": "Hola"}))
        assert s.configurado is True and s.slug and s.color_primario == "#112233"
        await booking.create_field(db, p, FieldIn(etiqueta="Pelo", tipo="seleccion", opciones=["Liso", "Rizado"]))
        assert [f.etiqueta for f in await booking.list_fields(db, p)] == ["Pelo"]
