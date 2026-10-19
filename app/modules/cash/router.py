from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import Page, PageParams, page_params
from app.core.rbac import require_permission
from app.core.tenancy import Principal, get_db, require_module
from app.modules.cash import service
from app.modules.cash.schemas import CloseIn, Cuadre, MovementIn, MovementOut, OpenIn, SessionOut

router = APIRouter(prefix="/cash", tags=["cash"], dependencies=[Depends(require_module("cash"))])
_manage = require_permission("cash:manage")


@router.post("/sessions/open", response_model=SessionOut, status_code=201, summary="409 CASH_SESSION_ALREADY_OPEN si ya hay una")
async def open_session(body: OpenIn, p: Principal = Depends(_manage), db: AsyncSession = Depends(get_db)) -> SessionOut:
    return await service.open_session(db, p, body)


@router.get("/sessions/current", response_model=SessionOut | None, summary="Caja abierta (null si no hay)")
async def current(p: Principal = Depends(_manage), db: AsyncSession = Depends(get_db)) -> SessionOut | None:
    return await service.current(db, p)


@router.get("/sessions", response_model=Page[SessionOut])
async def list_sessions(pp: PageParams = Depends(page_params), p: Principal = Depends(_manage),
                        db: AsyncSession = Depends(get_db)) -> Page[SessionOut]:
    return await service.list_sessions(db, p, pp)


@router.get("/sessions/{session_id}/cuadre", response_model=Cuadre, summary="Esperado vs contado, por método y movimientos")
async def cuadre(session_id: UUID, p: Principal = Depends(_manage), db: AsyncSession = Depends(get_db)) -> Cuadre:
    return await service.cuadre(db, p, session_id)


@router.post("/sessions/{session_id}/close", response_model=Cuadre, summary="Cierra la caja y congela el cuadre")
async def close_session(session_id: UUID, body: CloseIn, p: Principal = Depends(_manage),
                        db: AsyncSession = Depends(get_db)) -> Cuadre:
    return await service.close_session(db, p, session_id, body)


@router.post("/movements", response_model=MovementOut, status_code=201, summary="Ingreso o egreso en la caja abierta")
async def add_movement(body: MovementIn, p: Principal = Depends(_manage), db: AsyncSession = Depends(get_db)) -> MovementOut:
    return await service.add_movement(db, p, body)
