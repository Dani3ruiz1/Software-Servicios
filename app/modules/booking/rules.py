"""Reglas puras del motor público: token firmado, teléfono y validación del formulario. Sin BD."""
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import jwt

from app.core.config import get_settings
from app.core.errors import AppError

# `typ` distinto de "access": un token de reserva NUNCA sirve como sesión
TOKEN_TYP = "booking"  # noqa: S105 - valor del claim `typ`, no una contraseña
TOKEN_GRACE = timedelta(days=1)          # el enlace sigue sirviendo para consultar 1 día después de la cita
MAX_ACTIVE_PER_PHONE = 3
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


# ---------------------------------------------------------------- token firmado
@dataclass(frozen=True)
class BookingClaims:
    appointment_id: UUID
    tenant_id: UUID
    inicio_ts: int


def make_token(appointment_id: UUID, tenant_id: UUID, inicio: datetime) -> str:
    """Firmado (HS256). Lleva el inicio de la cita: si se reprograma, el enlace viejo queda inválido."""
    claims = {"typ": TOKEN_TYP, "sub": str(appointment_id), "tid": str(tenant_id), "ini": int(inicio.timestamp()),
              "exp": inicio.astimezone(UTC) + TOKEN_GRACE}
    return jwt.encode(claims, get_settings().jwt_secret, algorithm="HS256")


def read_token(token: str) -> BookingClaims:
    try:
        c = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"], options={"require": ["exp", "sub", "tid", "ini"]})
    except jwt.ExpiredSignatureError as e:
        raise AppError("TOKEN_EXPIRED", "Este enlace venció") from e
    except jwt.InvalidTokenError as e:
        raise AppError("UNAUTHORIZED", "Enlace inválido") from e
    if c.get("typ") != TOKEN_TYP:
        raise AppError("UNAUTHORIZED", "Enlace inválido")
    return BookingClaims(UUID(c["sub"]), UUID(c["tid"]), int(c["ini"]))


# ---------------------------------------------------------------- teléfono
def normalize_phone(raw: str) -> str:
    """Solo dígitos, sin el 57 de Colombia: '+57 300-123 4567' y '3001234567' son el mismo cliente."""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 12 and digits.startswith("57"):
        digits = digits[2:]
    if not 7 <= len(digits) <= 15:
        raise AppError("VALIDATION_ERROR", "Teléfono inválido", {"campo": "cliente.telefono"})
    return digits


# ---------------------------------------------------------------- formulario personalizado
@dataclass(frozen=True)
class FieldDef:
    id: UUID
    etiqueta: str
    tipo: str
    obligatorio: bool
    opciones: list[str] | None


def clean(s: str, limit: int) -> str:
    return _CTRL.sub("", s).strip()[:limit]


def validate_answers(fields: list[FieldDef], respuestas: dict[UUID, str | bool | int | float]) -> list[tuple[str, str]]:
    """Valida contra la definición del formulario y devuelve [(etiqueta, valor)] listo para guardar en las notas."""
    known = {f.id: f for f in fields}
    unknown = set(respuestas) - set(known)
    if unknown:
        raise AppError("VALIDATION_ERROR", "Hay campos que no existen en el formulario", {"campos": sorted(map(str, unknown))})
    out: list[tuple[str, str]] = []
    for f in fields:
        v = respuestas.get(f.id)
        vacio = v is None or (isinstance(v, str) and not v.strip())
        if f.tipo == "casilla":
            if f.obligatorio and v is not True:
                raise _bad(f, "debe marcarse")
            out.append((f.etiqueta, "Sí" if v is True else "No"))
            continue
        if vacio:
            if f.obligatorio:
                raise _bad(f, "es obligatorio")
            continue
        s = str(v)
        if f.tipo == "email" and not _EMAIL.match(s.strip()):
            raise _bad(f, "no es un correo válido")
        if f.tipo == "telefono":
            normalize_phone(s)
        if f.tipo == "numero":
            try:
                float(s)
            except ValueError as e:
                raise _bad(f, "debe ser un número") from e
        if f.tipo == "fecha":
            try:
                date.fromisoformat(s.strip())
            except ValueError as e:
                raise _bad(f, "debe ser una fecha AAAA-MM-DD") from e
        if f.tipo == "seleccion" and s not in (f.opciones or []):
            raise _bad(f, "no es una opción válida")
        out.append((f.etiqueta, clean(s, 500 if f.tipo == "area_texto" else 200)))
    return out


def _bad(f: FieldDef, why: str) -> AppError:
    return AppError("VALIDATION_ERROR", f"«{f.etiqueta}» {why}", {"campo_id": str(f.id)})


def notes_from_answers(answers: list[tuple[str, str]]) -> str | None:
    text = "\n".join(f"{k}: {v}" for k, v in answers)
    return text[:1000] or None


DEFAULT_CONSENT = ("Autorizo el tratamiento de mis datos personales (nombre, teléfono, correo) para gestionar mi cita y "
                   "recibir recordatorios, conforme a la Ley 1581 de 2012. Puedo solicitar su actualización o eliminación en cualquier momento.")
