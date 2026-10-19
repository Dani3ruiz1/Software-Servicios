"""Crea (o actualiza la contraseña de) un Super Admin SIN dejar la contraseña en el historial ni en archivos.

  docker compose exec api python -m app.scripts.create_superadmin tu@correo.com "Tu Nombre"

La contraseña se pide por teclado (no se muestra). En producción este es el ÚNICO camino: el super admin de desarrollo
de `sql/seed.sql` no debe existir (la API se niega a arrancar si sigue con su contraseña por defecto).
"""
import asyncio
import getpass
import sys

from sqlalchemy import text

from app.core.db import dispose_engine, scoped_session
from app.core.security import hash_password

MIN_LEN = 14


def check_password(pw: str) -> str | None:
    """Mensaje de error si la contraseña es débil; None si sirve."""
    if len(pw) < MIN_LEN:
        return f"Mínimo {MIN_LEN} caracteres"
    if len(set(pw)) < 6:
        return "Demasiado repetitiva"
    if pw.lower() == pw or pw.upper() == pw or not any(c.isdigit() for c in pw):
        return "Usa mayúsculas, minúsculas y números"
    return None


async def upsert(email: str, nombre: str, password: str) -> bool:
    """True si creó, False si actualizó."""
    async with scoped_session(platform=True) as db:
        row = (await db.execute(text(
            "INSERT INTO platform_users (email, password_hash, nombre) VALUES (:e, :h, :n) "
            "ON CONFLICT (email) DO UPDATE SET password_hash = EXCLUDED.password_hash, nombre = EXCLUDED.nombre, "
            "intentos_fallidos = 0, bloqueado_hasta = NULL, activo = true RETURNING (xmax = 0) AS creado"),
            {"e": email.strip().lower(), "h": hash_password(password), "n": nombre})).one()
        await db.execute(text("UPDATE refresh_tokens SET revocado_en = now() WHERE sujeto_tipo = 'platform_user' AND revocado_en IS NULL "
                              "AND sujeto_id = (SELECT id FROM platform_users WHERE email = :e)"), {"e": email.strip().lower()})
    return bool(row.creado)


def main() -> int:
    if len(sys.argv) != 3 or "@" not in sys.argv[1]:
        print('Uso: python -m app.scripts.create_superadmin correo@dominio.com "Nombre Completo"')
        return 2
    pw = getpass.getpass("Contraseña nueva: ")
    if pw != getpass.getpass("Repítela: "):
        print("No coinciden.")
        return 1
    if (err := check_password(pw)):
        print(f"Contraseña débil: {err}.")
        return 1

    async def go() -> bool:
        try:
            return await upsert(sys.argv[1], sys.argv[2], pw)
        finally:
            await dispose_engine()
    print("Super admin creado." if asyncio.run(go()) else "Contraseña actualizada y sesiones anteriores cerradas.")
    print("Activa el doble factor (POST /api/v1/auth/2fa/setup) en tu primer ingreso.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
