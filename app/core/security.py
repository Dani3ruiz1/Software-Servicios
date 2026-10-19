import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import jwt
import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from cryptography.fernet import Fernet, MultiFernet

from app.core.config import get_settings
from app.core.errors import AppError

_ph = PasswordHasher()


def hash_password(p: str) -> str:
    return _ph.hash(p)


def verify_password(hashed: str, p: str) -> bool:
    try:
        return _ph.verify(hashed, p)
    except (VerifyMismatchError, InvalidHashError):
        return False


DUMMY_HASH = hash_password("dummy-para-igualar-tiempos")   # evita enumerar usuarios por latencia


def create_access_token(*, sub: UUID, kind: str, tenant_id: UUID | None, rol: str | None,
                        imp: UUID | None = None, ttl_minutes: int | None = None) -> tuple[str, int]:
    """imp = id del super admin cuando es una sesión de impersonación."""
    s = get_settings()
    ttl = (ttl_minutes or s.jwt_access_minutes) * 60
    now = datetime.now(UTC)
    claims = {"sub": str(sub), "kind": kind, "tid": str(tenant_id) if tenant_id else None,
              "rol": rol, "typ": "access", "iat": now, "exp": now + timedelta(seconds=ttl)}
    if imp:
        claims["imp"] = str(imp)
    return jwt.encode(claims, s.jwt_secret, algorithm="HS256"), ttl


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        c = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"],
                       options={"require": ["exp", "sub", "kind"]})
    except jwt.ExpiredSignatureError as e:
        raise AppError("TOKEN_EXPIRED", "La sesión expiró") from e
    except jwt.InvalidTokenError as e:
        raise AppError("UNAUTHORIZED", "Token inválido") from e
    if c.get("typ") != "access":
        raise AppError("UNAUTHORIZED", "Token inválido")
    return c


def new_opaque_token() -> str:
    return secrets.token_urlsafe(48)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()   # alta entropía: SHA-256 basta (sin sal)


def _fernet() -> MultiFernet:
    """La PRIMERA clave cifra; todas sirven para descifrar. Rotar = poner la nueva primero, re-cifrar y luego retirar la vieja."""
    keys = get_settings().fernet_keys
    if not keys:
        raise RuntimeError("FERNET_KEY no configurada")
    return MultiFernet([Fernet(k.encode()) for k in keys])


def rotate_secret(token: str) -> str:
    """Re-cifra un valor con la clave primaria actual (sin exponerlo)."""
    return _fernet().rotate(token.encode()).decode()


def encrypt_secret(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_secret(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()


def verify_totp(encrypted_secret: str, code: str) -> bool:
    return pyotp.TOTP(decrypt_secret(encrypted_secret)).verify(code, valid_window=1)
