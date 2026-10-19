import os

# Antes de importar app.*: Settings se lee una sola vez.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://agenda_app:agenda_app_dev@localhost:5432/agenda")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("JWT_SECRET", "test-secret-test-secret-test-secret-32b")
# Clave FIJA solo para pruebas (no es secreta): así los datos cifrados que queden de una corrida siguen siendo legibles en la siguiente.
os.environ.setdefault("FERNET_KEY", "Y2xhdmUtZGUtcHJ1ZWJhcy1uby1lcy1zZWNyZXRhISE=")
os.environ.setdefault("ENVIRONMENT", "test")


import pytest  # noqa: E402  (va después de fijar las variables de entorno)


@pytest.fixture(autouse=True)
def _limitador_limpio_entre_pruebas():
    """Todas las pruebas comparten una sola IP simulada: sin esto, los límites de login (10 y 5 por minuto) se agotan entre pruebas y fallan al azar.
    Solo se vacía el contador en memoria ENTRE pruebas; los límites de la aplicación no cambian y cada prueba de límites los sigue ejerciendo."""
    from app.core import ratelimit
    ratelimit._memory._hits.clear()
    yield
