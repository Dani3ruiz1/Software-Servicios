"""Acceso propio del barbero (solo ve y mueve SU agenda) y analíticas de toda la plataforma (solo el Super Admin, solo agregados)."""
import httpx
from test_phase5_flow import _svc
from test_phase6_flow import _pro
from test_phase9_flow import PASSWORD, _needs_db, _sql, _user  # noqa: F401  (_needs_db: omite si no hay Postgres)

from app.core.startup import DEV_ADMIN_EMAIL, DEV_ADMIN_PASSWORD
from app.main import app


def cliente() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def entrar(c: httpx.AsyncClient, slug: str, email: str, password: str = PASSWORD) -> dict[str, str]:
    r = await c.post("/api/v1/auth/login", json={"tenant_slug": slug, "email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def cita(tid, sid, cid, minutos_desde_ahora: int, estado="pendiente", origen="recepcion"):
    """Inserta una cita de 30 min (por SQL, para poder fijar el pasado) y devuelve su id."""
    r = await _sql("INSERT INTO appointments (tenant_id, customer_id, staff_id, inicio, fin, estado, origen) VALUES (:t, :c, :s, "
                   "now() + make_interval(mins => :m), now() + make_interval(mins => :m + 30), :e, :o) RETURNING id",
                   t=tid, c=cid, s=sid, m=minutos_desde_ahora, e=estado, o=origen)
    return r[0].id


async def cliente_bd(tid, nombre="Cliente Prueba"):
    return (await _sql("INSERT INTO customers (tenant_id, nombre, telefono) VALUES (:t, :n, '3001112233') RETURNING id", t=tid, n=nombre))[0].id


# ================================================================== acceso del barbero
async def test_barber_sees_only_his_agenda_and_access_can_be_revoked():
    p, slug, email_admin = await _user("Profesional")
    sv = await _svc(p, "Corte", 30000)
    andres, camila = await _pro(p, "Andrés", sv), await _pro(p, "Camila", sv)
    cid = await cliente_bd(p.tenant_id)
    suya1, suya2 = await cita(p.tenant_id, andres, cid, -300), await cita(p.tenant_id, andres, cid, -200)
    ajena = await cita(p.tenant_id, camila, cid, -100)
    async with cliente() as c:
        admin = await entrar(c, slug, email_admin)
        r = await c.post(f"/api/v1/staff/{andres}/access", headers=admin, json={"email": "andres@barberia.co", "password": PASSWORD})
        assert r.status_code == 201 and r.json()["email"] == "andres@barberia.co"
        assert (await c.post(f"/api/v1/staff/{andres}/access", headers=admin, json={"email": "otro@barberia.co", "password": PASSWORD})).json()["code"] == "CONFLICT"   # ya tiene acceso
        assert (await c.post(f"/api/v1/staff/{camila}/access", headers=admin, json={"email": "andres@barberia.co", "password": PASSWORD})).json()["code"] == "CONFLICT"   # correo repetido
        assert (await c.post(f"/api/v1/staff/{camila}/access", headers=admin, json={"email": "mala", "password": "corta"})).status_code == 422

        barbero = await entrar(c, slug, "andres@barberia.co")
        me = (await c.get("/api/v1/auth/me", headers=barbero)).json()
        assert me["rol"] == "profesional" and "appointments:read_own" in me["permisos"] and "users:manage" not in me["permisos"]
        lista = (await c.get("/api/v1/appointments", headers=barbero)).json()
        assert {x["id"] for x in lista["items"]} == {str(suya1), str(suya2)}                       # nada de lo de Camila
        assert (await c.get(f"/api/v1/appointments/{ajena}", headers=barbero)).status_code == 404   # ni por id: no revela que existe
        r = await c.post(f"/api/v1/appointments/{suya1}/status", headers=barbero, json={"estado": "en_atencion"})
        assert r.status_code == 200 and r.json()["estado"] == "en_atencion"
        assert (await c.post(f"/api/v1/appointments/{ajena}/status", headers=barbero, json={"estado": "en_atencion"})).status_code == 404
        assert (await c.post(f"/api/v1/appointments/{suya2}/status", headers=barbero, json={"estado": "cancelada"})).status_code == 403   # cancelar no es cosa del barbero
        for ruta in ("/api/v1/users", "/api/v1/staff/" + str(camila) + "/access"):                    # y no administra nada
            assert (await c.get(ruta, headers=barbero)).status_code in (403, 405)
        assert (await c.post(f"/api/v1/staff/{camila}/access", headers=barbero, json={"email": "x@y.co", "password": PASSWORD})).status_code == 403

        assert (await c.delete(f"/api/v1/staff/{andres}/access", headers=admin)).status_code == 204
        r = await c.post("/api/v1/auth/login", json={"tenant_slug": slug, "email": "andres@barberia.co", "password": PASSWORD})
        assert r.status_code == 401                                                                   # ya no entra
        assert (await c.delete(f"/api/v1/staff/{andres}/access", headers=admin)).status_code == 404   # no hay nada que quitar
        assert (await c.post(f"/api/v1/staff/{andres}/access", headers=admin, json={"email": "andres@barberia.co", "password": PASSWORD})).status_code == 201   # se puede volver a dar (el correo quedó libre)
    quedan = await _sql("SELECT count(*) AS n FROM appointments WHERE staff_id = :s", s=andres)
    assert quedan[0].n == 2                                                                           # quitar el acceso no borra las citas


async def test_granting_access_respects_the_plan_user_limit():
    p, slug, email_admin = await _user("Independiente")                # 1 usuario: ya lo usa el administrador
    sv = await _svc(p, "Corte", 30000)
    sid = await _pro(p, "Pro", sv)
    async with cliente() as c:
        admin = await entrar(c, slug, email_admin)
        r = await c.post(f"/api/v1/staff/{sid}/access", headers=admin, json={"email": "pro@barberia.co", "password": PASSWORD})
        assert r.status_code == 409 and r.json()["code"] == "USER_LIMIT_REACHED"


# ================================================================== analíticas de plataforma
_TOKEN_SA: dict[str, str] = {}      # el login de plataforma admite 5 por minuto por IP: se inicia sesión una sola vez y se reutiliza (dura 15 min)


async def token_plataforma(c: httpx.AsyncClient) -> dict[str, str]:
    if not _TOKEN_SA:
        r = await c.post("/api/v1/auth/platform/login", json={"email": DEV_ADMIN_EMAIL, "password": DEV_ADMIN_PASSWORD})
        assert r.status_code == 200, r.text
        _TOKEN_SA["Authorization"] = f"Bearer {r.json()['access_token']}"
    return dict(_TOKEN_SA)


async def test_analytics_are_super_admin_only_and_validate_the_window():
    _, slug, email = await _user("Profesional")
    async with cliente() as c:
        assert (await c.get("/api/v1/platform/analytics")).status_code == 401
        negocio = await entrar(c, slug, email)
        assert (await c.get("/api/v1/platform/analytics", headers=negocio)).status_code == 403           # un negocio no ve a los demás
        sa = await token_plataforma(c)
        for dias in (3, 400):
            assert (await c.get("/api/v1/platform/analytics", params={"dias": dias}, headers=sa)).status_code == 422
        r = await c.get("/api/v1/platform/analytics", params={"dias": 14}, headers=sa)
        assert r.status_code == 200
        d = r.json()
        assert d["dias"] == 14 and len(d["serie"]) == 14 and d["serie"][-1]["fecha"] == d["hasta"] and d["zona_horaria"] == "America/Bogota"
        assert all(0 <= x["dia"] <= 6 and 0 <= x["hora"] <= 23 for x in d["mapa_calor"])
        assert d["resumen"]["mrr"] == (await c.get("/api/v1/platform/dashboard", headers=sa)).json()["mrr"]   # coincide con el resumen clásico
        assert set(d["citas_por_estado"]) == {"pendiente", "confirmada", "en_atencion", "atendida", "cancelada", "no_asistio"}
        texto = r.text.lower()
        assert "telefono" not in texto and "email" not in texto and "documento" not in texto               # solo agregados: ningún dato personal


async def test_analytics_numbers_match_what_actually_happened():
    p, slug, _ = await _user("Profesional")
    sv = await _svc(p, "Corte", 30000)
    sid = await _pro(p, "Pro", sv)
    cid = await cliente_bd(p.tenant_id)
    ids = [await cita(p.tenant_id, sid, cid, -60 * (2 + 2 * i), "atendida", "reserva_web" if i < 4 else "whatsapp") for i in range(6)]   # 6 atendidas, 2 horas aparte
    await cita(p.tenant_id, sid, cid, -60 * 20, "no_asistio")
    await cita(p.tenant_id, sid, cid, -60 * 22, "no_asistio")
    await cita(p.tenant_id, sid, cid, -60 * 24, "cancelada")
    for i, aid in enumerate(ids):                                      # cada atendida se cobró: 30.000 (+ propina en las dos primeras)
        propina = 5000 if i < 2 else 0
        v = (await _sql("INSERT INTO sales (tenant_id, customer_id, staff_id, appointment_id, subtotal, propina, total, estado, pagada_en) "
                        "VALUES (:t, :c, :s, :a, 30000, CAST(:p AS numeric), 30000 + CAST(:p AS numeric), 'pagada', now()) RETURNING id", t=p.tenant_id, c=cid, s=sid, a=aid, p=propina))[0].id
        await _sql("INSERT INTO payments (tenant_id, sale_id, metodo, monto) VALUES (:t, :v, 'efectivo', 30000 + CAST(:p AS numeric))", t=p.tenant_id, v=v, p=propina)
    async with cliente() as c:
        sa = await token_plataforma(c)
        d = (await c.get("/api/v1/platform/analytics", params={"dias": 30, "top": 200}, headers=sa)).json()   # la base de pruebas acumula negocios: ranking amplio para no depender del top 8
    fila = next(x for x in d["top_negocios"] if x["slug"] == slug)
    assert (fila["citas"], fila["atendidas"], fila["no_asistio"]) == (8, 6, 2)                          # la cancelada no cuenta como cita
    assert fila["ventas"] == "190000.00"                                                                # 6 × 30.000 + 2 propinas de 5.000
    assert d["resumen"]["citas_atendidas"] >= 6 and d["resumen"]["ventas_cantidad"] >= 6
    assert sum(x["atendidas"] for x in d["serie"]) == d["citas_por_estado"]["atendida"]                 # la serie diaria suma lo mismo que el total
    assert {o["origen"] for o in d["por_origen"]} >= {"reserva_web", "whatsapp", "recepcion"}
    assert sum(o["reservas"] for o in d["por_origen"]) == d["resumen"]["reservas_creadas"]
    assert d["crecimiento"][-1]["acumulado"] == d["resumen"]["negocios_total"]                          # el último mes acumula todos los negocios
    assert sum(x["negocios"] for x in d["por_plan"]) <= d["resumen"]["negocios_total"]


async def test_super_admin_panels_are_platform_only_and_never_leak_secrets():
    _, slug, email = await _user("Profesional")
    rutas = ("cobros", "usuarios", "auditoria", "salud")
    async with cliente() as c:
        negocio = await entrar(c, slug, email)
        for ruta in rutas:
            assert (await c.get(f"/api/v1/platform/{ruta}")).status_code == 401
            assert (await c.get(f"/api/v1/platform/{ruta}", headers=negocio)).status_code == 403
        sa = await token_plataforma(c)
        u = (await c.get("/api/v1/platform/usuarios", params={"q": email}, headers=sa)).json()
        assert u["total"] >= 1 and u["items"][0]["email"] == email and u["items"][0]["rol"] == "admin"
        assert (await c.get("/api/v1/platform/usuarios", params={"rol": "otro"}, headers=sa)).status_code == 422
        assert "password" not in str(u).lower() and "totp_secret" not in str(u)
        cb = (await c.get("/api/v1/platform/cobros", headers=sa)).json()
        assert set(cb["resumen"]) == {"mes_actual", "mes_anterior", "total_periodo", "cantidad", "vencidos"} and cb["pagos"]["page"] == 1
        au = (await c.get("/api/v1/platform/auditoria", params={"accion": "login"}, headers=sa)).json()
        assert all("login" in x["accion"] for x in au["items"]) and "antes" not in str(au) and "despues" not in str(au)
        s = (await c.get("/api/v1/platform/salud", headers=sa)).json()
        assert s["base_de_datos_mb"] > 0 and s["usuarios_bloqueados"] >= 0 and "credenciales" not in str(s).lower()


async def test_super_admin_user_actions_work_and_are_audited_without_the_password():
    p, slug, email = await _user("Profesional")
    uid = (await _sql("SELECT id FROM users WHERE email = :e AND tenant_id = :t", e=email, t=p.tenant_id))[0].id
    base = f"/api/v1/platform/usuarios/{uid}"
    login = {"tenant_slug": slug, "email": email, "password": PASSWORD}
    async with cliente() as c:
        negocio = await entrar(c, slug, email)
        assert (await c.post(f"{base}/desbloquear", headers=negocio)).status_code == 403
        sa = await token_plataforma(c)
        await _sql("UPDATE users SET bloqueado_hasta = now() + interval '1 hour', intentos_fallidos = 5 WHERE id = :u", u=uid)
        assert (await c.post(f"{base}/desbloquear", headers=sa)).status_code == 204
        u = (await _sql("SELECT bloqueado_hasta, intentos_fallidos, activo FROM users WHERE id = :u", u=uid))[0]
        assert u.bloqueado_hasta is None and u.intentos_fallidos == 0
        assert (await c.put(f"{base}/activo", json={"activo": False}, headers=sa)).status_code == 204
        assert (await _sql("SELECT activo FROM users WHERE id = :u", u=uid))[0].activo is False
        assert (await _sql("SELECT count(*) n FROM refresh_tokens WHERE sujeto_id = :u AND revocado_en IS NULL", u=uid))[0].n == 0   # sesiones cerradas
        assert (await c.put(f"{base}/activo", json={"activo": True}, headers=sa)).status_code == 204
        r = await c.post(f"{base}/restablecer-clave", headers=sa)
        nueva = r.json()["clave_temporal"]
        assert len(nueva) >= 10 and (await c.post("/api/v1/auth/login", json=login)).status_code != 200   # la clave vieja ya no sirve
        assert (await c.post("/api/v1/auth/login", json={**login, "password": nueva})).status_code == 200
        assert (await c.post("/api/v1/platform/usuarios/00000000-0000-0000-0000-000000000000/desbloquear", headers=sa)).status_code == 404
        log = (await c.get("/api/v1/platform/auditoria", params={"accion": "usuario_", "tenant_id": str(p.tenant_id)}, headers=sa)).json()
        assert {"usuario_desbloqueado", "usuario_desactivado", "usuario_activado", "usuario_clave_restablecida"} <= {x["accion"] for x in log["items"]}
        assert nueva not in str(log)
        assert nueva not in str(await _sql("SELECT despues FROM audit_logs WHERE tenant_id = :t", t=p.tenant_id))


async def test_booking_only_reports_rank_clients_without_any_money():
    from datetime import date, timedelta
    p, slug, email = await _user("Profesional")
    sv = await _svc(p, "Corte", 30000)
    andres = await _pro(p, "Andrés", sv)
    ana, beto = await cliente_bd(p.tenant_id, "Ana"), await cliente_bd(p.tenant_id, "Beto")
    for m in (-4000, -3000, -2000):
        await cita(p.tenant_id, andres, ana, m, estado="atendida")
    await cita(p.tenant_id, andres, ana, -1000, estado="cancelada")
    await cita(p.tenant_id, andres, beto, -500, estado="atendida")
    await cita(p.tenant_id, andres, beto, -100, estado="no_asistio")
    q = {"desde": str(date.today() - timedelta(days=10)), "hasta": str(date.today() + timedelta(days=1))}
    async with cliente() as c:
        h = await entrar(c, slug, email)
        rk = (await c.get("/api/v1/reports/ranking-clientes", params=q, headers=h)).json()
        assert [f["cliente"] for f in rk["filas"]] == ["Ana", "Beto"]
        ana_f, beto_f = rk["filas"]
        assert (ana_f["reservas"], ana_f["atendidas"], ana_f["canceladas"]) == (4, 3, 1) and (beto_f["reservas"], beto_f["no_asistio"]) == (2, 1)
        cr = (await c.get("/api/v1/reports/clientes-reservas", params=q, headers=h)).json()
        assert cr["totales"]["total"] == 2 and cr["totales"]["nuevos"] == 2          # la cancelada no cuenta como visita
        rp = (await c.get("/api/v1/reports/reservas-profesional", params=q, headers=h)).json()
        assert rp["filas"][0]["profesional"] == "Andrés" and rp["filas"][0]["reservas"] == 6 and rp["filas"][0]["clientes"] == 2
        assert (await c.get("/api/v1/reports/reservas-servicio", params=q, headers=h)).status_code == 200
        for r in (rk, cr, rp):
            assert all(col["tipo"] != "dinero" for col in r["columnas"])                # ninguna cifra de dinero
