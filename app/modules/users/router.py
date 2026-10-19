from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import Page, PageParams, page_params
from app.core.rbac import require_permission
from app.core.tenancy import Principal, get_db
from app.modules.users import service
from app.modules.users.schemas import SeatsOut, UserIn, UserOut, UserPatch

router = APIRouter(prefix="/users", tags=["users"])
_manage = require_permission("users:manage")


@router.get("", response_model=Page[UserOut])
async def list_users(rol: str | None = None, activo: bool | None = None, pp: PageParams = Depends(page_params),
                     p: Principal = Depends(_manage), db: AsyncSession = Depends(get_db)) -> Page[UserOut]:
    return await service.list_users(db, p, pp, rol, activo)


@router.get("/seats", response_model=SeatsOut, summary="Cupos de usuarios del plan (para mostrar 3/5)")
async def seats(p: Principal = Depends(_manage), db: AsyncSession = Depends(get_db)) -> SeatsOut:
    return await service.seats(db, p)


@router.post("", response_model=UserOut, status_code=201, summary="409 USER_LIMIT_REACHED si el plan no tiene cupo")
async def create_user(body: UserIn, p: Principal = Depends(_manage), db: AsyncSession = Depends(get_db)) -> UserOut:
    return await service.create_user(db, p, body)


@router.patch("/{user_id}", response_model=UserOut)
async def patch_user(user_id: UUID, body: UserPatch, p: Principal = Depends(_manage),
                     db: AsyncSession = Depends(get_db)) -> UserOut:
    return await service.patch_user(db, p, user_id, body)


@router.delete("/{user_id}", status_code=204, summary="Borrado lógico; libera el cupo")
async def delete_user(user_id: UUID, p: Principal = Depends(_manage), db: AsyncSession = Depends(get_db)) -> Response:
    await service.delete_user(db, p, user_id)
    return Response(status_code=204)
