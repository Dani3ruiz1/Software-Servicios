from datetime import UTC, date, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import Page, PageParams, page_params
from app.core.rbac import require_any_permission, require_permission
from app.core.tenancy import Principal, get_db, get_principal, require_module
from app.modules.appointments import service
from app.modules.appointments.schemas import (
    AppointmentIn,
    AppointmentOut,
    AppointmentPatch,
    AvailabilityOut,
    CalendarOut,
    Estado,
    MoveIn,
    StatsOut,
    StatusIn,
    Vista,
)

# Todo el módulo exige el módulo "calendar" activo en el plan del negocio.
router = APIRouter(prefix="/appointments", tags=["appointments"], dependencies=[Depends(require_module("calendar"))])
_write = require_permission("appointments:write")


@router.get("/availability", response_model=AvailabilityOut,
            summary="Franjas libres (horario + bloqueos + citas + duración + buffer + cupos + anticipación)")
async def availability(fecha_desde: date, fecha_hasta: date | None = None, service_ids: list[UUID] = Query([]),
                       staff_id: UUID | None = None, duracion_min: int | None = Query(None, gt=0, le=720),
                       p: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)) -> AvailabilityOut:
    return await service.availability(db, p.tid, desde=fecha_desde, hasta=fecha_hasta or fecha_desde,
                                      service_ids=service_ids, staff_id=staff_id, duracion_min=duracion_min)


@router.get("/calendar", response_model=CalendarOut,
            summary="Vista de agenda por día/semana/mes (el rol profesional solo ve lo suyo)")
async def calendar(vista: Vista = "semana", fecha: date | None = None, staff_id: UUID | None = None,
                   p: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)) -> CalendarOut:
    ctx = await service.get_ctx(db, p.tid)
    hoy = datetime.now(UTC).astimezone(ctx.tz).date()
    return await service.calendar(db, p, vista, fecha or hoy, staff_id)


@router.get("/stats", response_model=StatsOut, summary="Conteo de turnos por estado en un rango (`desde` inclusive, `hasta` exclusive)")
async def stats(desde: datetime, hasta: datetime, p: Principal = Depends(get_principal),
                db: AsyncSession = Depends(get_db)) -> StatsOut:
    return await service.stats(db, p, desde, hasta)


@router.get("", response_model=Page[AppointmentOut], summary="Lista paginada. Orden: inicio|created_at")
async def list_appointments(desde: datetime | None = None, hasta: datetime | None = None, staff_id: UUID | None = None,
                            estado: Estado | None = None, customer_id: UUID | None = None,
                            pp: PageParams = Depends(page_params), p: Principal = Depends(get_principal),
                            db: AsyncSession = Depends(get_db)) -> Page[AppointmentOut]:
    return await service.list_appointments(db, p, pp, desde, hasta, staff_id, estado, customer_id)


@router.post("", response_model=AppointmentOut, status_code=201,
             summary="Crea cita. 409 SLOT_TAKEN si el profesional ya está ocupado")
async def create_appointment(body: AppointmentIn, p: Principal = Depends(_write),
                             db: AsyncSession = Depends(get_db)) -> AppointmentOut:
    return await service.create_appointment(db, p, body)


@router.get("/{appointment_id}", response_model=AppointmentOut)
async def get_appointment(appointment_id: UUID, p: Principal = Depends(get_principal),
                          db: AsyncSession = Depends(get_db)) -> AppointmentOut:
    return await service.get_appointment(db, p, appointment_id)


@router.patch("/{appointment_id}", response_model=AppointmentOut)
async def patch_appointment(appointment_id: UUID, body: AppointmentPatch, p: Principal = Depends(_write),
                            db: AsyncSession = Depends(get_db)) -> AppointmentOut:
    return await service.patch_appointment(db, p, appointment_id, body)


@router.post("/{appointment_id}/status", response_model=AppointmentOut,
             summary="Cambia el estado (máquina de estados). El profesional solo puede en_atencion/atendida en lo suyo")
async def change_status(appointment_id: UUID, body: StatusIn,
                        p: Principal = Depends(require_any_permission("appointments:write", "appointments:status_own")),
                        db: AsyncSession = Depends(get_db)) -> AppointmentOut:
    return await service.change_status(db, p, appointment_id, body)


@router.post("/{appointment_id}/move", response_model=AppointmentOut, summary="Reprograma y/o reasigna profesional")
async def move_appointment(appointment_id: UUID, body: MoveIn, p: Principal = Depends(_write),
                           db: AsyncSession = Depends(get_db)) -> AppointmentOut:
    return await service.move_appointment(db, p, appointment_id, body)
