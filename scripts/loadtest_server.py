"""Servidor SOLO PARA PRUEBAS DE CARGA: la API real, con el límite de peticiones por IP desactivado en este proceso.
NUNCA lo uses en un servidor expuesto: sin ese límite cualquiera podría saturar el login o las reservas.

    python scripts/loadtest_server.py [puerto]      # por defecto 8001
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn  # noqa: E402

import app.core.ratelimit as rl  # noqa: E402


async def _sin_limite(key: str, window: int) -> tuple[int, int]:
    return 1, window


rl.hit = _sin_limite  # type: ignore[assignment]   # las dependencias llaman a `hit` por nombre de módulo: queda parchado solo aquí

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 8001, log_level="warning")
