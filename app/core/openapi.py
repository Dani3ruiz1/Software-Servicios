"""Contrato OpenAPI fiel a la realidad: errores con NUESTRO formato y ejemplos de petición/respuesta en cada endpoint."""
from typing import Any

from app.core.errors import ERROR_CATALOG

DESCRIPTION = """API REST multi-negocio para agenda, ventas, caja, reservas en línea, WhatsApp con IA y facturación electrónica.

**Autenticación**: `POST /api/v1/auth/login` devuelve `access_token` (15 min) y `refresh_token` (rotativo). Envía `Authorization: Bearer <access_token>`.
**Errores**: siempre `{code, message, details}`; el frontend decide por `code` (catálogo en `docs/ERRORES.md`).
**Listas**: `?page=1&page_size=20&sort=-campo` → `{items, page, page_size, total}`.
**Fechas**: ISO-8601 con zona horaria. **Dinero**: texto decimal (p. ej. `"35000.00"`), moneda COP.
**Negocio**: sale del token, nunca de un parámetro. Las rutas `/public/{slug}/...` y los webhooks no usan token."""

ERROR_REF = {"$ref": "#/components/schemas/ErrorResponse"}


def resolve(spec: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in node:
        node = spec["components"]["schemas"][node["$ref"].rsplit("/", 1)[1]]
    return node


def example(spec: dict[str, Any], node: dict[str, Any], depth: int = 0) -> Any:
    """Valor de ejemplo válido para un esquema JSON (usa `example`/`default`/`enum` si existen)."""
    node = resolve(spec, node)
    for key in ("example", "default"):
        if key in node and node[key] is not None:
            return node[key]
    if node.get("examples"):
        return node["examples"][0]
    if "enum" in node:
        return node["enum"][0]
    if "const" in node:
        return node["const"]
    for combo in ("anyOf", "oneOf"):
        if combo in node:
            options = [o for o in node[combo] if resolve(spec, o).get("type") != "null"]
            return example(spec, options[0], depth + 1) if options else None
    if "allOf" in node:
        merged: dict[str, Any] = {}
        for part in node["allOf"]:
            v = example(spec, part, depth + 1)
            merged.update(v if isinstance(v, dict) else {})
        return merged
    t, fmt = node.get("type"), node.get("format")
    if t == "string":
        if node.get("minLength", 0) > 8:
            return "x" * node["minLength"]
        return {"uuid": "00000000-0000-0000-0000-000000000000", "date-time": "2026-10-20T10:00:00-05:00", "date": "2026-10-20",
                "time": "09:00:00", "email": "persona@correo.co"}.get(fmt or "", "texto")
    if t == "integer":
        return node.get("minimum", node.get("exclusiveMinimum", 0) + 1 if "exclusiveMinimum" in node else 0)
    if t == "number":
        return node.get("minimum", 0)
    if t == "boolean":
        return False
    if t == "array":
        return [] if depth > 3 else [example(spec, node.get("items", {}), depth + 1)]
    if t == "object" or "properties" in node:
        if depth > 4:
            return {}
        required = set(node.get("required", []))
        return {k: example(spec, v, depth + 1) for k, v in node.get("properties", {}).items() if k in required or depth == 0}
    return None


def _err(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"code": code, "message": message, "details": details or {}}


def _resp(description: str, ex: dict[str, Any]) -> dict[str, Any]:
    return {"description": description, "content": {"application/json": {"schema": ERROR_REF, "example": ex}}}


def enrich(spec: dict[str, Any]) -> dict[str, Any]:
    comps = spec.setdefault("components", {}).setdefault("schemas", {})
    for dead in ("HTTPValidationError", "ValidationError"):           # el formato por defecto de FastAPI NO es el que devolvemos
        comps.pop(dead, None)
    comps["ErrorResponse"] = {
        "type": "object", "required": ["code", "message", "details"], "title": "ErrorResponse",
        "properties": {"code": {"type": "string", "enum": sorted(ERROR_CATALOG), "description": "Código estable: decide por él, no por `message`"},
                       "message": {"type": "string", "description": "Texto en español, mostrable al usuario"},
                       "details": {"type": "object", "additionalProperties": True, "description": "Datos extra según el código"}},
        "example": _err("NOT_FOUND", "Cita no encontrado")}
    for path, ops in spec.get("paths", {}).items():
        for method, op in ops.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            body = op.get("requestBody", {}).get("content", {}).get("application/json")
            if body and "example" not in body and "examples" not in body:
                body["example"] = example(spec, body["schema"])
            res = op.setdefault("responses", {})
            for code, r in res.items():
                media = r.get("content", {}).get("application/json")
                if code.startswith("2") and media and "schema" in media and "example" not in media:
                    media["example"] = example(spec, media["schema"])
            res["422"] = _resp("Datos inválidos (`VALIDATION_ERROR`)", _err(
                "VALIDATION_ERROR", "Datos inválidos", {"campos": [{"campo": "email", "mensaje": "value is not a valid email address"}]}))
            if "security" in op:
                res["401"] = _resp("Sin token, token inválido o vencido (`UNAUTHORIZED` / `TOKEN_EXPIRED`)", _err("TOKEN_EXPIRED", "La sesión expiró"))
                res["403"] = _resp("Sin permiso o módulo no incluido (`FORBIDDEN` / `MODULE_NOT_ENABLED` / `TENANT_BLOCKED`)",
                                   _err("FORBIDDEN", "No tienes permiso para esta acción", {"permiso": "reports:read"}))
            if path.startswith("/api/v1/public/") or path in ("/api/v1/auth/login", "/api/v1/auth/platform/login", "/api/v1/auth/refresh"):
                res["429"] = _resp("Demasiadas peticiones (`RATE_LIMITED`); cabecera `Retry-After` en segundos", _err(
                    "RATE_LIMITED", "Demasiadas solicitudes. Intenta de nuevo en un momento.", {"reintentar_en_segundos": 30}))
    return spec
