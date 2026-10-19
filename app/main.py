import logging
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import get_settings
from app.core.db import dispose_engine
from app.core.errors import ERROR_CATALOG, install_error_handlers
from app.core.logging import request_id_var, setup_logging
from app.core.openapi import DESCRIPTION, enrich
from app.core.startup import readiness, startup_checks
from app.modules.appointments.router import router as appointments_router
from app.modules.auth.router import router as auth_router
from app.modules.booking.router import public_router as public_booking_router
from app.modules.booking.router import router as booking_router
from app.modules.cash.router import router as cash_router
from app.modules.catalog.router import router as catalog_router
from app.modules.customers.router import router as customers_router
from app.modules.integrations.einvoice.router import router as einvoice_router
from app.modules.integrations.router import router as integrations_router
from app.modules.integrations.router import webhook_router
from app.modules.inventory.router import router as inventory_router
from app.modules.platform.router import router as platform_router
from app.modules.reports.router import router as reports_router
from app.modules.sales.router import router as sales_router
from app.modules.staff.router import router as staff_router
from app.modules.users.router import router as users_router

settings = get_settings()
setup_logging()
access_log = logging.getLogger("app.access")
_RID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await startup_checks()                       # en producción aborta si la configuración es insegura
    yield
    await dispose_engine()


app = FastAPI(title="Agenda SaaS API", version="1.0.0", description=DESCRIPTION, lifespan=lifespan,
              docs_url="/docs" if settings.docs_on else None, redoc_url=None,
              openapi_url="/openapi.json" if settings.docs_on else None)
install_error_handlers(app)
app.add_middleware(
    CORSMiddleware, allow_origins=settings.cors_list, allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"], expose_headers=["X-Request-ID", "Retry-After"],
)

_SECURITY_HEADERS = {"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
                     "Permissions-Policy": "geolocation=(), microphone=(), camera=()", "Cache-Control": "no-store"}


@app.middleware("http")
async def hardening(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """Id de petición (solo si es razonable: evita inyectar basura en los logs), tope de tamaño, cabeceras de seguridad y log de acceso."""
    inbound = request.headers.get("X-Request-ID", "")
    rid = inbound if _RID.match(inbound) else uuid.uuid4().hex
    request_id_var.set(rid)
    t0 = time.perf_counter()
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > settings.max_body_bytes:
        resp: Response = JSONResponse({"code": "VALIDATION_ERROR", "message": "La petición es demasiado grande", "details": {}}, status_code=413)
    else:
        resp = await call_next(request)
    resp.headers["X-Request-ID"] = rid
    for k, v in _SECURITY_HEADERS.items():
        resp.headers.setdefault(k, v)
    if settings.is_production:
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    route = request.scope.get("route")
    path = getattr(route, "path", None) or "(sin ruta)"        # la PLANTILLA de la ruta, nunca la URL real: hay tokens en algunas URLs
    if not path.startswith("/health"):
        access_log.info("http", extra={"metodo": request.method, "ruta": path, "estado": resp.status_code,
                                       "ms": round((time.perf_counter() - t0) * 1000, 1)})
    return resp


@app.get("/health", tags=["health"], summary="Liveness: el proceso responde")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"], summary="Readiness: 503 si la base de datos no responde (Redis solo se informa)")
async def health_ready() -> JSONResponse:
    ok, detail = await readiness()
    return JSONResponse({"status": "ok" if ok and detail["redis"] else "degraded" if ok else "down", **detail}, status_code=200 if ok else 503)


api = APIRouter(prefix="/api/v1")
api.include_router(auth_router)
api.include_router(platform_router)
for r in (users_router, staff_router, customers_router, catalog_router, appointments_router, sales_router, cash_router,
          reports_router, booking_router, public_booking_router, integrations_router, webhook_router, einvoice_router, inventory_router):
    api.include_router(r)


@api.get("/meta/error-codes", tags=["meta"], summary="Catálogo de códigos de error")
async def error_codes() -> dict[str, int]:
    return ERROR_CATALOG


app.include_router(api)

# Interfaz web de demostración (reserva pública + panel del negocio). Solo fuera de producción: en producción la sirve el frontend real.
_WEB = Path(__file__).resolve().parents[1] / "web"
if _WEB.is_dir() and not settings.is_production:
    app.mount("/app", StaticFiles(directory=_WEB, html=True), name="web")
    _SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,48}[a-z0-9]$")

    @app.get("/r/{slug}", include_in_schema=False)
    async def enlace_corto(slug: str) -> Response:
        """Enlace corto para compartir (`/r/mi-negocio`): lleva a la página de reservas. Solo acepta direcciones con forma válida (sin redirecciones abiertas)."""
        if not _SLUG.match(slug):
            return JSONResponse({"code": "NOT_FOUND", "message": "Dirección no encontrada", "details": {}}, status_code=404)
        return RedirectResponse(f"/app/?n={slug}", status_code=307)


def custom_openapi() -> dict[str, Any]:
    """OpenAPI con nuestro formato de errores y ejemplos en cada endpoint (se calcula una vez)."""
    if app.openapi_schema is None:
        app.openapi_schema = enrich(get_openapi(title=app.title, version=app.version, description=app.description, routes=app.routes))
    return app.openapi_schema


app.openapi = custom_openapi  # type: ignore[method-assign]
