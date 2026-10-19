from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rbac import require_permission
from app.core.tenancy import Principal, get_db, get_principal
from app.modules.staff import service
from app.modules.staff.schemas import (
    AccessIn,
    AccessOut,
    BlockIn,
    BlockOut,
    ScheduleIn,
    ScheduleItem,
    StaffIn,
    StaffOut,
    StaffPatch,
    StaffServicesIn,
)

router = APIRouter(prefix="/staff", tags=["staff"])
_manage = require_permission("staff:manage")


@router.get("", response_model=list[StaffOut])
async def list_staff(solo_activos: bool = False, p: Principal = Depends(get_principal),
                     db: AsyncSession = Depends(get_db)) -> list[StaffOut]:
    return await service.list_staff(db, p, solo_activos)


@router.post("", response_model=StaffOut, status_code=201)
async def create_staff(body: StaffIn, p: Principal = Depends(_manage), db: AsyncSession = Depends(get_db)) -> StaffOut:
    return await service.create_staff(db, p, body)


@router.get("/{staff_id}", response_model=StaffOut)
async def get_staff(staff_id: UUID, p: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)) -> StaffOut:
    return await service.get_staff(db, p, staff_id)


@router.patch("/{staff_id}", response_model=StaffOut)
async def patch_staff(staff_id: UUID, body: StaffPatch, p: Principal = Depends(_manage),
                      db: AsyncSession = Depends(get_db)) -> StaffOut:
    return await service.patch_staff(db, p, staff_id, body)


@router.delete("/{staff_id}", status_code=204, summary="Borrado lógico; 409 si tiene citas futuras")
async def delete_staff(staff_id: UUID, p: Principal = Depends(_manage), db: AsyncSession = Depends(get_db)) -> Response:
    await service.delete_staff(db, p, staff_id)
    return Response(status_code=204)


_users = require_permission("users:manage")


@router.post("/{staff_id}/access", response_model=AccessOut, status_code=201,
             summary="Crea el acceso del profesional a su propia agenda. 409 USER_LIMIT_REACHED si el plan no tiene cupo")
async def grant_access(staff_id: UUID, body: AccessIn, p: Principal = Depends(_users), db: AsyncSession = Depends(get_db)) -> AccessOut:
    return await service.grant_access(db, p, staff_id, body)


@router.delete("/{staff_id}/access", status_code=204, summary="Quita el acceso (el perfil y sus citas se conservan)")
async def revoke_access(staff_id: UUID, p: Principal = Depends(_users), db: AsyncSession = Depends(get_db)) -> Response:
    await service.revoke_access(db, p, staff_id)
    return Response(status_code=204)


@router.put("/{staff_id}/services", response_model=StaffOut, summary="Reemplaza los servicios que realiza")
async def set_services(staff_id: UUID, body: StaffServicesIn, p: Principal = Depends(_manage),
                       db: AsyncSession = Depends(get_db)) -> StaffOut:
    return await service.set_services(db, p, staff_id, body.service_ids)


@router.get("/{staff_id}/schedule", response_model=list[ScheduleItem])
async def get_schedule(staff_id: UUID, p: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)) -> list[ScheduleItem]:
    return await service.get_schedule(db, p, staff_id)


@router.put("/{staff_id}/schedule", response_model=list[ScheduleItem], summary="Reemplaza el horario semanal")
async def set_schedule(staff_id: UUID, body: ScheduleIn, p: Principal = Depends(_manage),
                       db: AsyncSession = Depends(get_db)) -> list[ScheduleItem]:
    return await service.set_schedule(db, p, staff_id, body)


@router.get("/{staff_id}/blocks", response_model=list[BlockOut])
async def list_blocks(staff_id: UUID, desde: datetime | None = None, hasta: datetime | None = None,
                      p: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)) -> list[BlockOut]:
    return await service.list_blocks(db, p, staff_id, desde, hasta)


@router.post("/{staff_id}/blocks", response_model=BlockOut, status_code=201,
             summary="Almuerzo, vacaciones, festivo. Devuelve citas_afectadas (no las cancela)")
async def create_block(staff_id: UUID, body: BlockIn, p: Principal = Depends(_manage), db: AsyncSession = Depends(get_db)) -> BlockOut:
    return await service.create_block(db, p, staff_id, body)


@router.delete("/{staff_id}/blocks/{block_id}", status_code=204)
async def delete_block(staff_id: UUID, block_id: UUID, p: Principal = Depends(_manage), db: AsyncSession = Depends(get_db)) -> Response:
    await service.delete_block(db, p, staff_id, block_id)
    return Response(status_code=204)
