"""Rotación de la clave de cifrado (FERNET_KEY). Re-cifra TODO lo que está cifrado en la BD con la clave primaria actual.

Procedimiento (sin interrupción del servicio):
  1. Genera una clave nueva y ponla PRIMERA:  FERNET_KEY=<nueva>,<vieja>   (la API ya cifra con la nueva y descifra con ambas)
  2. Reinicia api / worker / beat.
  3. Ejecuta:  python -m app.scripts.rotate_keys          (puedes repetirlo: es idempotente)
  4. Si no lista valores ilegibles (salida 0), retira la clave vieja:  FERNET_KEY=<nueva>   y reinicia.
"""
import asyncio

from cryptography.fernet import InvalidToken
from sqlalchemy import text

from app.core.db import dispose_engine, scoped_session
from app.core.security import rotate_secret

# (tabla, columna) que guardan datos cifrados con Fernet
TARGETS = [("integrations", "credenciales_cifradas"), ("users", "totp_secret"), ("platform_users", "totp_secret")]


async def rotate() -> dict[str, list[str]]:
    """Re-cifra cada valor por separado. Uno ilegible (clave equivocada, dato corrupto) NO aborta el resto: se informa y la salida es de error."""
    out: dict[str, list[str]] = {"ok": [], "fallidos": []}
    async with scoped_session(platform=True) as db:
        for tabla, col in TARGETS:
            rows = (await db.execute(text(f"SELECT id, {col} AS v FROM {tabla} WHERE {col} IS NOT NULL"))).all()   # noqa: S608 - lista fija arriba
            for r in rows:
                try:
                    nuevo = rotate_secret(r.v)
                except InvalidToken:
                    out["fallidos"].append(f"{tabla}.{col}:{r.id}")
                    continue
                await db.execute(text(f"UPDATE {tabla} SET {col} = :v WHERE id = :i"), {"v": nuevo, "i": r.id})  # noqa: S608
                out["ok"].append(f"{tabla}.{col}:{r.id}")
    return out


def main() -> int:
    async def go() -> dict[str, list[str]]:
        try:
            return await rotate()
        finally:
            await dispose_engine()
    res = asyncio.run(go())
    print(f"re-cifrados: {len(res['ok'])}")
    for f in res["fallidos"]:
        print(f"NO SE PUDO LEER (revisa la clave o el dato): {f}")
    return 1 if res["fallidos"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
