from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.pagination import Page, PageParams
from app.core.repository import get_or_404, paginate, scoped
from app.core.tenancy import Principal
from app.modules.audit import service as audit
from app.modules.catalog.models import Service, ServiceCategory
from app.modules.catalog.schemas import (
    CategoryIn,
    CategoryOut,
    CategoryPatch,
    ServiceIn,
    ServiceOut,
    ServicePatch,
)

DEFAULT_IVA = Decimal("19")


def _aud(db: AsyncSession, p: Principal, accion: str, ent: str, eid: UUID, antes: dict | None = None,  # type: ignore[type-arg]
         despues: dict | None = None) -> None:  # type: ignore[type-arg]
    audit.record(db, actor_tipo="user", actor_id=p.id, tenant_id=p.tid, accion=accion, entidad=ent,
                 entidad_id=eid, antes=antes, despues=despues)


def normalize_iva(aplica: bool, pct: Decimal | None) -> Decimal:
    """Regla: sin IVA => 0 %; con IVA y sin porcentaje => 19 % (Colombia)."""
    if not aplica:
        return Decimal("0")
    return DEFAULT_IVA if pct is None else pct


# ---- categorías
async def list_categories(db: AsyncSession, p: Principal) -> list[CategoryOut]:
    rows = (await db.execute(scoped(ServiceCategory, p.tid).order_by(ServiceCategory.orden, ServiceCategory.nombre))).scalars()
    return [CategoryOut.model_validate(r) for r in rows]


async def create_category(db: AsyncSession, p: Principal, d: CategoryIn) -> CategoryOut:
    dup = (await db.execute(scoped(ServiceCategory, p.tid).where(
        func.lower(ServiceCategory.nombre) == d.nombre.lower()))).first()
    if dup:
        raise AppError("CONFLICT", "Ya existe una categoría con ese nombre")
    c = ServiceCategory(tenant_id=p.tid, **d.model_dump())
    db.add(c)
    await db.flush()
    _aud(db, p, "categoria_creada", "service_category", c.id, despues=d.model_dump())
    return CategoryOut.model_validate(c)


async def patch_category(db: AsyncSession, p: Principal, cid: UUID, d: CategoryPatch) -> CategoryOut:
    c = await get_or_404(db, ServiceCategory, cid, p.tid, "Categoría")
    for k, v in d.model_dump(exclude_unset=True).items():
        setattr(c, k, v)
    await db.flush()
    return CategoryOut.model_validate(c)


async def delete_category(db: AsyncSession, p: Principal, cid: UUID) -> None:
    c = await get_or_404(db, ServiceCategory, cid, p.tid, "Categoría")
    en_uso = (await db.execute(scoped(Service, p.tid).where(Service.category_id == cid).limit(1))).first()
    if en_uso:
        raise AppError("CONFLICT", "La categoría tiene servicios; muévelos o elimínalos primero")
    c.deleted_at = datetime.now(UTC)
    _aud(db, p, "categoria_eliminada", "service_category", c.id)


# ---- servicios
async def _check_category(db: AsyncSession, p: Principal, category_id: UUID | None) -> None:
    if category_id:
        await get_or_404(db, ServiceCategory, category_id, p.tid, "Categoría")


async def list_services(db: AsyncSession, p: Principal, pp: PageParams, q: str | None, category_id: UUID | None,
                        activo: bool | None) -> Page[ServiceOut]:
    stmt = scoped(Service, p.tid)
    if q:
        stmt = stmt.where(Service.nombre.ilike(f"%{q}%"))
    if category_id:
        stmt = stmt.where(Service.category_id == category_id)
    if activo is not None:
        stmt = stmt.where(Service.activo.is_(activo))
    rows, total = await paginate(db, stmt, pp, {"nombre": Service.nombre, "precio": Service.precio,
                                                "duracion_min": Service.duracion_min}, "nombre")
    return Page[ServiceOut](items=[ServiceOut.model_validate(r) for r in rows], page=pp.page,
                            page_size=pp.page_size, total=total)


async def get_service(db: AsyncSession, p: Principal, sid: UUID) -> ServiceOut:
    return ServiceOut.model_validate(await get_or_404(db, Service, sid, p.tid, "Servicio"))


async def create_service(db: AsyncSession, p: Principal, d: ServiceIn) -> ServiceOut:
    await _check_category(db, p, d.category_id)
    data = d.model_dump(exclude={"porcentaje_iva"})
    s = Service(tenant_id=p.tid, porcentaje_iva=normalize_iva(d.aplica_iva, d.porcentaje_iva), **data)
    db.add(s)
    await db.flush()
    _aud(db, p, "servicio_creado", "service", s.id, despues=d.model_dump(mode="json"))
    return ServiceOut.model_validate(s)


async def patch_service(db: AsyncSession, p: Principal, sid: UUID, d: ServicePatch) -> ServiceOut:
    s = await get_or_404(db, Service, sid, p.tid, "Servicio")
    ch = d.model_dump(exclude_unset=True)
    if "category_id" in ch:
        await _check_category(db, p, ch["category_id"])
    antes = {k: str(getattr(s, k)) for k in ch}
    for k, v in ch.items():
        setattr(s, k, v)
    pct = ch["porcentaje_iva"] if "porcentaje_iva" in ch else (s.porcentaje_iva or None)
    s.porcentaje_iva = normalize_iva(s.aplica_iva, pct)
    await db.flush()
    _aud(db, p, "servicio_actualizado", "service", s.id, antes, d.model_dump(mode="json", exclude_unset=True))
    return ServiceOut.model_validate(s)


async def delete_service(db: AsyncSession, p: Principal, sid: UUID) -> None:
    s = await get_or_404(db, Service, sid, p.tid, "Servicio")
    s.deleted_at, s.activo = datetime.now(UTC), False     # las citas/ventas pasadas conservan su precio pactado
    _aud(db, p, "servicio_eliminado", "service", s.id)


async def services_by_ids(db: AsyncSession, tenant_id: UUID, ids: list[UUID]) -> list[Service]:
    """Servicios activos del tenant, en el mismo orden pedido; error si falta alguno."""
    found = {s.id: s for s in (await db.execute(scoped(Service, tenant_id).where(
        Service.id.in_(ids), Service.activo.is_(True)))).scalars()}
    missing = [str(i) for i in ids if i not in found]
    if missing:
        raise AppError("NOT_FOUND", "Servicio no encontrado o inactivo", {"servicios": missing})
    return [found[i] for i in ids]

