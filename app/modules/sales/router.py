from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import Page, PageParams, page_params
from app.core.rbac import require_permission
from app.core.tenancy import Principal, get_db, require_any_module
from app.modules.sales import service
from app.modules.sales.schemas import (
    PayIn,
    Receipt,
    SaleIn,
    SaleListItem,
    SaleOut,
    SendIn,
    SendOut,
    VoidIn,
)

# Ventas existen con calendario (plan independiente) o con recepción.
router = APIRouter(prefix="/sales", tags=["sales"], dependencies=[Depends(require_any_module("calendar", "reception"))])
_read = require_permission("sales:read")
_write = require_permission("sales:write")
_void = require_permission("sales:void")


@router.post("", response_model=SaleOut, status_code=201,
             summary="Venta sin cita, o convertir una cita (appointment_id). Con `pagos` se cobra en el acto")
async def create_sale(body: SaleIn, p: Principal = Depends(_write), db: AsyncSession = Depends(get_db)) -> SaleOut:
    return await service.create_sale(db, p, body)


@router.get("", response_model=Page[SaleListItem], summary="Orden: created_at|total|pagada_en")
async def list_sales(desde: datetime | None = None, hasta: datetime | None = None, estado: str | None = None,
                     customer_id: UUID | None = None, staff_id: UUID | None = None, cash_session_id: UUID | None = None,
                     pp: PageParams = Depends(page_params), p: Principal = Depends(_read),
                     db: AsyncSession = Depends(get_db)) -> Page[SaleListItem]:
    return await service.list_sales(db, p, pp, desde, hasta, estado, customer_id, staff_id, cash_session_id)


@router.get("/{sale_id}", response_model=SaleOut)
async def get_sale(sale_id: UUID, p: Principal = Depends(_read), db: AsyncSession = Depends(get_db)) -> SaleOut:
    return await service.get_sale(db, p, sale_id)


@router.post("/{sale_id}/pay", response_model=SaleOut, summary="Cobra una venta abierta (pago dividido: varios `pagos`)")
async def pay_sale(sale_id: UUID, body: PayIn, p: Principal = Depends(_write), db: AsyncSession = Depends(get_db)) -> SaleOut:
    return await service.pay_sale(db, p, sale_id, body)


@router.post("/{sale_id}/void", response_model=SaleOut, summary="Anula (solo mientras su caja siga abierta)")
async def void_sale(sale_id: UUID, body: VoidIn, p: Principal = Depends(_void), db: AsyncSession = Depends(get_db)) -> SaleOut:
    return await service.void_sale(db, p, sale_id, body)


@router.get("/{sale_id}/receipt", response_model=Receipt, summary="Comprobante interno para imprimir/mostrar")
async def receipt(sale_id: UUID, p: Principal = Depends(_read), db: AsyncSession = Depends(get_db)) -> Receipt:
    return await service.receipt(db, p, sale_id)


@router.post("/{sale_id}/send", response_model=SendOut, status_code=202, summary="Encola el envío del comprobante por WhatsApp o correo")
async def send_receipt(sale_id: UUID, body: SendIn, p: Principal = Depends(_write), db: AsyncSession = Depends(get_db)) -> SendOut:
    return await service.send_receipt(db, p, sale_id, body)
