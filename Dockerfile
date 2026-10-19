FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
# Cliente de PostgreSQL: lo usan los scripts de respaldo y de inicialización (pg_dump, psql)
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 app
WORKDIR /srv

COPY pyproject.toml ./
COPY app ./app
# INSTALL_DEV=true (compose de desarrollo): instala en modo editable con pytest/ruff/mypy. Producción: solo lo necesario.
ARG INSTALL_DEV=false
RUN if [ "$INSTALL_DEV" = "true" ]; then pip install -e ".[dev]"; else pip install .; fi

COPY alembic.ini ./
COPY alembic ./alembic
COPY sql ./sql
COPY scripts ./scripts
COPY tests ./tests
RUN chown -R app:app /srv
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status == 200 else 1)"
# --proxy-headers: la IP real del cliente viene del proxy inverso (necesario para límites de peticiones y auditoría).
# FORWARDED_ALLOW_IPS debe ser la IP/red del proxy (Caddy); con "*" cualquiera podría falsificar su IP.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips=\"${FORWARDED_ALLOW_IPS:-127.0.0.1}\""]
