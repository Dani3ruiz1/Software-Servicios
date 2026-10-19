from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


@lru_cache
def _engine() -> AsyncEngine:
    s = get_settings()
    return create_async_engine(
        s.database_url, pool_pre_ping=True, pool_size=s.db_pool_size, max_overflow=s.db_max_overflow, pool_timeout=s.db_pool_timeout_s,
        connect_args={"server_settings": {"statement_timeout": str(s.db_statement_timeout_ms), "application_name": "agenda-saas"}})


@lru_cache
def _maker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(_engine(), expire_on_commit=False)


async def dispose_engine() -> None:
    """Cierra el pool y olvida el engine. Las conexiones asyncpg pertenecen al event loop que las creó:
    cada tarea de Celery usa su propio `asyncio.run`, así que debe empezar y terminar con un pool limpio."""
    if _engine.cache_info().currsize:
        await _engine().dispose()
    _engine.cache_clear()
    _maker.cache_clear()


@asynccontextmanager
async def scoped_session(*, tenant_id: UUID | None = None, platform: bool = False) -> AsyncIterator[AsyncSession]:
    """Una transacción con el contexto RLS fijado. Commit al salir; rollback si hay excepción.

    set_config(..., true) es local a la transacción: al devolver la conexión al pool no queda
    ningún tenant "pegado" (el riesgo clásico de RLS + pool de conexiones).
    Sin tenant_id ni platform => RLS devuelve 0 filas (fail-closed).
    """
    async with _maker()() as s, s.begin():
        if tenant_id:
            await s.execute(text("SELECT set_config('app.current_tenant', :v, true)"), {"v": str(tenant_id)})
        if platform:
            await s.execute(text("SELECT set_config('app.is_platform', 'on', true)"))
        yield s
