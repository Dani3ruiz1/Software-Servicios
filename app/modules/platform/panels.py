"""Paneles de solo lectura del Super Admin: cobros, usuarios, auditoría y salud del sistema.

Cruzan todos los negocios con la sesión de plataforma (RLS de plataforma). Solo se exponen datos operativos: nunca contraseñas, secretos 2FA
ni credenciales de integraciones.
"""
import secrets
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.pagination import Page, PageParams
from app.core.security import hash_password
from app.core.tenancy import Principal
from app.modules.audit import service as audit

TZ = "America/Bogota"


# ------------------------------------------------------------------ cobros
class CobroOut(BaseModel):
    id: UUID
    tenant_id: UUID
    negocio: str
    monto: Decimal
    fecha_pago: date
    periodo_desde: date
    periodo_hasta: date
    metodo: str
    referencia: str | None


class CobrosResumen(BaseModel):
    mes_actual: Decimal
    mes_anterior: Decimal
    total_periodo: Decimal
    cantidad: int
    vencidos: int          # negocios con suscripción vencida (cartera por cobrar)


class CobrosOut(BaseModel):
    resumen: CobrosResumen
    pagos: Page[CobroOut]


async def cobros(db: AsyncSession, pp: PageParams, desde: date | None, hasta: date | None, metodo: str | None) -> CobrosOut:
    where = "(CAST(:desde AS date) IS NULL OR p.fecha_pago >= :desde) AND (CAST(:hasta AS date) IS NULL OR p.fecha_pago <= :hasta) AND (CAST(:metodo AS text) IS NULL OR p.metodo = :metodo)"
    prm: dict[str, Any] = {"desde": desde, "hasta": hasta, "metodo": metodo, "lim": pp.page_size, "off": pp.offset, "tz": TZ}
    base = f"FROM subscription_payments p JOIN tenants t ON t.id = p.tenant_id WHERE {where}"
    total, cantidad = (await db.execute(text(f"SELECT coalesce(sum(p.monto), 0), count(*) {base}"), prm)).one()  # noqa: S608
    filas = (await db.execute(text(
        f"SELECT p.id, p.tenant_id, t.nombre, p.monto, p.fecha_pago, p.periodo_desde, p.periodo_hasta, p.metodo, p.referencia {base} "  # noqa: S608
        "ORDER BY p.fecha_pago DESC, p.created_at DESC LIMIT :lim OFFSET :off"), prm)).all()
    mes, ant = (await db.execute(text(
        "SELECT coalesce(sum(monto) FILTER (WHERE date_trunc('month', fecha_pago) = date_trunc('month', (now() AT TIME ZONE :tz)::date)), 0), "
        "       coalesce(sum(monto) FILTER (WHERE date_trunc('month', fecha_pago) = date_trunc('month', (now() AT TIME ZONE :tz)::date) - interval '1 month'), 0) "
        "FROM subscription_payments"), {"tz": TZ})).one()
    vencidos = (await db.execute(text("SELECT count(*) FROM tenants WHERE deleted_at IS NULL AND estado = 'vencida'"))).scalar_one()
    items = [CobroOut(id=r[0], tenant_id=r[1], negocio=r[2], monto=r[3], fecha_pago=r[4], periodo_desde=r[5], periodo_hasta=r[6],
                      metodo=r[7], referencia=r[8]) for r in filas]
    return CobrosOut(resumen=CobrosResumen(mes_actual=mes, mes_anterior=ant, total_periodo=total, cantidad=cantidad, vencidos=vencidos),
                     pagos=Page(items=items, page=pp.page, page_size=pp.page_size, total=cantidad))


# ------------------------------------------------------------------ usuarios
class UsuarioOut(BaseModel):
    id: UUID
    tenant_id: UUID
    negocio: str
    nombre: str
    email: str
    rol: str
    activo: bool
    bloqueado: bool
    con_2fa: bool
    ultimo_login: datetime | None


async def usuarios(db: AsyncSession, pp: PageParams, q: str | None, rol: str | None, tenant_id: UUID | None) -> Page[UsuarioOut]:
    prm: dict[str, Any] = {"q": f"%{q.strip()}%" if q and q.strip() else None, "rol": rol, "tid": tenant_id, "lim": pp.page_size, "off": pp.offset}
    base = ("FROM users u JOIN tenants t ON t.id = u.tenant_id WHERE u.deleted_at IS NULL AND t.deleted_at IS NULL "
            "AND (CAST(:q AS text) IS NULL OR u.nombre ILIKE :q OR u.email::text ILIKE :q OR t.nombre ILIKE :q) "
            "AND (CAST(:rol AS text) IS NULL OR u.rol = :rol) AND (CAST(:tid AS uuid) IS NULL OR u.tenant_id = :tid)")
    total = (await db.execute(text(f"SELECT count(*) {base}"), prm)).scalar_one()  # noqa: S608
    filas = (await db.execute(text(
        f"SELECT u.id, u.tenant_id, t.nombre, u.nombre, u.email::text, u.rol, u.activo, coalesce(u.bloqueado_hasta > now(), false), "  # noqa: S608
        f"u.totp_habilitado, u.ultimo_login {base} ORDER BY t.nombre, u.rol, u.nombre LIMIT :lim OFFSET :off"), prm)).all()
    return Page(items=[UsuarioOut(id=r[0], tenant_id=r[1], negocio=r[2], nombre=r[3], email=r[4], rol=r[5], activo=r[6], bloqueado=r[7],
                                  con_2fa=r[8], ultimo_login=r[9]) for r in filas], page=pp.page, page_size=pp.page_size, total=total)


# ------------------------------------------------------------------ auditoría
class AuditoriaOut(BaseModel):
    id: UUID
    fecha: datetime
    actor_tipo: str
    negocio: str | None
    accion: str
    entidad: str
    ip: str | None


async def auditoria(db: AsyncSession, pp: PageParams, accion: str | None, tenant_id: UUID | None, actor_tipo: str | None) -> Page[AuditoriaOut]:
    prm: dict[str, Any] = {"accion": f"%{accion.strip()}%" if accion and accion.strip() else None, "tid": tenant_id, "actor": actor_tipo,
                           "lim": pp.page_size, "off": pp.offset}
    base = ("FROM audit_logs a LEFT JOIN tenants t ON t.id = a.tenant_id WHERE (CAST(:accion AS text) IS NULL OR a.accion ILIKE :accion) "
            "AND (CAST(:tid AS uuid) IS NULL OR a.tenant_id = :tid) AND (CAST(:actor AS text) IS NULL OR a.actor_tipo = :actor)")
    total = (await db.execute(text(f"SELECT count(*) {base}"), prm)).scalar_one()  # noqa: S608
    filas = (await db.execute(text(
        f"SELECT a.id, a.created_at, a.actor_tipo, t.nombre, a.accion, a.entidad, host(a.ip) {base} ORDER BY a.created_at DESC LIMIT :lim OFFSET :off"),  # noqa: S608
        prm)).all()
    return Page(items=[AuditoriaOut(id=r[0], fecha=r[1], actor_tipo=r[2], negocio=r[3], accion=r[4], entidad=r[5], ip=r[6]) for r in filas],
                page=pp.page, page_size=pp.page_size, total=total)


# ------------------------------------------------------------------ salud del sistema
class Conteo(BaseModel):
    clave: str
    n: int


class NegocioConFallos(BaseModel):
    tenant_id: UUID
    negocio: str
    fallidas: int


class SaludOut(BaseModel):
    notificaciones_7d: list[Conteo]          # por estado
    notificaciones_pendientes_viejas: int    # pendientes con más de 1 h de retraso: la cola se está atascando
    negocios_con_fallos: list[NegocioConFallos]
    integraciones: list[Conteo]              # "tipo · estado"
    usuarios_bloqueados: int
    usuarios_sin_2fa_admin: int
    negocios_sin_admin_activo: int
    suscripciones_vencidas: int
    base_de_datos_mb: float


async def salud(db: AsyncSession) -> SaludOut:
    notif = [Conteo(clave=r[0], n=r[1]) for r in (await db.execute(text(
        "SELECT estado, count(*) FROM notifications WHERE created_at >= now() - interval '7 days' GROUP BY 1 ORDER BY 2 DESC"))).all()]
    viejas = (await db.execute(text(
        "SELECT count(*) FROM notifications WHERE estado = 'pendiente' AND programada_para < now() - interval '1 hour'"))).scalar_one()
    fallos = [NegocioConFallos(tenant_id=r[0], negocio=r[1], fallidas=r[2]) for r in (await db.execute(text(
        "SELECT t.id, t.nombre, count(*) FROM notifications n JOIN tenants t ON t.id = n.tenant_id "
        "WHERE n.estado = 'fallida' AND n.created_at >= now() - interval '7 days' AND t.deleted_at IS NULL GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 8"))).all()]
    integ = [Conteo(clave=f"{r[0]} · {r[1]}", n=r[2]) for r in (await db.execute(text(
        "SELECT i.tipo, i.estado, count(*) FROM integrations i JOIN tenants t ON t.id = i.tenant_id WHERE t.deleted_at IS NULL GROUP BY 1, 2 ORDER BY 1, 2"))).all()]
    bloq = (await db.execute(text(
        "SELECT count(*) FROM users u JOIN tenants t ON t.id = u.tenant_id WHERE u.deleted_at IS NULL AND t.deleted_at IS NULL AND u.bloqueado_hasta > now()"))).scalar_one()
    sin2fa = (await db.execute(text(
        "SELECT count(*) FROM users u JOIN tenants t ON t.id = u.tenant_id WHERE u.deleted_at IS NULL AND t.deleted_at IS NULL AND u.activo "
        "AND u.rol = 'admin' AND NOT u.totp_habilitado"))).scalar_one()
    sin_admin = (await db.execute(text(
        "SELECT count(*) FROM tenants t WHERE t.deleted_at IS NULL AND NOT EXISTS "
        "(SELECT 1 FROM users u WHERE u.tenant_id = t.id AND u.rol = 'admin' AND u.activo AND u.deleted_at IS NULL)"))).scalar_one()
    venc = (await db.execute(text("SELECT count(*) FROM tenants WHERE deleted_at IS NULL AND estado = 'vencida'"))).scalar_one()
    mb = (await db.execute(text("SELECT pg_database_size(current_database()) / 1048576.0"))).scalar_one()
    return SaludOut(notificaciones_7d=notif, notificaciones_pendientes_viejas=viejas, negocios_con_fallos=fallos, integraciones=integ,
                    usuarios_bloqueados=bloq, usuarios_sin_2fa_admin=sin2fa, negocios_sin_admin_activo=sin_admin, suscripciones_vencidas=venc,
                    base_de_datos_mb=round(float(mb), 1))


# ------------------------------------------------------------------ acciones sobre usuarios (siempre auditadas)
class ClaveTemporalOut(BaseModel):
    clave_temporal: str      # se muestra UNA vez; no se guarda en claro ni en la bitácora


async def _usuario(db: AsyncSession, uid: UUID) -> tuple[UUID, str, bool]:
    r = (await db.execute(text("SELECT tenant_id, email::text, activo FROM users WHERE id = :u AND deleted_at IS NULL"), {"u": uid})).first()
    if not r:
        raise AppError("NOT_FOUND", "El usuario no existe")
    return r[0], r[1], r[2]


async def _cerrar_sesiones(db: AsyncSession, uid: UUID) -> None:
    await db.execute(text("UPDATE refresh_tokens SET revocado_en = now() WHERE sujeto_tipo = 'user' AND sujeto_id = :u AND revocado_en IS NULL"), {"u": uid})


def _aud(db: AsyncSession, p: Principal, accion: str, uid: UUID, tid: UUID, email: str, ip: str | None, extra: dict[str, Any] | None = None) -> None:
    audit.record(db, actor_tipo="platform_user", actor_id=p.id, tenant_id=tid, accion=accion, entidad="user", entidad_id=uid, despues={"email": email, **(extra or {})}, ip=ip)


async def desbloquear(db: AsyncSession, p: Principal, uid: UUID, ip: str | None) -> None:
    tid, email, _ = await _usuario(db, uid)
    await db.execute(text("UPDATE users SET bloqueado_hasta = NULL, intentos_fallidos = 0, updated_at = now() WHERE id = :u"), {"u": uid})
    _aud(db, p, "usuario_desbloqueado", uid, tid, email, ip)


async def fijar_activo(db: AsyncSession, p: Principal, uid: UUID, activo: bool, ip: str | None) -> None:
    tid, email, _ = await _usuario(db, uid)
    await db.execute(text("UPDATE users SET activo = :a, updated_at = now() WHERE id = :u"), {"a": activo, "u": uid})
    if not activo:
        await _cerrar_sesiones(db, uid)
    _aud(db, p, "usuario_activado" if activo else "usuario_desactivado", uid, tid, email, ip)


async def restablecer_clave(db: AsyncSession, p: Principal, uid: UUID, ip: str | None) -> ClaveTemporalOut:
    tid, email, _ = await _usuario(db, uid)
    clave = secrets.token_urlsafe(12)                         # 16 caracteres; cumple el mínimo de 10
    await db.execute(text("UPDATE users SET password_hash = :h, intentos_fallidos = 0, bloqueado_hasta = NULL, updated_at = now() WHERE id = :u"),
                     {"h": hash_password(clave), "u": uid})
    await _cerrar_sesiones(db, uid)
    _aud(db, p, "usuario_clave_restablecida", uid, tid, email, ip)       # nunca se registra la clave
    return ClaveTemporalOut(clave_temporal=clave)
