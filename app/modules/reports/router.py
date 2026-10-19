from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rbac import require_permission
from app.core.tenancy import Principal, get_db, require_module
from app.modules.reports import export, service
from app.modules.reports.schemas import Agrupar, Formato, ReportName, ReportOut

router = APIRouter(prefix="/reports", tags=["reports"], dependencies=[Depends(require_module("reports"))])


@router.get("/{nombre}", response_model=ReportOut,
            summary="Reporte (json) o archivo (xlsx/pdf) con `formato`. Mismos filtros para los tres")
async def report(nombre: ReportName, desde: date, hasta: date, agrupar: Agrupar = "dia", staff_id: UUID | None = None,
                 formato: Formato = "json", p: Principal = Depends(require_permission("reports:read")),
                 db: AsyncSession = Depends(get_db)) -> ReportOut | Response:
    """`agrupar` aplica a ventas, ocupacion, clientes y citas. Fechas locales del negocio, ambas inclusivas (máx. 366 días).
    Reportes: ventas · por-profesional · por-servicio · por-metodo · ocupacion · clientes · citas."""
    r = await service.run(db, p, nombre, desde, hasta, agrupar, staff_id)
    if formato == "json":
        return r
    negocio = (await db.execute(text("SELECT nombre FROM tenants WHERE id = :t"), {"t": p.tid})).scalar_one()
    fname = f"reporte-{nombre}-{desde}-{hasta}.{formato}"
    body, mime = (export.to_xlsx(r, negocio), export.XLSX) if formato == "xlsx" else (export.to_pdf(r, negocio), export.PDF)
    return Response(body, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{fname}"'})
