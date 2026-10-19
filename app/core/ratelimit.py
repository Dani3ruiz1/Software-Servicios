"""Limitador de peticiones (ventana fija) para endpoints públicos.

Usa Redis (contador compartido entre procesos). Si Redis no responde, cae a un contador en memoria del proceso:
la protección se degrada (por proceso, no global) pero las reservas NO se caen por un fallo de Redis.
"""
import logging
import time
from collections.abc import Callable
from functools import lru_cache

from fastapi import Depends, Request

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.http import client_ip

log = logging.getLogger("app.ratelimit")


class MemoryCounter:
    """Ventana fija en memoria. `clock` inyectable para probar sin dormir."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock, self._hits = clock, {}      # type: ignore[var-annotated]

    def hit(self, key: str, window: int) -> tuple[int, int]:
        """Devuelve (golpes en la ventana actual, segundos hasta que se reinicie)."""
        now = self._clock()
        count, start = self._hits.get(key, (0, now))
        if now - start >= window:
            count, start = 0, now
        count += 1
        self._hits[key] = (count, start)
        if len(self._hits) > 50_000:             # evita crecer sin límite ante un ataque con muchas IPs
            self._hits = {k: v for k, v in self._hits.items() if now - v[1] < window}
        return count, max(int(window - (now - start)), 1)


_memory = MemoryCounter()


@lru_cache
def _redis():  # type: ignore[no-untyped-def]
    import redis.asyncio as aioredis
    return aioredis.from_url(get_settings().redis_url, socket_timeout=0.5, socket_connect_timeout=0.5)


_redis_down_until = 0.0
REDIS_RETRY_S = 30.0


async def hit(key: str, window: int) -> tuple[int, int]:
    global _redis_down_until
    if time.monotonic() < _redis_down_until:      # Redis falló hace poco: no hacer esperar a cada petición
        return _memory.hit(key, window)
    try:
        r = _redis()
        pipe = r.pipeline()
        pipe.incr(f"rl:{key}")
        pipe.expire(f"rl:{key}", window, nx=True)
        pipe.ttl(f"rl:{key}")
        count, _, ttl = await pipe.execute()
        return int(count), max(int(ttl), 1)
    except Exception as e:                       # noqa: BLE001 - cualquier fallo de Redis degrada, no tumba
        _redis_down_until = time.monotonic() + REDIS_RETRY_S
        log.warning("redis_no_disponible_usando_memoria", extra={"error": type(e).__name__, "reintento_en_s": REDIS_RETRY_S})
        return _memory.hit(key, window)


def rate_limit(name: str, limit: int, window_s: int) -> Callable[..., object]:
    """Dependencia: `limit` peticiones por IP (y por slug, si la ruta lo trae) cada `window_s` segundos."""
    async def dep(request: Request, ip: str | None = Depends(client_ip)) -> None:
        slug = request.path_params.get("slug", "-")
        count, retry = await hit(f"{name}:{slug}:{ip or 'sin-ip'}", window_s)
        if count > limit:
            raise AppError("RATE_LIMITED", "Demasiadas solicitudes. Intenta de nuevo en un momento.",
                           {"reintentar_en_segundos": retry}, headers={"Retry-After": str(retry)})
    return dep
