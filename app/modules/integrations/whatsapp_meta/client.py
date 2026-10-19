"""WhatsApp Cloud API (Meta): envío, validación de credenciales, firma y lectura del webhook.

Todo lo que no es red (firma, parser, partir mensajes largos) es puro y está probado sin Meta.
"""
import hashlib
import hmac
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.config import get_settings

MAX_TEXT = 4096                    # límite de WhatsApp por mensaje de texto


class MetaError(Exception):
    def __init__(self, status: int, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.status, self.message, self.code = status, message, code


# ---------------------------------------------------------------- firma del webhook (puro)
def verify_signature(app_secret: str, raw_body: bytes, header: str | None) -> bool:
    """Meta firma el cuerpo EXACTO con HMAC-SHA256 y la clave secreta de la app: `X-Hub-Signature-256: sha256=<hex>`."""
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def sign(app_secret: str, raw_body: bytes) -> str:
    """Solo para pruebas y simulaciones locales."""
    return "sha256=" + hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------- lectura del webhook (puro)
@dataclass(frozen=True)
class Inbound:
    phone_number_id: str
    wa_id: str                  # teléfono del cliente en formato internacional, sin "+"
    wamid: str
    tipo: str
    texto: str | None
    nombre: str | None


@dataclass(frozen=True)
class StatusUpdate:
    phone_number_id: str
    wamid: str
    estado: str                 # sent | delivered | read | failed
    error: str | None = None


@dataclass
class Parsed:
    mensajes: list[Inbound] = field(default_factory=list)
    estados: list[StatusUpdate] = field(default_factory=list)


def _items(x: Any) -> list[dict[str, Any]]:
    """Solo los elementos que son objetos: cualquier otra cosa (texto, null, números) se ignora."""
    return [i for i in x if isinstance(i, dict)] if isinstance(x, list) else []


def parse_webhook(payload: Any) -> Parsed:
    """Tolerante: cualquier forma inesperada se ignora (Meta cambia payloads y espera siempre un 200)."""
    out = Parsed()
    if not isinstance(payload, dict) or payload.get("object") != "whatsapp_business_account":
        return out
    for entry in _items(payload.get("entry")):
        for change in _items(entry.get("changes")):
            value = change.get("value")
            if not isinstance(value, dict):
                continue
            pid = str((value.get("metadata") or {}).get("phone_number_id") or "") if isinstance(value.get("metadata"), dict) else ""
            if not pid:
                continue
            names = {str(c.get("wa_id")): (c.get("profile") or {}).get("name") for c in _items(value.get("contacts")) if isinstance(c.get("profile") or {}, dict)}
            for m in _items(value.get("messages")):
                if not m.get("id") or not m.get("from"):
                    continue
                tipo = str(m.get("type") or "unknown")
                texto = ((m.get("text") or {}).get("body") if tipo == "text" else None)
                if tipo == "button":
                    texto = (m.get("button") or {}).get("text")
                if tipo == "interactive":
                    it = m.get("interactive") or {}
                    texto = ((it.get("button_reply") or it.get("list_reply") or {}).get("title"))
                out.mensajes.append(Inbound(pid, str(m["from"]), str(m["id"]), tipo, texto, names.get(str(m["from"]))))
            for st in _items(value.get("statuses")):
                if st.get("id") and st.get("status"):
                    errs = st.get("errors") if isinstance(st.get("errors"), list) else []
                    out.estados.append(StatusUpdate(pid, str(st["id"]), str(st["status"]),
                                                    str(errs[0].get("title") or errs[0].get("message")) if errs and isinstance(errs[0], dict) else None))
    return out


def split_text(text: str, limit: int = MAX_TEXT) -> list[str]:
    """Parte un texto largo en varios mensajes, cortando en saltos de línea o espacios."""
    text = text.strip()
    parts: list[str] = []
    while len(text) > limit:
        cut = max(text.rfind("\n", 0, limit), text.rfind(" ", 0, limit))
        cut = cut if cut > limit // 2 else limit
        parts.append(text[:cut].strip())
        text = text[cut:].strip()
    return [*parts, text] if text else parts


# ---------------------------------------------------------------- cliente HTTP
class MetaClient:
    def __init__(self, phone_number_id: str, access_token: str, http: httpx.AsyncClient | None = None) -> None:
        self.phone_number_id = phone_number_id
        self._token = access_token
        self._http = http
        self._base = f"https://graph.facebook.com/{get_settings().meta_graph_version}"

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        return await self._call("POST", path, json=body)

    async def _call(self, method: str, path: str, **kw: Any) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._token}"}
        try:
            if self._http:
                r = await self._http.request(method, f"{self._base}{path}", headers=headers, **kw)
            else:
                async with httpx.AsyncClient(timeout=15) as c:
                    r = await c.request(method, f"{self._base}{path}", headers=headers, **kw)
        except httpx.HTTPError as e:                      # red caída, timeout...
            raise MetaError(0, f"No se pudo contactar a Meta ({type(e).__name__})") from e
        data = r.json() if r.content else {}
        if r.status_code >= 400:
            err = (data.get("error") or {}) if isinstance(data, dict) else {}
            raise MetaError(r.status_code, str(err.get("message") or "Error de Meta"), err.get("code"))
        return data if isinstance(data, dict) else {}

    async def send_text(self, to: str, body: str) -> str:
        """Devuelve el wamid del mensaje enviado."""
        data = await self._post(f"/{self.phone_number_id}/messages", {
            "messaging_product": "whatsapp", "recipient_type": "individual", "to": to, "type": "text",
            "text": {"preview_url": False, "body": body[:MAX_TEXT]}})
        return str(((data.get("messages") or [{}])[0]).get("id") or "")

    async def send_template(self, to: str, name: str, params: list[str], lang: str = "es") -> str:
        comps = [{"type": "body", "parameters": [{"type": "text", "text": p[:1000]} for p in params]}] if params else []
        data = await self._post(f"/{self.phone_number_id}/messages", {
            "messaging_product": "whatsapp", "to": to, "type": "template",
            "template": {"name": name, "language": {"code": lang}, "components": comps}})
        return str(((data.get("messages") or [{}])[0]).get("id") or "")

    async def phone_info(self) -> dict[str, Any]:
        """Valida token + número: Meta responde 200 con los datos del número, o 4xx si algo está mal."""
        return await self._call("GET", f"/{self.phone_number_id}", params={"fields": "display_phone_number,verified_name,quality_rating"})
