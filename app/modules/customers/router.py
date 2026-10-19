from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import Page, PageParams, page_params
from app.core.rbac import require_permission
from app.core.tenancy import Principal, get_db, get_principal
from app.modules.customers import service
from app.modules.customers.schemas import CustomerHistory, CustomerIn, CustomerOut, CustomerPatch

router = APIRouter(prefix="/customers", tags=["customers"])
_write = require_permission("customers:write")


@router.get("", response_model=Page[CustomerOut], summary="Búsqueda `q` por nombre, teléfono, email o documento")
async def list_customers(q: str | None = None, pp: PageParams = Depends(page_params),
                         p: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)) -> Page[CustomerOut]:
    return await service.list_customers(db, p, pp, q)


@router.post("", response_model=CustomerOut, status_code=201)
async def create_customer(body: CustomerIn, p: Principal = Depends(_write), db: AsyncSession = Depends(get_db)) -> CustomerOut:
    return await service.create_customer(db, p, body)


@router.get("/{customer_id}", response_model=CustomerOut)
async def get_customer(customer_id: UUID, p: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)) -> CustomerOut:
    return await service.get_customer(db, p, customer_id)


@router.patch("/{customer_id}", response_model=CustomerOut)
async def patch_customer(customer_id: UUID, body: CustomerPatch, p: Principal = Depends(_write),
                         db: AsyncSession = Depends(get_db)) -> CustomerOut:
    return await service.patch_customer(db, p, customer_id, body)


@router.delete("/{customer_id}", status_code=204, summary="Borrado lógico")
async def delete_customer(customer_id: UUID, p: Principal = Depends(_write), db: AsyncSession = Depends(get_db)) -> Response:
    await service.delete_customer(db, p, customer_id)
    return Response(status_code=204)


@router.get("/{customer_id}/history", response_model=CustomerHistory, summary="Historial de citas y contadores")
async def history(customer_id: UUID, pp: PageParams = Depends(page_params), p: Principal = Depends(get_principal),
                  db: AsyncSession = Depends(get_db)) -> CustomerHistory:
    return await service.history(db, p, customer_id, pp)
