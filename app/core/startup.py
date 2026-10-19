"""Verificaciones al arrancar y salud del servicio.

En PRODUCCIÓN las verificaciones críticas detienen el arranque (mejor un contenedor que no sube que una brecha silenciosa);
en desarrollo solo avisan en el log.
"""
import asyncio
import logging

from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import scoped_session
from app.core.security import verify_password

log = logging.getLogger("app.startup")

# Credenciales de DESARROLLO sembradas por sql/seed.sql. No deben existir jamás en producción.
DEV_ADMIN_EMAIL = "superadmin@agenda-saas.co"
DEV_ADMIN_PASSWORD = "Cambiar.Ya.123"  # noqa: S105 - es la contraseña de desarrollo, justamente lo que se detecta


async def startup_checks() -> list[str]:
    """Devuelve los problemas encontrados; en producción, cualquiera aborta el arranque."""
    s = get_settings()
    problems: list[str] = []
    try:
        async with scoped_session(platform=True) as db:
            role = (await db.execute(text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"))).one()
            if role.rolsuper or role.rolbypassrls:
                problems.append("El rol de BD de la API es superusuario o BYPASSRLS: el aislamiento entre negocios (RLS) NO se está aplicando")
            h = (await db.execute(text("SELECT password_hash FROM platform_users WHERE email = :e"), {"e": DEV_ADMIN_EMAIL})).scalar()
            if h and verify_password(h, DEV_ADMIN_PASSWORD):
                problems.append(f"Existe el super admin de desarrollo ({DEV_ADMIN_EMAIL}) con su contraseña por defecto: crea el tuyo y elimínalo")
    except Exception as e:                                             # noqa: BLE001 - sin BD no se puede verificar nada
        if s.is_production:
            raise RuntimeError(f"No se pudo verificar la base de datos al arrancar: {type(e).__name__}") from e
        log.warning("verificacion_de_arranque_omitida", extra={"error": type(e).__name__})
        return []
    for p in problems:
        log.critical("arranque_inseguro", extra={"problema": p}) if s.is_production else log.warning("arranque_inseguro", extra={"problema": p})
    if problems and s.is_production:
        raise RuntimeError("Arranque abortado por configuración insegura:\n - " + "\n - ".join(problems))
    return problems


async def readiness() -> tuple[bool, dict[str, bool]]:
    """(listo, detalle). La BD es imprescindible; Redis no (el limitador cae a memoria), pero se informa."""
    db_ok = redis_ok = False
    try:
        async with scoped_session(platform=True) as db:
            await asyncio.wait_for(db.execute(text("SELECT 1")), timeout=2)
        db_ok = True
    except Exception as e:                                             # noqa: BLE001
        log.warning("salud_bd_fallo", extra={"error": type(e).__name__})
    try:
        from app.core.ratelimit import _redis
        await asyncio.wait_for(_redis().ping(), timeout=1)
        redis_ok = True
    except Exception as e:                                             # noqa: BLE001
        log.info("salud_redis_fallo", extra={"error": type(e).__name__})
    return db_ok, {"db": db_ok, "redis": redis_ok}
