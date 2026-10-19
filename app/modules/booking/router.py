from collections.abc import AsyncIterator
from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import scoped_session
from app.core.ratelimit import rate_limit
from app.core.rbac import require_permission
from app.core.tenancy import Principal, get_db, require_module
from app.modules.booking import service
from app.modules.booking.schemas import (
    BookingIn,
    BookingOut,
    BookingView,
    CancelIn,
    FieldIn,
    FieldOut,
    FieldPatch,
    PublicAvailability,
    PublicConfig,
    RescheduleIn,
    RuleIn,
    RuleOut,
    SettingsIn,
    SettingsOut,
)

# ================================================================== administración (requiere login + módulo `booking`)
router = APIRouter(prefix="/booking", tags=["booking (admin)"], dependencies=[Depends(require_module("booking"))])
_manage = require_permission("booking:manage")


@router.get("/settings", response_model=SettingsOut, summary="Marca, textos, reglas y modo de asignación")
async def get_settings(p: Principal = Depends(_manage), db: AsyncSession = Depends(get_db)) -> SettingsOut:
    return await service.get_admin_settings(db, p)


@router.put("/settings", response_model=SettingsOut, summary="Guarda la configuración (crea la primera vez)")
async def put_settings(body: SettingsIn, p: Principal = Depends(_manage), db: AsyncSession = Depends(get_db)) -> SettingsOut:
    return await service.put_admin_settings(db, p, body)


@router.get("/form-fields", response_model=list[FieldOut])
async def list_fields(p: Principal = Depends(_manage), db: AsyncSession = Depends(get_db)) -> list[FieldOut]:
    return await service.list_fields(db, p)


@router.post("/form-fields", response_model=FieldOut, status_code=201, summary="Campo extra del formulario de reserva (máx. 20)")
async def create_field(body: FieldIn, p: Principal = Depends(_manage), db: AsyncSession = Depends(get_db)) -> FieldOut:
    return await service.create_field(db, p, body)


@router.patch("/form-fields/{field_id}", response_model=FieldOut)
async def patch_field(field_id: UUID, body: FieldPatch, p: Principal = Depends(_manage), db: AsyncSession = Depends(get_db)) -> FieldOut:
    return await service.patch_field(db, p, field_id, body)


@router.delete("/form-fields/{field_id}", status_code=204)
async def delete_field(field_id: UUID, p: Principal = Depends(_manage), db: AsyncSession = Depends(get_db)) -> Response:
    await service.delete_field(db, p, field_id)
    return Response(status_code=204)


@router.get("/rules", response_model=list[RuleOut], summary="Reglas de cupos simultáneos y duración del turno")
async def list_rules(p: Principal = Depends(_manage), db: AsyncSession = Depends(get_db)) -> list[RuleOut]:
    return await service.list_rules(db, p)


@router.post("/rules", response_model=RuleOut, status_code=201)
async def create_rule(body: RuleIn, p: Principal = Depends(_manage), db: AsyncSession = Depends(get_db)) -> RuleOut:
    return await service.create_rule(db, p, body)


@router.delete("/rules/{rule_id}", status_code=204)
async def delete_rule(rule_id: UUID, p: Principal = Depends(_manage), db: AsyncSession = Depends(get_db)) -> Response:
    await service.delete_rule(db, p, rule_id)
    return Response(status_code=204)


# ================================================================== público (SIN login; el negocio se identifica por la dirección)
public_router = APIRouter(prefix="/public/{slug}", tags=["public booking"])
Slug = Annotated[str, Path(pattern=r"^[A-Za-z0-9-]{3,50}$")]


async def _ctx(slug: Slug) -> service.PublicCtx:
    return await service.resolve(slug)


async def _db(c: service.PublicCtx = Depends(_ctx)) -> AsyncIterator[AsyncSession]:
    async with scoped_session(tenant_id=c.tenant_id) as db:            # RLS: solo ve los datos de ESE negocio
        yield db


@public_router.get("/config", response_model=PublicConfig, dependencies=[Depends(rate_limit("cfg", 60, 60))],
                   summary="Marca, formulario, servicios y profesionales visibles")
async def config(c: service.PublicCtx = Depends(_ctx), db: AsyncSession = Depends(_db)) -> PublicConfig:
    return await service.public_config(db, c)


@public_router.get("/availability", response_model=PublicAvailability, dependencies=[Depends(rate_limit("avail", 40, 60))],
                   summary="Horarios libres (máx. 14 días por consulta)")
async def availability(fecha_desde: date, fecha_hasta: date | None = None, service_ids: list[UUID] = Query([], max_length=10),
                       staff_id: UUID | None = None, c: service.PublicCtx = Depends(_ctx),
                       db: AsyncSession = Depends(_db)) -> PublicAvailability:
    return await service.public_availability(db, c, fecha_desde, fecha_hasta or fecha_desde, service_ids, staff_id)


@public_router.post("/bookings", response_model=BookingOut, status_code=201,
                    dependencies=[Depends(rate_limit("book", 5, 600)), Depends(rate_limit("book-day", 20, 86400))],
                    summary="Crea la reserva. 409 SLOT_UNAVAILABLE/SLOT_TAKEN, 429 si abusa. Devuelve el token para gestionarla")
async def create_booking(body: BookingIn, c: service.PublicCtx = Depends(_ctx), db: AsyncSession = Depends(_db)) -> BookingOut:
    return await service.create_booking(db, c, body)


@public_router.get("/bookings/{token}", response_model=BookingView, dependencies=[Depends(rate_limit("view", 30, 60))])
async def view_booking(token: str, c: service.PublicCtx = Depends(_ctx), db: AsyncSession = Depends(_db)) -> BookingView:
    return await service.view_booking(db, c, token)


@public_router.post("/bookings/{token}/cancel", response_model=BookingView, dependencies=[Depends(rate_limit("cancel", 10, 3600))],
                    summary="Cancela. 409 CANCELLATION_WINDOW_CLOSED si ya pasó el límite")
async def cancel_booking(token: str, body: CancelIn, c: service.PublicCtx = Depends(_ctx), db: AsyncSession = Depends(_db)) -> BookingView:
    return await service.cancel_booking(db, c, token, body)


@public_router.post("/bookings/{token}/reschedule", response_model=BookingOut, dependencies=[Depends(rate_limit("resched", 10, 3600))],
                    summary="Reprograma. Devuelve un token NUEVO; el anterior deja de servir")
async def reschedule_booking(token: str, body: RescheduleIn, c: service.PublicCtx = Depends(_ctx),
                             db: AsyncSession = Depends(_db)) -> BookingOut:
    return await service.reschedule_booking(db, c, token, body)
