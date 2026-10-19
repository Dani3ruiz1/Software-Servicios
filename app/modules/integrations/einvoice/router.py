from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import Page, PageParams, page_params
from app.core.rbac import require_permission
from app.core.tenancy import Principal, get_db, require_module, tenant_tx
from app.modules.integrations.einvoice import service
from app.modules.integrations.einvoice.schemas import (
    CreditNoteIn,
    EmitIn,
    InvoiceDetail,
    InvoiceOut,
    ProviderIn,
    ProviderStatus,
    RetryIn,
    SettingsIn,
    SettingsOut,
    ValidationOut,
)

# Todo exige el módulo `einvoice` (plan Premium). Sin él, el negocio sigue con su comprobante interno (ventas).
router = APIRouter(tags=["einvoice"], dependencies=[Depends(require_module("einvoice"))])
_cfg = require_permission("integrations:manage")
_inv = require_permission("invoices:manage")


@router.get("/einvoice/settings", response_model=SettingsOut | None, summary="Resolución de numeración, NIT y cuánto rango queda (null si no está configurado)")
async def get_settings(p: Principal = Depends(_cfg), db: AsyncSession = Depends(get_db)) -> SettingsOut | None:
    return await service.settings_out(db, p.tid)


@router.put("/einvoice/settings", response_model=SettingsOut, summary="Guarda NIT (valida el DV), resolución, prefijo y rango autorizados por la DIAN")
async def put_settings(body: SettingsIn, p: Principal = Depends(_cfg), db: AsyncSession = Depends(get_db)) -> SettingsOut:
    return await service.put_settings(db, p, body)


@router.get("/einvoice/provider", response_model=ProviderStatus)
async def get_provider(p: Principal = Depends(_cfg), db: AsyncSession = Depends(get_db)) -> ProviderStatus:
    return await service.provider_status(db, p)


@router.put("/einvoice/provider", response_model=ProviderStatus, summary="Elige el proveedor tecnológico y guarda sus credenciales cifradas")
async def put_provider(body: ProviderIn, p: Principal = Depends(_cfg), db: AsyncSession = Depends(get_db)) -> ProviderStatus:
    return await service.put_provider(db, p, body)


@router.post("/einvoice/provider/validate", response_model=ValidationOut)
async def validate_provider(p: Principal = Depends(_cfg), db: AsyncSession = Depends(get_db)) -> ValidationOut:
    return await service.validate_provider(db, p)


@router.post("/sales/{sale_id}/invoice", response_model=InvoiceOut, status_code=201,
             summary="Factura electrónica de una venta PAGADA (opcional por venta). Queda `emitida` y se envía en segundo plano")
async def emit(sale_id: UUID, body: EmitIn, background: BackgroundTasks, p: Principal = Depends(_inv)) -> InvoiceOut:
    async with tenant_tx(p) as db:                      # se confirma AQUÍ, antes de lanzar el envío en segundo plano
        out = await service.emit_invoice(db, p, sale_id, body.adquirente)
    background.add_task(service.run_dispatch, p.tid, out.id)
    return out


@router.get("/invoices", response_model=Page[InvoiceOut], summary="Facturas y notas crédito. Orden: fecha_emision|numero")
async def list_invoices(estado: str | None = None, tipo: str | None = None, sale_id: UUID | None = None, desde: datetime | None = None,
                        hasta: datetime | None = None, pp: PageParams = Depends(page_params), p: Principal = Depends(_inv),
                        db: AsyncSession = Depends(get_db)) -> Page[InvoiceOut]:
    return await service.list_invoices(db, p, pp, estado, tipo, sale_id, desde, hasta)


@router.get("/invoices/{invoice_id}", response_model=InvoiceDetail)
async def get_invoice(invoice_id: UUID, p: Principal = Depends(_inv), db: AsyncSession = Depends(get_db)) -> InvoiceDetail:
    return await service.get_invoice(db, p, invoice_id)


@router.post("/invoices/{invoice_id}/retry", response_model=InvoiceOut,
             summary="Reenvía una `rechazada` o en `error` con el MISMO número (puede corregir el adquirente)")
async def retry(invoice_id: UUID, body: RetryIn, background: BackgroundTasks, p: Principal = Depends(_inv)) -> InvoiceOut:
    async with tenant_tx(p) as db:
        out = await service.retry_invoice(db, p, invoice_id, body.adquirente)
    background.add_task(service.run_dispatch, p.tid, out.id)
    return out


@router.post("/invoices/{invoice_id}/credit-note", response_model=InvoiceOut, status_code=201,
             summary="Nota crédito de una factura ACEPTADA (total o por ítems). No supera el valor de la factura")
async def credit_note(invoice_id: UUID, body: CreditNoteIn, background: BackgroundTasks, p: Principal = Depends(_inv)) -> InvoiceOut:
    async with tenant_tx(p) as db:
        out = await service.create_credit_note(db, p, invoice_id, body)
    background.add_task(service.run_dispatch, p.tid, out.id)
    return out


@router.get("/invoices/{invoice_id}/pdf", summary="PDF del proveedor SIMULADO (marcado sin validez fiscal). Los proveedores reales traen su `pdf_url`")
async def pdf(invoice_id: UUID, p: Principal = Depends(_inv), db: AsyncSession = Depends(get_db)) -> Response:
    return Response(await service.sandbox_pdf(db, p, invoice_id), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="documento-{invoice_id}.pdf"'})
