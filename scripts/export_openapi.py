"""Genera los entregables de contrato para el frontend:  openapi.json · postman_collection.json · docs/ERRORES.md

  python scripts/export_openapi.py            # (re)genera los tres archivos
  python scripts/export_openapi.py --check    # falla (exit 1) si alguno quedó desactualizado: úsalo en CI

El contrato sale del propio código (nunca se edita a mano): si cambias un endpoint y no regeneras, el CI lo avisa.
"""
import json
import os
import sys
from pathlib import Path
from typing import Any

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:y@localhost/x")
os.environ.setdefault("REDIS_URL", "redis://localhost")
os.environ.setdefault("JWT_SECRET", "exportar-contrato-no-es-un-secreto-real-0123456789")
os.environ.setdefault("ENVIRONMENT", "development")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.errors import ERROR_CATALOG, ERROR_HELP  # noqa: E402
from app.core.openapi import example  # noqa: E402
from app.main import app  # noqa: E402

METHODS = ("get", "post", "put", "patch", "delete")


def postman(spec: dict[str, Any]) -> dict[str, Any]:
    folders: dict[str, list[dict[str, Any]]] = {}
    for path, ops in spec["paths"].items():
        for method in METHODS:
            op = ops.get(method)
            if not op:
                continue
            tag = (op.get("tags") or ["otros"])[0]
            url_path = path.replace("{", ":").replace("}", "")
            params = op.get("parameters", [])
            query = [{"key": q["name"], "value": str(example(spec, q.get("schema", {})) or ""), "disabled": not q.get("required", False)}
                     for q in params if q["in"] == "query"]
            variables = [{"key": q["name"], "value": str(example(spec, q.get("schema", {})))} for q in params if q["in"] == "path"]
            item: dict[str, Any] = {
                "name": op.get("summary") or f"{method.upper()} {path}",
                "request": {"method": method.upper(), "header": [], "description": (op.get("description") or "")[:2000],
                            "url": {"raw": "{{base_url}}" + url_path, "host": ["{{base_url}}"], "path": [p for p in url_path.strip("/").split("/")],
                                    "query": query, "variable": variables}},
                "response": []}
            if "security" not in op:
                item["request"]["auth"] = {"type": "noauth"}                       # rutas públicas: sin token
            body = op.get("requestBody", {}).get("content", {}).get("application/json")
            if body:
                item["request"]["header"].append({"key": "Content-Type", "value": "application/json"})
                item["request"]["body"] = {"mode": "raw", "raw": json.dumps(example(spec, body["schema"]), ensure_ascii=False, indent=2),
                                           "options": {"raw": {"language": "json"}}}
            if path.endswith(("/login", "/platform/login", "/refresh")):           # guarda los tokens para las demás peticiones
                item["event"] = [{"listen": "test", "script": {"type": "text/javascript", "exec": [
                    "const r = pm.response.json();",
                    "if (r.access_token) { pm.collectionVariables.set('access_token', r.access_token); pm.collectionVariables.set('refresh_token', r.refresh_token); }"]}}]
            for code, r in op.get("responses", {}).items():                # respuesta de ejemplo guardada en Postman
                media = r.get("content", {}).get("application/json", {})
                if code.startswith("2") and "example" in media:
                    item["response"] = [{"name": f"{code} · ejemplo", "originalRequest": item["request"], "status": r.get("description", "OK"),
                                         "code": int(code), "header": [{"key": "Content-Type", "value": "application/json"}],
                                         "body": json.dumps(media["example"], ensure_ascii=False, indent=2), "_postman_previewlanguage": "json"}]
                    break
            folders.setdefault(tag, []).append(item)
    return {"info": {"name": spec["info"]["title"], "description": "Generada desde openapi.json. Define `base_url`; haz login y el token se guarda solo.",
                     "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"},
            "auth": {"type": "bearer", "bearer": [{"key": "token", "value": "{{access_token}}", "type": "string"}]},
            "variable": [{"key": "base_url", "value": "http://localhost:8000"}, {"key": "access_token", "value": ""}, {"key": "refresh_token", "value": ""}],
            "item": [{"name": t, "item": items} for t, items in sorted(folders.items())]}


def errores_md() -> str:
    lines = ["# Catálogo de errores", "", "> Generado por `scripts/export_openapi.py`. No editar a mano.", "",
             "Todos los errores tienen la forma `{ \"code\": \"...\", \"message\": \"...\", \"details\": {} }`. El frontend decide por **`code`**, nunca por el texto de `message`.", "",
             "| Código | HTTP | Qué significa | Qué hace el frontend |", "|---|---|---|---|"]
    for code, status in ERROR_CATALOG.items():
        meaning, action = ERROR_HELP[code]
        lines.append(f"| `{code}` | {status} | {meaning} | {action} |")
    lines += ["", "Notas: `TENANT_BLOCKED` responde 402 (vencimiento) o 403 (suspensión manual); `SLOT_TAKEN` y `SLOT_UNAVAILABLE` son 409; "
              "`RATE_LIMITED` incluye la cabecera `Retry-After`.", ""]
    return "\n".join(lines)


def build() -> dict[Path, str]:
    spec = app.openapi()
    dump = lambda o: json.dumps(o, ensure_ascii=False, indent=2) + "\n"  # noqa: E731
    return {ROOT / "openapi.json": dump(spec), ROOT / "postman_collection.json": dump(postman(spec)), ROOT / "docs" / "ERRORES.md": errores_md()}


def main() -> int:
    files = build()
    if "--check" in sys.argv:
        stale = [p.name for p, text in files.items() if not p.exists() or p.read_text(encoding="utf-8") != text]
        if stale:
            print("Desactualizados:", ", ".join(stale), "\nEjecuta: python scripts/export_openapi.py")
            return 1
        print("Contrato al día.")
        return 0
    for p, text in files.items():
        p.parent.mkdir(exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
        print(f"escrito {p.relative_to(ROOT)} ({len(text):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
