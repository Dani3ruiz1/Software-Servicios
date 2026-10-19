from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, func
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

_TS = DateTime(timezone=True)
_MONEY = Numeric(12, 2)


class PlatformUser(Base):
    __tablename__ = "platform_users"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(CITEXT, unique=True)
    password_hash: Mapped[str]
    nombre: Mapped[str]
    activo: Mapped[bool] = mapped_column(default=True)
    totp_secret: Mapped[str | None]
    totp_habilitado: Mapped[bool] = mapped_column(default=False)
    intentos_fallidos: Mapped[int] = mapped_column(default=0)
    bloqueado_hasta: Mapped[datetime | None] = mapped_column(_TS)
    ultimo_login: Mapped[datetime | None] = mapped_column(_TS)


class Plan(Base):
    __tablename__ = "plans"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    nombre: Mapped[str]
    precio_mensual: Mapped[Decimal] = mapped_column(_MONEY)
    max_usuarios: Mapped[int]
    max_mensajes_ia_mes: Mapped[int | None]
    es_independiente: Mapped[bool] = mapped_column(default=False)
    activo: Mapped[bool] = mapped_column(default=True)


class Module(Base):
    __tablename__ = "modules"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    codigo: Mapped[str]
    nombre: Mapped[str]
    precio_mensual_base: Mapped[Decimal] = mapped_column(_MONEY)


class PlanModule(Base):
    __tablename__ = "plan_modules"
    plan_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("plans.id"), primary_key=True)
    module_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("modules.id"), primary_key=True)


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    nombre: Mapped[str]
    nit: Mapped[str | None]
    slug: Mapped[str] = mapped_column(CITEXT, unique=True)
    business_type: Mapped[str]
    modo: Mapped[str]
    zona_horaria: Mapped[str]
    moneda: Mapped[str] = mapped_column(default="COP")
    estado: Mapped[str] = mapped_column(default="activa")
    requiere_sesion_caja: Mapped[bool] = mapped_column(default=True)
    bloqueo_manual: Mapped[bool] = mapped_column(default=False)
    contacto_nombre: Mapped[str | None]
    contacto_email: Mapped[str | None] = mapped_column(CITEXT)
    contacto_telefono: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(_TS, server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(_TS)


class TenantSubscription(Base):
    __tablename__ = "tenant_subscriptions"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("tenants.id"))
    plan_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("plans.id"))
    fecha_inicio: Mapped[date] = mapped_column(Date)
    fecha_fin: Mapped[date] = mapped_column(Date)
    estado: Mapped[str] = mapped_column(default="vigente")
    precio_plan_aplicado: Mapped[Decimal] = mapped_column(_MONEY)
    notas: Mapped[str | None]


class TenantModule(Base):
    __tablename__ = "tenant_modules"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("tenants.id"))
    module_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("modules.id"))
    origen: Mapped[str]
    precio_override: Mapped[Decimal | None] = mapped_column(_MONEY)
    activo: Mapped[bool] = mapped_column(default=True)


class SubscriptionPayment(Base):
    __tablename__ = "subscription_payments"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("tenants.id"))
    monto: Mapped[Decimal] = mapped_column(_MONEY)
    fecha_pago: Mapped[date] = mapped_column(Date)
    periodo_desde: Mapped[date] = mapped_column(Date)
    periodo_hasta: Mapped[date] = mapped_column(Date)
    metodo: Mapped[str]
    referencia: Mapped[str | None]
    registrado_por: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(_TS, server_default=func.now())
