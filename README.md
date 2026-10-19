# Agenda SaaS — API para negocios de citas

API REST multi-negocio (barberías, spas, uñas, estética): agenda y disponibilidad, ventas y caja, reportes, reservas públicas en línea, WhatsApp con asistente de IA y facturación electrónica (DIAN, proveedor simulado). Solo backend y base de datos; el frontend lo hace otra persona con el contrato de abajo.

**Stack**: Python 3.12 · FastAPI · Pydantic v2 · SQLAlchemy 2.0 async · Alembic · PostgreSQL 16 (RLS) · Redis + Celery · JWT + argon2 · Docker Compose.

## Arranque en desarrollo

```bash
cp .env.example .env
docker compose up --build        # api, postgres, pgadmin, redis, worker, beat
```

- Docs interactivas: http://localhost:8000/docs (solo con `DOCS_ENABLED=true`)
- Super admin de desarrollo (solo semilla de dev): `superadmin@agenda-saas.co` — **no existe en producción**.
- Pruebas: `pytest` (las que necesitan Postgres se omiten solas si no hay base; ver `docs/ARQUITECTURA.md`).
- Calidad: `ruff check app tests scripts` · `mypy app` · `python scripts/export_openapi.py --check`.

## Documentos

| Documento | Para quién |
|---|---|
| [`docs/CONTRATO_FRONTEND.md`](docs/CONTRATO_FRONTEND.md) | Quien hace el frontend: login, errores, paginación, permisos, reservas públicas |
| [`openapi.json`](openapi.json) · [`postman_collection.json`](postman_collection.json) | Contrato exacto (con ejemplos) |
| [`docs/ERRORES.md`](docs/ERRORES.md) | Catálogo de códigos de error |
| [`docs/OPERACION.md`](docs/OPERACION.md) | Despliegue, respaldos, rotación de claves, runbooks |
| [`docs/ARQUITECTURA.md`](docs/ARQUITECTURA.md) | Diseño por fase, decisiones y riesgos |
| [`docs/DECISIONES_PENDIENTES.md`](docs/DECISIONES_PENDIENTES.md) | Lo que tienes que decidir tú antes de salir a producción |

## Seguridad en una línea

Un solo esquema con `tenant_id` en todo y **RLS forzada en PostgreSQL**; la API se conecta con un rol sin privilegios (`agenda_app`) y el negocio sale siempre del token firmado, nunca de un parámetro. En producción la API **se niega a arrancar** con configuración insegura.
