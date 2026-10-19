from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import Page, PageParams, page_params
from app.core.rbac import require_permission
from app.core.tenancy import Principal, get_db, get_principal
from app.modules.catalog import service
from app.modules.catalog.schemas import (
    CategoryIn,
    CategoryOut,
    CategoryPatch,
    ServiceIn,
    ServiceOut,
    ServicePatch,
)

router = APIRouter(prefix="/catalog", tags=["catalog"])
_manage = require_permission("catalog:manage")


@router.get("/categories", response_model=list[CategoryOut])
async def list_categories(p: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)) -> list[CategoryOut]:
    return await service.list_categories(db, p)


@router.post("/categories", response_model=CategoryOut, status_code=201)
async def create_category(body: CategoryIn, p: Principal = Depends(_manage), db: AsyncSession = Depends(get_db)) -> CategoryOut:
    return await service.create_category(db, p, body)


@router.patch("/categories/{category_id}", response_model=CategoryOut)
async def patch_category(category_id: UUID, body: CategoryPatch, p: Principal = Depends(_manage),
                         db: AsyncSession = Depends(get_db)) -> CategoryOut:
    return await service.patch_category(db, p, category_id, body)


@router.delete("/categories/{category_id}", status_code=204)
async def delete_category(category_id: UUID, p: Principal = Depends(_manage), db: AsyncSession = Depends(get_db)) -> Response:
    await service.delete_category(db, p, category_id)
    return Response(status_code=204)


@router.get("/services", response_model=Page[ServiceOut], summary="Filtros: q, category_id, activo. Orden: nombre|precio|duracion_min")
async def list_services(q: str | None = None, category_id: UUID | None = None, activo: bool | None = None,
                        pp: PageParams = Depends(page_params), p: Principal = Depends(get_principal),
                        db: AsyncSession = Depends(get_db)) -> Page[ServiceOut]:
    return await service.list_services(db, p, pp, q, category_id, activo)


@router.get("/services/{service_id}", response_model=ServiceOut)
async def get_service(service_id: UUID, p: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)) -> ServiceOut:
    return await service.get_service(db, p, service_id)


@router.post("/services", response_model=ServiceOut, status_code=201)
async def create_service(body: ServiceIn, p: Principal = Depends(_manage), db: AsyncSession = Depends(get_db)) -> ServiceOut:
    return await service.create_service(db, p, body)


@router.patch("/services/{service_id}", response_model=ServiceOut)
async def patch_service(service_id: UUID, body: ServicePatch, p: Principal = Depends(_manage),
                        db: AsyncSession = Depends(get_db)) -> ServiceOut:
    return await service.patch_service(db, p, service_id, body)


@router.delete("/services/{service_id}", status_code=204, summary="Borrado lógico")
async def delete_service(service_id: UUID, p: Principal = Depends(_manage), db: AsyncSession = Depends(get_db)) -> Response:
    await service.delete_service(db, p, service_id)
    return Response(status_code=204)
