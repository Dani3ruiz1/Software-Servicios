from functools import lru_cache
from typing import Literal

from cryptography.fernet import Fernet
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

_WEAK_WORDS = ("cambia", "change", "secret-test", "e2e-secret", "dev", "test", "password", "123456")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    database_url: str
    redis_url: str
    jwt_secret: str
    jwt_access_minutes: int = 15
    jwt_refresh_days: int = 14
    fernet_key: str = ""                  # una o varias claves separadas por coma: la PRIMERA cifra, todas sirven para descifrar (rotación)
    cors_origins: str = ""
    default_timezone: str = "America/Bogota"
    login_max_attempts: int = 5
    subscription_grace_days: int = 0      # días de cortesía tras fecha_fin antes de bloquear
    impersonation_minutes: int = 30
    privacy_policy_version: str = "v1"   # versión del texto de consentimiento (Ley 1581)
    public_base_url: str = "http://localhost:3000"     # frontend: arma los enlaces de reserva que se envían por WhatsApp/correo
    api_public_url: str = "http://localhost:8000"      # esta API: se muestra la URL del webhook a configurar en Meta
    docs_enabled: bool | None = None      # /docs y /openapi.json. Por defecto: sí, salvo en producción
    max_body_bytes: int = 2_000_000       # tamaño máximo de una petición (el webhook de Meta tiene su propio tope)
    # --- Base de datos
    db_pool_size: int = 10
    db_max_overflow: int = 10
    db_pool_timeout_s: int = 30
    db_statement_timeout_ms: int = 30_000  # ninguna consulta puede colgar un worker más de este tiempo
    # --- WhatsApp (Meta Cloud API)
    meta_graph_version: str = "v21.0"
    whatsapp_window_hours: int = 24                    # fuera de esta ventana solo se puede escribir con plantillas aprobadas
    # --- IA (Claude). La clave de plataforma paga los mensajes que el plan incluye; el negocio puede traer la suya.
    anthropic_api_key: str = ""
    ai_model: str = "claude-opus-5-5"
    ai_effort: str = "medium"
    ai_max_tokens: int = 1024
    ai_history_messages: int = 14
    ai_max_tool_rounds: int = 5
    ai_refusal_fallback: bool = True                   # si el modelo rechaza por seguridad, la API reintenta en otro modelo
    ai_fallback_model: str = "claude-opus-4-8"
    # --- Correo (SMTP). Vacío = el canal de correo queda deshabilitado y las notificaciones por correo fallan con aviso claro.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def fernet_keys(self) -> list[str]:
        return [k.strip() for k in self.fernet_key.split(",") if k.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def docs_on(self) -> bool:
        return (not self.is_production) if self.docs_enabled is None else self.docs_enabled

    @model_validator(mode="after")
    def _production_safety(self) -> "Settings":
        """En producción la API NO arranca con una configuración insegura: es mejor un error claro al desplegar que una brecha después."""
        if not self.is_production:
            return self
        problems: list[str] = []
        if len(self.jwt_secret) < 32 or any(w in self.jwt_secret.lower() for w in _WEAK_WORDS):
            problems.append("JWT_SECRET debe tener 32+ caracteres aleatorios (genera uno con: python -c \"import secrets;print(secrets.token_urlsafe(48))\")")
        if not self.fernet_keys:
            problems.append("FERNET_KEY es obligatoria en producción (cifra las credenciales de Meta/IA/facturación)")
        for k in self.fernet_keys:
            try:
                Fernet(k.encode())
            except (ValueError, TypeError):
                problems.append("FERNET_KEY contiene una clave inválida (genera con: python -c \"from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())\")")
                break
        if not self.cors_list or "*" in self.cors_list or any(not o.startswith("https://") for o in self.cors_list):
            problems.append("CORS_ORIGINS debe listar los orígenes del frontend con https:// (sin *)")
        url = make_url(self.database_url)
        if url.username in ("postgres", "root", "admin") or (url.password or "") in ("", "agenda_app_dev", "postgres", "password"):
            problems.append("DATABASE_URL debe usar el rol sin privilegios `agenda_app` con contraseña propia: un superusuario ignora RLS y rompe el aislamiento entre negocios")
        for name, v in (("PUBLIC_BASE_URL", self.public_base_url), ("API_PUBLIC_URL", self.api_public_url)):
            if not v.startswith("https://"):
                problems.append(f"{name} debe empezar con https://")
        if problems:
            raise ValueError("Configuración insegura para producción:\n - " + "\n - ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
