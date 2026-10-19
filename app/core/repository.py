"""Acceso a datos compartido. Defensa en profundidad: además de RLS, TODA consulta de negocio
filtra explícitamente por tenant_id (y oculta los borrados lógicos)."""
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.pagination import Page, PageParams, parse_sort


def scoped(model: Any, tenant_id: UUID) -> Select[Any]:
    q = select(model).where(model.tenant_id == tenant_id)
    if hasattr(model, "deleted_at"):
        q = q.where(model.deleted_at.is_(None))
    return q


async def get_or_404[M](db: AsyncSession, model: type[M], obj_id: UUID, tenant_id: UUID, label: str) -> M:
    obj = (await db.execute(scoped(model, tenant_id).where(model.id == obj_id))).scalar_one_or_none()  # type: ignore[attr-defined]
    if obj is None:
        raise AppError("NOT_FOUND", f"{label} no encontrado")
    return cast(M, obj)


async def paginate(db: AsyncSession, stmt: Select[Any], pp: PageParams, sortable: dict[str, Any],
                   default_sort: str) -> tuple[list[Any], int]:
    total = (await db.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))).scalar_one()
    col, desc = parse_sort(pp.sort, sortable, default_sort)
    stmt = stmt.order_by(col.desc() if desc else col.asc()).offset(pp.offset).limit(pp.page_size)  # type: ignore[attr-defined]
    return list((await db.execute(stmt)).scalars()), total


def page_of(items: list[Any], pp: PageParams, total: int) -> Page[Any]:
    return Page(items=items, page=pp.page, page_size=pp.page_size, total=total)
