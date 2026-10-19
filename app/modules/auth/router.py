from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.http import client_ip
from app.core.ratelimit import rate_limit
from app.core.tenancy import Principal, get_db, get_db_unrestricted, get_principal
from app.modules.auth import service
from app.modules.auth.schemas import (
    ChangePasswordIn,
    ForgotPasswordIn,
    LoginIn,
    MeOut,
    PlatformLoginIn,
    RefreshIn,
    ResetPasswordIn,
    TokenOut,
    TotpEnableIn,
    TotpSetupOut,
)

router = APIRouter(prefix="/auth", tags=["auth"])



@router.post("/login", response_model=TokenOut, dependencies=[Depends(rate_limit("login", 10, 60))], summary="Login de usuario de un negocio")
async def login(body: LoginIn, r: Request) -> TokenOut:
    return await service.login(body.tenant_slug, body.email, body.password, body.totp_code, client_ip(r))


@router.post("/platform/login", response_model=TokenOut, dependencies=[Depends(rate_limit("plogin", 5, 60))], summary="Login del Super Admin")
async def platform_login(body: PlatformLoginIn, r: Request) -> TokenOut:
    return await service.platform_login(body.email, body.password, body.totp_code, client_ip(r))


@router.post("/refresh", response_model=TokenOut, dependencies=[Depends(rate_limit("refresh", 30, 60))], summary="Rota el refresh token")
async def refresh(body: RefreshIn, r: Request) -> TokenOut:
    return await service.refresh(body.refresh_token, client_ip(r))


@router.post("/logout", status_code=204, summary="Cierra la sesión (revoca la familia de tokens)")
async def logout(body: RefreshIn) -> Response:
    await service.logout(body.refresh_token)
    return Response(status_code=204)


@router.post("/change-password", status_code=204)
async def change_password(body: ChangePasswordIn, r: Request, p: Principal = Depends(get_principal),
                          db: AsyncSession = Depends(get_db)) -> Response:
    await service.change_password(db, p, body.current_password, body.new_password, client_ip(r))
    return Response(status_code=204)


@router.post("/forgot-password", status_code=202, dependencies=[Depends(rate_limit("forgot", 5, 600))], summary="Siempre 202 (no revela si la cuenta existe)")
async def forgot_password(body: ForgotPasswordIn) -> dict[str, str]:
    await service.forgot_password(body.tenant_slug, body.email)
    return {"message": "Si la cuenta existe, enviaremos instrucciones al correo."}


@router.post("/reset-password", status_code=204, dependencies=[Depends(rate_limit("reset", 10, 600))])
async def reset_password(body: ResetPasswordIn, r: Request) -> Response:
    await service.reset_password(body.token, body.new_password, client_ip(r))
    return Response(status_code=204)


@router.post("/2fa/setup", response_model=TotpSetupOut, summary="Genera el secreto TOTP (solo admins)")
async def totp_setup(p: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)) -> TotpSetupOut:
    secret, uri = await service.totp_setup(db, p)
    return TotpSetupOut(secret=secret, otpauth_uri=uri)


@router.post("/2fa/enable", status_code=204, summary="Confirma el código y activa el 2FA")
async def totp_enable(body: TotpEnableIn, r: Request, p: Principal = Depends(get_principal),
                      db: AsyncSession = Depends(get_db)) -> Response:
    await service.totp_enable(db, p, body.code, client_ip(r))
    return Response(status_code=204)


@router.get("/me", response_model=MeOut, summary="Perfil, rol, permisos, módulos activos y estado de la suscripción (funciona aunque el negocio esté bloqueado)")
async def me(p: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db_unrestricted)) -> MeOut:
    return await service.me(db, p)
