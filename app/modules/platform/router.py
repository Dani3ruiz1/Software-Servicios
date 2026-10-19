from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.http import client_ip
from app.core.pagination import Page, PageParams, page_params
from app.core.rbac import require_platform
from app.core.tenancy import Principal, get_db
from app.modules.platform import analytics, panels, service
from app.modules.platform.schemas import (
    AnalyticsOut,
    AssignPlanIn,
    BillingOut,
    DashboardOut,
    Estado,
    ImpersonationOut,
    ModuleCode,
    ModuleOut,
    ModulePatch,
    PaymentIn,
    PaymentOut,
    PaymentResult,
    PlanIn,
    PlanOut,
    PlanPatch,
    TenantCreate,
    TenantDetail,
    TenantListItem,
    TenantModuleIn,
    TenantPatch,
)

# Todo el router exige Super Admin ANTES de abrir la sesión de BD.
router = APIRouter(prefix="/platform", tags=["platform (super admin)"], dependencies=[Depends(require_platform)])



@router.get("/dashboard", response_model=DashboardOut, summary="Empresas por estado y MRR")
async def dashboard(db: AsyncSession = Depends(get_db)) -> DashboardOut:
    return await service.dashboard(db)


@router.get("/analytics", response_model=AnalyticsOut, summary="Métricas de toda la plataforma para el tablero: negocios, reservas, ventas, retención y horas pico")
async def platform_analytics(dias: int = Query(30, ge=7, le=365, description="Ventana en días terminando hoy"),
                             top: int = Query(8, ge=1, le=200, description="Cuántos negocios trae el ranking"),
                             db: AsyncSession = Depends(get_db)) -> AnalyticsOut:
    return await analytics.run(db, dias, top)


# ---- módulos y planes
@router.get("/modules", response_model=list[ModuleOut])
async def modules(db: AsyncSession = Depends(get_db)) -> list[ModuleOut]:
    return await service.list_modules(db)


@router.patch("/modules/{codigo}", response_model=ModuleOut, summary="Cambia nombre o precio base del módulo")
async def patch_module(codigo: ModuleCode, body: ModulePatch, r: Request, p: Principal = Depends(require_platform),
                       db: AsyncSession = Depends(get_db)) -> ModuleOut:
    return await service.patch_module(db, p, codigo, body, client_ip(r))


@router.get("/plans", response_model=list[PlanOut])
async def plans(solo_activos: bool = False, db: AsyncSession = Depends(get_db)) -> list[PlanOut]:
    return await service.list_plans(db, solo_activos)


@router.post("/plans", response_model=PlanOut, status_code=201)
async def create_plan(body: PlanIn, r: Request, p: Principal = Depends(require_platform),
                      db: AsyncSession = Depends(get_db)) -> PlanOut:
    return await service.create_plan(db, p, body, client_ip(r))


@router.patch("/plans/{plan_id}", response_model=PlanOut, summary="No altera el precio ya pactado de las suscripciones")
async def patch_plan(plan_id: UUID, body: PlanPatch, r: Request, p: Principal = Depends(require_platform),
                     db: AsyncSession = Depends(get_db)) -> PlanOut:
    return await service.patch_plan(db, p, plan_id, body, client_ip(r))


# ---- tenants
@router.get("/tenants", response_model=Page[TenantListItem])
async def list_tenants(q: str | None = None, estado: Estado | None = None, pp: PageParams = Depends(page_params),
                       db: AsyncSession = Depends(get_db)) -> Page[TenantListItem]:
    return await service.list_tenants(db, pp, q, estado)


@router.post("/tenants", response_model=TenantDetail, status_code=201,
             summary="Crea negocio + suscripción + módulos del plan + admin + servicios de ejemplo")
async def create_tenant(body: TenantCreate, r: Request, p: Principal = Depends(require_platform),
                        db: AsyncSession = Depends(get_db)) -> TenantDetail:
    return await service.create_tenant(db, p, body, client_ip(r))


@router.get("/tenants/{tenant_id}", response_model=TenantDetail)
async def get_tenant(tenant_id: UUID, db: AsyncSession = Depends(get_db)) -> TenantDetail:
    return await service.get_tenant(db, tenant_id)


@router.patch("/tenants/{tenant_id}", response_model=TenantDetail)
async def patch_tenant(tenant_id: UUID, body: TenantPatch, r: Request, p: Principal = Depends(require_platform),
                       db: AsyncSession = Depends(get_db)) -> TenantDetail:
    return await service.patch_tenant(db, p, tenant_id, body, client_ip(r))


@router.post("/tenants/{tenant_id}/estado/{accion}", response_model=TenantDetail,
             summary="activar | suspender | bloquear (manual; ni pagos ni el job lo revierten, solo 'activar')")
async def set_estado(tenant_id: UUID, accion: Literal["activar", "suspender", "bloquear"], r: Request,
                     p: Principal = Depends(require_platform), db: AsyncSession = Depends(get_db)) -> TenantDetail:
    return await service.set_estado(db, p, tenant_id, accion, client_ip(r))


@router.put("/tenants/{tenant_id}/plan", response_model=TenantDetail, summary="Asigna/cambia plan y sincroniza módulos")
async def assign_plan(tenant_id: UUID, body: AssignPlanIn, r: Request, p: Principal = Depends(require_platform),
                      db: AsyncSession = Depends(get_db)) -> TenantDetail:
    return await service.assign_plan(db, p, tenant_id, body, client_ip(r))


@router.put("/tenants/{tenant_id}/modules/{codigo}", response_model=TenantDetail,
            summary="Módulo adicional con precio manual (precio_override=null usa el precio base)")
async def set_module(tenant_id: UUID, codigo: ModuleCode, body: TenantModuleIn, r: Request,
                     p: Principal = Depends(require_platform), db: AsyncSession = Depends(get_db)) -> TenantDetail:
    return await service.set_tenant_module(db, p, tenant_id, codigo, body, client_ip(r))


@router.get("/tenants/{tenant_id}/billing", response_model=BillingOut, summary="Desglose del precio mensual total")
async def billing(tenant_id: UUID, db: AsyncSession = Depends(get_db)) -> BillingOut:
    return (await service.get_tenant(db, tenant_id)).facturacion


@router.post("/tenants/{tenant_id}/payments", response_model=PaymentResult, status_code=201,
             summary="Registra pago/renovación: extiende la suscripción y desbloquea al instante")
async def register_payment(tenant_id: UUID, body: PaymentIn, r: Request, p: Principal = Depends(require_platform),
                           db: AsyncSession = Depends(get_db)) -> PaymentResult:
    return await service.register_payment(db, p, tenant_id, body, client_ip(r))


@router.get("/tenants/{tenant_id}/payments", response_model=Page[PaymentOut])
async def list_payments(tenant_id: UUID, pp: PageParams = Depends(page_params),
                        db: AsyncSession = Depends(get_db)) -> Page[PaymentOut]:
    return await service.list_payments(db, tenant_id, pp)


@router.post("/tenants/{tenant_id}/impersonate", response_model=ImpersonationOut,
             summary="Token de soporte (30 min) como admin del negocio; queda auditado")
async def impersonate(tenant_id: UUID, r: Request, p: Principal = Depends(require_platform),
                      db: AsyncSession = Depends(get_db)) -> ImpersonationOut:
    return await service.impersonate(db, p, tenant_id, client_ip(r))


@router.get("/cobros", response_model=panels.CobrosOut, summary="Pagos de suscripción de todos los negocios, con totales del mes y cartera vencida")
async def cobros(desde: date | None = None, hasta: date | None = None, metodo: str | None = Query(None, max_length=40),
                 pp: PageParams = Depends(page_params), db: AsyncSession = Depends(get_db)) -> panels.CobrosOut:
    return await panels.cobros(db, pp, desde, hasta, metodo)


@router.get("/usuarios", response_model=Page[panels.UsuarioOut], summary="Cuentas de todos los negocios (sin datos secretos)")
async def usuarios(q: str | None = Query(None, max_length=80), rol: Literal["admin", "recepcionista", "profesional"] | None = None,
                   tenant_id: UUID | None = None, pp: PageParams = Depends(page_params), db: AsyncSession = Depends(get_db)) -> Page[panels.UsuarioOut]:
    return await panels.usuarios(db, pp, q, rol, tenant_id)


@router.get("/auditoria", response_model=Page[panels.AuditoriaOut], summary="Bitácora de acciones de toda la plataforma (solo lectura)")
async def auditoria(accion: str | None = Query(None, max_length=60), tenant_id: UUID | None = None, actor_tipo: str | None = Query(None, max_length=20),
                    pp: PageParams = Depends(page_params), db: AsyncSession = Depends(get_db)) -> Page[panels.AuditoriaOut]:
    return await panels.auditoria(db, pp, accion, tenant_id, actor_tipo)


@router.get("/salud", response_model=panels.SaludOut, summary="Salud del sistema: cola de mensajes, integraciones, seguridad y base de datos")
async def salud(db: AsyncSession = Depends(get_db)) -> panels.SaludOut:
    return await panels.salud(db)


class _ActivoIn(BaseModel):
    activo: bool


@router.post("/usuarios/{user_id}/desbloquear", status_code=204, summary="Quita el bloqueo por intentos fallidos")
async def desbloquear_usuario(user_id: UUID, r: Request, p: Principal = Depends(require_platform), db: AsyncSession = Depends(get_db)) -> Response:
    await panels.desbloquear(db, p, user_id, client_ip(r))
    return Response(status_code=204)


@router.put("/usuarios/{user_id}/activo", status_code=204, summary="Activa o desactiva la cuenta (al desactivar se cierran sus sesiones)")
async def activo_usuario(user_id: UUID, body: _ActivoIn, r: Request, p: Principal = Depends(require_platform), db: AsyncSession = Depends(get_db)) -> Response:
    await panels.fijar_activo(db, p, user_id, body.activo, client_ip(r))
    return Response(status_code=204)


@router.post("/usuarios/{user_id}/restablecer-clave", response_model=panels.ClaveTemporalOut,
             summary="Genera una clave temporal (se muestra una sola vez), cierra sus sesiones y desbloquea la cuenta")
async def restablecer_clave(user_id: UUID, r: Request, p: Principal = Depends(require_platform), db: AsyncSession = Depends(get_db)) -> panels.ClaveTemporalOut:
    return await panels.restablecer_clave(db, p, user_id, client_ip(r))
