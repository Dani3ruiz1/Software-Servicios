from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import Page, PageParams, page_params
from app.core.rbac import require_permission
from app.core.tenancy import Principal, get_db, require_module
from app.modules.inventory import service
from app.modules.inventory.schemas import (
    MovementIn,
    MovementOut,
    ProductIn,
    ProductOut,
    ProductPatch,
    SummaryOut,
)

router = APIRouter(prefix="/inventory", tags=["inventory"], dependencies=[Depends(require_module("inventory"))])
_manage = require_permission("inventory:manage")      # también para leer: el costo de los productos no es para todo el equipo


@router.get("/summary", response_model=SummaryOut, summary="Productos, cuántos están bajo stock y valor del inventario a costo")
async def summary(p: Principal = Depends(_manage), db: AsyncSession = Depends(get_db)) -> SummaryOut:
    return await service.summary(db, p)


@router.get("/products", response_model=Page[ProductOut], summary="Filtros: q (nombre o código), activo, bajo_stock. Orden: nombre|stock_actual|created_at")
async def list_products(q: str | None = None, activo: bool | None = None, bajo_stock: bool | None = None, pp: PageParams = Depends(page_params),
                        p: Principal = Depends(_manage), db: AsyncSession = Depends(get_db)) -> Page[ProductOut]:
    return await service.list_products(db, p, pp, q, activo, bajo_stock)


@router.post("/products", response_model=ProductOut, status_code=201, summary="Crea un producto; `stock_inicial` queda registrado como entrada")
async def create_product(body: ProductIn, p: Principal = Depends(_manage), db: AsyncSession = Depends(get_db)) -> ProductOut:
    return await service.create_product(db, p, body)


@router.get("/products/{product_id}", response_model=ProductOut)
async def get_product(product_id: UUID, p: Principal = Depends(_manage), db: AsyncSession = Depends(get_db)) -> ProductOut:
    return await service.get_product(db, p, product_id)


@router.patch("/products/{product_id}", response_model=ProductOut, summary="Edita datos del producto (el stock solo cambia con movimientos)")
async def patch_product(product_id: UUID, body: ProductPatch, p: Principal = Depends(_manage), db: AsyncSession = Depends(get_db)) -> ProductOut:
    return await service.patch_product(db, p, product_id, body)


@router.delete("/products/{product_id}", status_code=204, summary="Borrado lógico (el historial de movimientos se conserva)")
async def delete_product(product_id: UUID, p: Principal = Depends(_manage), db: AsyncSession = Depends(get_db)) -> Response:
    await service.delete_product(db, p, product_id)
    return Response(status_code=204)


@router.post("/products/{product_id}/movements", response_model=MovementOut, status_code=201,
             summary="Entrada, salida o ajuste. 409 STOCK_INSUFFICIENT si una salida pide más de lo que hay")
async def add_movement(product_id: UUID, body: MovementIn, p: Principal = Depends(_manage), db: AsyncSession = Depends(get_db)) -> MovementOut:
    return await service.add_movement(db, p, product_id, body)


@router.get("/products/{product_id}/movements", response_model=Page[MovementOut], summary="Historial del producto, lo más reciente primero")
async def list_movements(product_id: UUID, pp: PageParams = Depends(page_params), p: Principal = Depends(_manage),
                         db: AsyncSession = Depends(get_db)) -> Page[MovementOut]:
    return await service.list_movements(db, p, product_id, pp)
