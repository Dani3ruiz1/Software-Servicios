from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import available_timezones

from pydantic import BaseModel, EmailStr, Field, StringConstraints, field_validator

Money = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]
Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{1,48}[a-z0-9]$")]
ModuleCode = Literal["calendar", "reception", "inventory", "cash", "reports", "booking",
                     "einvoice", "whatsapp", "ai"]
Estado = Literal["activa", "por_vencer", "vencida", "suspendida", "bloqueada"]


def _tz(v: str) -> str:
    if v not in available_timezones():
        raise ValueError("Zona horaria inválida")
    return v


# ---------- Módulos y planes ----------
class ModuleOut(BaseModel):
    id: UUID
    codigo: str
    nombre: str
    precio_mensual_base: Decimal


class ModulePatch(BaseModel):
    nombre: str | None = None
    precio_mensual_base: Money | None = None


class PlanIn(BaseModel):
    nombre: str = Field(min_length=2, max_length=60)
    precio_mensual: Money
    max_usuarios: int = Field(gt=0)
    max_mensajes_ia_mes: int | None = Field(None, ge=0)
    es_independiente: bool = False
    modulos: list[ModuleCode] = []


class PlanPatch(BaseModel):
    nombre: str | None = None
    precio_mensual: Money | None = None
    max_usuarios: int | None = Field(None, gt=0)
    max_mensajes_ia_mes: int | None = Field(None, ge=0)
    activo: bool | None = None
    modulos: list[ModuleCode] | None = None


class PlanOut(BaseModel):
    id: UUID
    nombre: str
    precio_mensual: Decimal
    max_usuarios: int
    max_mensajes_ia_mes: int | None
    es_independiente: bool
    activo: bool
    modulos: list[str]


# ---------- Tenants ----------
class AdminIn(BaseModel):
    nombre: str = Field(min_length=2)
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)


class TenantCreate(BaseModel):
    nombre: str = Field(min_length=2, max_length=120)
    nit: str | None = None
    slug: Slug
    business_type: str
    plan_id: UUID
    zona_horaria: str = "America/Bogota"
    contacto_nombre: str | None = None
    contacto_email: EmailStr | None = None
    contacto_telefono: str | None = None
    fecha_inicio: date | None = None
    fecha_fin: date | None = Field(None, description="Por defecto: inicio + 30 días")
    precio_plan_aplicado: Money | None = Field(None, description="Por defecto: precio del plan")
    cargar_servicios_ejemplo: bool = True
    admin: AdminIn

    _v_tz = field_validator("zona_horaria")(_tz)


class TenantPatch(BaseModel):
    nombre: str | None = None
    nit: str | None = None
    zona_horaria: str | None = None
    contacto_nombre: str | None = None
    contacto_email: EmailStr | None = None
    contacto_telefono: str | None = None
    requiere_sesion_caja: bool | None = None

    @field_validator("zona_horaria")
    @classmethod
    def _v(cls, v: str | None) -> str | None:
        return v if v is None else _tz(v)


class TenantListItem(BaseModel):
    id: UUID
    nombre: str
    slug: str
    estado: str
    bloqueo_manual: bool
    plan: str | None
    fecha_fin: date | None
    dias_restantes: int | None
    created_at: datetime


class BillingItem(BaseModel):
    concepto: str
    tipo: Literal["plan", "modulo"]
    precio: Decimal
    origen_precio: Literal["plan", "base", "manual"]


class BillingOut(BaseModel):
    moneda: str
    items: list[BillingItem]
    total_mensual: Decimal


class SubscriptionOut(BaseModel):
    plan_id: UUID
    plan: str
    fecha_inicio: date
    fecha_fin: date
    precio_plan_aplicado: Decimal
    notas: str | None


class TenantModuleOut(BaseModel):
    codigo: str
    nombre: str
    origen: str
    activo: bool
    precio_override: Decimal | None


class TenantDetail(BaseModel):
    id: UUID
    nombre: str
    nit: str | None
    slug: str
    business_type: str
    modo: str
    zona_horaria: str
    estado: str
    bloqueo_manual: bool
    requiere_sesion_caja: bool
    contacto_nombre: str | None
    contacto_email: str | None
    contacto_telefono: str | None
    usuarios_activos: int
    suscripcion: SubscriptionOut | None
    modulos: list[TenantModuleOut]
    facturacion: BillingOut


class AssignPlanIn(BaseModel):
    plan_id: UUID
    precio_plan_aplicado: Money | None = Field(None, description="Por defecto: precio del plan")
    notas: str | None = None


class TenantModuleIn(BaseModel):
    activo: bool = True
    precio_override: Money | None = Field(None, description="Precio manual; null = precio base del módulo")


class PaymentIn(BaseModel):
    monto: Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]
    fecha_pago: date
    periodo_desde: date
    periodo_hasta: date
    metodo: str = Field(min_length=2)
    referencia: str | None = None

    @field_validator("periodo_hasta")
    @classmethod
    def _rango(cls, v: date, info) -> date:  # type: ignore[no-untyped-def]
        if "periodo_desde" in info.data and v < info.data["periodo_desde"]:
            raise ValueError("periodo_hasta debe ser >= periodo_desde")
        return v


class PaymentOut(BaseModel):
    id: UUID
    monto: Decimal
    fecha_pago: date
    periodo_desde: date
    periodo_hasta: date
    metodo: str
    referencia: str | None


class PaymentResult(BaseModel):
    pago: PaymentOut
    estado_negocio: str
    fecha_fin: date


class DashboardOut(BaseModel):
    por_estado: dict[str, int]
    activas: int
    por_vencer: int
    vencidas: int
    suspendidas_o_bloqueadas: int
    mrr: Decimal
    moneda: str = "COP"


class ImpersonationOut(BaseModel):
    access_token: str
    expires_in: int
    usuario_id: UUID
    nota: str = "Sesión de soporte auditada; no se puede renovar ni cambiar contraseñas."


# ---------- Analíticas de toda la plataforma (Super Admin) ----------
class AnalyticsSummary(BaseModel):
    negocios_total: int
    negocios_activos: int
    negocios_nuevos: int = Field(description="Negocios creados en el periodo")
    usuarios_activos: int
    clientes_nuevos: int = Field(description="Clientes registrados en el periodo, sumando todos los negocios")
    mrr: Decimal = Field(description="Ingreso mensual recurrente actual")
    reservas_creadas: int = Field(description="Reservas hechas en el periodo (por fecha en que se crearon)")
    citas_periodo: int = Field(description="Citas cuya fecha cae en el periodo (por fecha de la cita)")
    citas_atendidas: int
    tasa_no_show: float = Field(description="% de no asistencias sobre citas atendidas + no asistidas")
    tasa_cancelacion: float = Field(description="% de citas canceladas sobre las del periodo")
    reservas_online_pct: float = Field(description="% de reservas hechas por el enlace público")
    ventas_total: Decimal
    ventas_cantidad: int
    ticket_promedio: Decimal


class DayPoint(BaseModel):
    fecha: date
    reservas: int
    atendidas: int
    ventas: Decimal


class CitasPorEstado(BaseModel):
    pendiente: int
    confirmada: int
    en_atencion: int
    atendida: int
    cancelada: int
    no_asistio: int


class PlanSlice(BaseModel):
    plan: str
    negocios: int
    mrr: Decimal


class TypeSlice(BaseModel):
    tipo: str
    negocios: int


class OriginPoint(BaseModel):
    origen: str
    reservas: int


class TopTenant(BaseModel):
    nombre: str
    slug: str
    citas: int
    atendidas: int
    no_asistio: int
    ventas: Decimal


class HeatCell(BaseModel):
    dia: int = Field(description="0 = lunes … 6 = domingo")
    hora: int = Field(description="0–23, hora local del negocio")
    citas: int


class UpcomingExpiry(BaseModel):
    nombre: str
    slug: str
    estado: str
    fecha_fin: date
    dias: int = Field(description="Días que faltan (negativo = ya venció)")
    plan: str


class ThinTenant(BaseModel):
    nombre: str
    slug: str


class GrowthPoint(BaseModel):
    mes: date
    nuevos: int
    acumulado: int


class AnalyticsOut(BaseModel):
    """Todo lo que necesita el tablero del Super Admin en una sola llamada. Solo agregados: nunca datos personales."""
    desde: date
    hasta: date
    dias: int
    zona_horaria: str
    resumen: AnalyticsSummary
    serie: list[DayPoint]
    citas_por_estado: CitasPorEstado
    por_plan: list[PlanSlice]
    por_tipo: list[TypeSlice]
    por_origen: list[OriginPoint]
    top_negocios: list[TopTenant]
    mapa_calor: list[HeatCell]
    vencimientos: list[UpcomingExpiry]
    sin_actividad_total: int = Field(description="Negocios activos sin ninguna reserva nueva en 14 días")
    sin_actividad: list[ThinTenant]
    crecimiento: list[GrowthPoint]
