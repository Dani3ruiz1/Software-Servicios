"""Caja: sesiones, movimientos y cuadre. NO importa de ventas (ventas importa de aquí)."""
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.pagination import Page, PageParams
from app.core.repository import get_or_404, paginate
from app.core.tenancy import Principal
from app.modules.audit import service as audit
from app.modules.cash.models import CashMovement, CashSession
from app.modules.cash.schemas import (
    CloseIn,
    Cuadre,
    MethodTotal,
    MovementIn,
    MovementOut,
    OpenIn,
    SessionOut,
)

ZERO = Decimal("0")


def expected_cash(inicial: Decimal, ventas_efectivo: Decimal, ingresos: Decimal, egresos: Decimal) -> Decimal:
    """Efectivo que debería haber: base + cobros en efectivo + ingresos − egresos.
    Datáfono y transferencias no pasan por el cajón, por eso no cuentan."""
    return inicial + ventas_efectivo + ingresos - egresos


def _aud(db: AsyncSession, p: Principal, accion: str, sid: UUID, antes: dict | None = None,  # type: ignore[type-arg]
         despues: dict | None = None) -> None:  # type: ignore[type-arg]
    audit.record(db, actor_tipo="user", actor_id=p.id, tenant_id=p.tid, accion=accion, entidad="cash_session",
                 entidad_id=sid, antes=antes, despues=despues)


async def cash_required(db: AsyncSession, tenant_id: UUID) -> bool:
    """Regla 9: se exige caja abierta si el negocio lo configuró Y tiene el módulo de caja.
    (El plan independiente no tiene caja: vende sin sesión.)"""
    return bool((await db.execute(text(
        "SELECT t.requiere_sesion_caja AND EXISTS (SELECT 1 FROM tenant_modules tm JOIN modules m ON m.id = tm.module_id "
        "WHERE tm.tenant_id = t.id AND m.codigo = 'cash' AND tm.activo) FROM tenants t WHERE t.id = :t"),
        {"t": tenant_id})).scalar())


async def get_open_session(db: AsyncSession, tenant_id: UUID, *, lock: str | None = None) -> CashSession | None:
    q = select(CashSession).where(CashSession.tenant_id == tenant_id, CashSession.estado == "abierta")
    if lock == "share":
        q = q.with_for_update(read=True)       # cobrar: comparte; cerrar: exclusivo => no se cobra en una caja que se cierra
    elif lock == "update":
        q = q.with_for_update()
    return (await db.execute(q)).scalar_one_or_none()


async def require_open_session(db: AsyncSession, tenant_id: UUID) -> CashSession | None:
    """Devuelve la sesión abierta (con candado compartido) o None si no se exige; error si se exige y no hay."""
    s = await get_open_session(db, tenant_id, lock="share")
    if s is None and await cash_required(db, tenant_id):
        raise AppError("CASH_SESSION_REQUIRED", "Abre la caja antes de cobrar")
    return s


async def open_session(db: AsyncSession, p: Principal, d: OpenIn) -> SessionOut:
    if await get_open_session(db, p.tid):
        raise AppError("CASH_SESSION_ALREADY_OPEN", "Ya hay una caja abierta")
    s = CashSession(tenant_id=p.tid, responsable_id=p.id, monto_inicial=d.monto_inicial, notas=d.notas)
    db.add(s)
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError as e:                       # dos aperturas simultáneas: gana una (índice único parcial)
        raise AppError("CASH_SESSION_ALREADY_OPEN", "Ya hay una caja abierta") from e
    _aud(db, p, "caja_abierta", s.id, despues={"monto_inicial": str(d.monto_inicial)})
    return SessionOut.model_validate(s)


async def current(db: AsyncSession, p: Principal) -> SessionOut | None:
    s = await get_open_session(db, p.tid)
    return SessionOut.model_validate(s) if s else None


async def list_sessions(db: AsyncSession, p: Principal, pp: PageParams) -> Page[SessionOut]:
    rows, total = await paginate(db, select(CashSession).where(CashSession.tenant_id == p.tid), pp,
                                 {"abierta_en": CashSession.abierta_en}, "-abierta_en")
    return Page[SessionOut](items=[SessionOut.model_validate(r) for r in rows], page=pp.page, page_size=pp.page_size, total=total)


async def add_movement(db: AsyncSession, p: Principal, d: MovementIn) -> MovementOut:
    s = await get_open_session(db, p.tid, lock="share")
    if s is None:
        raise AppError("CASH_SESSION_REQUIRED", "Abre la caja para registrar movimientos")
    m = CashMovement(tenant_id=p.tid, cash_session_id=s.id, created_by=p.id, **d.model_dump())
    db.add(m)
    await db.flush()
    _aud(db, p, "caja_movimiento", s.id, despues={"tipo": d.tipo, "monto": str(d.monto), "categoria": d.categoria})
    return MovementOut.model_validate(m)


async def _totals(db: AsyncSession, tenant_id: UUID, sid: UUID) -> dict:  # type: ignore[type-arg]
    prm = {"t": tenant_id, "c": sid}
    metodos = [MethodTotal(metodo=r.metodo, monto=r.monto, pagos=r.n) for r in (await db.execute(text(
        "SELECT pg.metodo, sum(pg.monto) AS monto, count(*) AS n FROM payments pg JOIN sales s "
        "ON s.tenant_id = pg.tenant_id AND s.id = pg.sale_id "
        "WHERE s.tenant_id = :t AND s.cash_session_id = :c AND s.estado = 'pagada' GROUP BY pg.metodo ORDER BY pg.metodo"), prm))]
    v = (await db.execute(text(
        "SELECT count(*) AS n, coalesce(sum(total), 0) AS total, coalesce(sum(propina), 0) AS propina FROM sales "
        "WHERE tenant_id = :t AND cash_session_id = :c AND estado = 'pagada'"), prm)).one()
    mov = {r.tipo: r.monto for r in (await db.execute(text(
        "SELECT tipo, sum(monto) AS monto FROM cash_movements WHERE tenant_id = :t AND cash_session_id = :c GROUP BY tipo"), prm))}
    return {"metodos": metodos, "ventas": v, "ingresos": mov.get("ingreso", ZERO), "egresos": mov.get("egreso", ZERO)}


async def cuadre(db: AsyncSession, p: Principal, sid: UUID) -> Cuadre:
    s = await get_or_404(db, CashSession, sid, p.tid, "Sesión de caja")
    t = await _totals(db, p.tid, sid)
    efectivo = next((m.monto for m in t["metodos"] if m.metodo == "efectivo"), ZERO)
    esperado = s.monto_esperado if s.estado == "cerrada" and s.monto_esperado is not None \
        else expected_cash(s.monto_inicial, efectivo, t["ingresos"], t["egresos"])
    movs = (await db.execute(select(CashMovement).where(CashMovement.tenant_id == p.tid, CashMovement.cash_session_id == sid)
                             .order_by(CashMovement.created_at))).scalars()
    return Cuadre(
        sesion=SessionOut.model_validate(s), monto_inicial=s.monto_inicial, ventas_efectivo=efectivo, ingresos=t["ingresos"],
        egresos=t["egresos"], esperado_efectivo=esperado, contado=s.monto_contado,
        diferencia=s.diferencia if s.estado == "cerrada" else None, por_metodo=t["metodos"], ventas_pagadas=t["ventas"].n,
        total_vendido=t["ventas"].total, propinas=t["ventas"].propina, movimientos=[MovementOut.model_validate(m) for m in movs])


async def close_session(db: AsyncSession, p: Principal, sid: UUID, d: CloseIn) -> Cuadre:
    s = (await db.execute(select(CashSession).where(CashSession.tenant_id == p.tid, CashSession.id == sid)
                          .with_for_update())).scalar_one_or_none()
    if s is None:
        raise AppError("NOT_FOUND", "Sesión de caja no encontrada")
    if s.estado != "abierta":
        raise AppError("CONFLICT", "Esa caja ya está cerrada")
    t = await _totals(db, p.tid, sid)
    efectivo = next((m.monto for m in t["metodos"] if m.metodo == "efectivo"), ZERO)
    s.monto_esperado = expected_cash(s.monto_inicial, efectivo, t["ingresos"], t["egresos"])
    s.monto_contado = d.monto_contado
    s.diferencia = d.monto_contado - s.monto_esperado          # negativo = faltante, positivo = sobrante
    s.estado, s.cerrada_en = "cerrada", datetime.now(UTC)
    if d.notas:
        s.notas = f"{s.notas}\n{d.notas}" if s.notas else d.notas
    await db.flush()
    _aud(db, p, "caja_cerrada", s.id, despues={"esperado": str(s.monto_esperado), "contado": str(d.monto_contado),
                                                "diferencia": str(s.diferencia)})
    return await cuadre(db, p, sid)
