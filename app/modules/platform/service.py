"""Super Admin: tenants, planes, módulos, precios manuales, pagos, vencimientos, impersonación."""
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import and_, delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.pagination import Page, PageParams, parse_sort
from app.core.security import create_access_token, hash_password
from app.core.tenancy import Principal
from app.modules.audit import service as audit
from app.modules.platform.models import (
    Module,
    Plan,
    PlanModule,
    SubscriptionPayment,
    Tenant,
    TenantModule,
    TenantSubscription,
)
from app.modules.platform.schemas import (
    AssignPlanIn,
    BillingItem,
    BillingOut,
    DashboardOut,
    ImpersonationOut,
    ModuleOut,
    ModulePatch,
    PaymentIn,
    PaymentOut,
    PaymentResult,
    PlanIn,
    PlanOut,
    PlanPatch,
    SubscriptionOut,
    TenantCreate,
    TenantDetail,
    TenantListItem,
    TenantModuleIn,
    TenantModuleOut,
    TenantPatch,
)
from app.modules.users.models import User

REMINDER_DAYS = (7, 3)


# ---------------------------------------------------------------- reglas puras (testeables)
def estado_por_dias(dias_restantes: int, grace_days: int) -> str:
    """dias_restantes = fecha_fin - hoy. Negativo = ya venció."""
    if dias_restantes + grace_days < 0:
        return "bloqueada"
    if dias_restantes < 0:
        return "vencida"
    if dias_restantes <= REMINDER_DAYS[0]:
        return "por_vencer"
    return "activa"


def compute_billing(plan: str | None, plan_price: Decimal,
                    extras: list[tuple[str, Decimal, Decimal | None]], moneda: str = "COP") -> BillingOut:
    """Regla 2: plan + módulos adicionales (precio_override si existe; si no, el base)."""
    items = [BillingItem(concepto=f"Plan {plan}", tipo="plan", precio=plan_price, origen_precio="plan")] if plan else []
    for nombre, base, override in extras:
        items.append(BillingItem(concepto=nombre, tipo="modulo", precio=base if override is None else override,
                                 origen_precio="base" if override is None else "manual"))
    return BillingOut(moneda=moneda, items=items, total_mensual=sum((i.precio for i in items), Decimal("0")))


def _today(tz: str) -> date:
    return datetime.now(ZoneInfo(tz)).date()


def _aud(db: AsyncSession, p: Principal, accion: str, entidad: str, entidad_id: UUID | None, tenant_id: UUID | None,
         ip: str | None, antes: dict | None = None, despues: dict | None = None) -> None:  # type: ignore[type-arg]
    audit.record(db, actor_tipo="platform_user", actor_id=p.id, tenant_id=tenant_id, accion=accion,
                 entidad=entidad, entidad_id=entidad_id, antes=antes, despues=despues, ip=ip)


# ---------------------------------------------------------------- módulos y planes
async def list_modules(db: AsyncSession) -> list[ModuleOut]:
    rows = (await db.execute(select(Module).order_by(Module.codigo))).scalars()
    return [ModuleOut(id=m.id, codigo=m.codigo, nombre=m.nombre, precio_mensual_base=m.precio_mensual_base) for m in rows]


async def patch_module(db: AsyncSession, p: Principal, codigo: str, d: ModulePatch, ip: str | None) -> ModuleOut:
    m = (await db.execute(select(Module).where(Module.codigo == codigo))).scalar_one_or_none()
    if m is None:
        raise AppError("NOT_FOUND", "Módulo no encontrado")
    antes = {"nombre": m.nombre, "precio_mensual_base": str(m.precio_mensual_base)}
    for k, v in d.model_dump(exclude_unset=True).items():
        setattr(m, k, v)
    _aud(db, p, "modulo_actualizado", "module", m.id, None, ip, antes, d.model_dump(mode="json", exclude_unset=True))
    return ModuleOut(id=m.id, codigo=m.codigo, nombre=m.nombre, precio_mensual_base=m.precio_mensual_base)


async def _module_ids(db: AsyncSession, codes: list[str]) -> dict[str, UUID]:
    rows = (await db.execute(select(Module.codigo, Module.id).where(Module.codigo.in_(codes)))).all()
    return {c: i for c, i in rows}


async def _plan_out(db: AsyncSession, plan: Plan) -> PlanOut:
    codes = (await db.execute(select(Module.codigo).join(PlanModule, PlanModule.module_id == Module.id)
                              .where(PlanModule.plan_id == plan.id).order_by(Module.codigo))).scalars().all()
    return PlanOut(id=plan.id, nombre=plan.nombre, precio_mensual=plan.precio_mensual, max_usuarios=plan.max_usuarios,
                   max_mensajes_ia_mes=plan.max_mensajes_ia_mes, es_independiente=plan.es_independiente,
                   activo=plan.activo, modulos=list(codes))


async def list_plans(db: AsyncSession, solo_activos: bool) -> list[PlanOut]:
    q = select(Plan).order_by(Plan.precio_mensual)
    if solo_activos:
        q = q.where(Plan.activo.is_(True))
    return [await _plan_out(db, pl) for pl in (await db.execute(q)).scalars()]


async def _set_plan_modules(db: AsyncSession, plan_id: UUID, codes: list[str]) -> None:
    ids = await _module_ids(db, codes)
    await db.execute(delete(PlanModule).where(PlanModule.plan_id == plan_id))
    for mid in ids.values():
        db.add(PlanModule(plan_id=plan_id, module_id=mid))


async def create_plan(db: AsyncSession, p: Principal, d: PlanIn, ip: str | None) -> PlanOut:
    if d.es_independiente and d.max_usuarios != 1:
        raise AppError("VALIDATION_ERROR", "Un plan independiente permite un solo usuario")
    plan = Plan(**d.model_dump(exclude={"modulos"}))
    db.add(plan)
    try:
        await db.flush()
    except IntegrityError as e:
        raise AppError("CONFLICT", "Ya existe un plan con ese nombre") from e
    await _set_plan_modules(db, plan.id, list(d.modulos))
    await db.flush()
    _aud(db, p, "plan_creado", "plan", plan.id, None, ip, despues=d.model_dump(mode="json"))
    return await _plan_out(db, plan)


async def patch_plan(db: AsyncSession, p: Principal, plan_id: UUID, d: PlanPatch, ip: str | None) -> PlanOut:
    plan = await db.get(Plan, plan_id)
    if plan is None:
        raise AppError("NOT_FOUND", "Plan no encontrado")
    antes = (await _plan_out(db, plan)).model_dump(mode="json")
    ch = d.model_dump(exclude_unset=True)
    mods = ch.pop("modulos", None)
    for k, v in ch.items():
        setattr(plan, k, v)
    if plan.es_independiente and plan.max_usuarios != 1:
        raise AppError("VALIDATION_ERROR", "Un plan independiente permite un solo usuario")
    if mods is not None:
        await _set_plan_modules(db, plan.id, mods)
    await db.flush()
    out = await _plan_out(db, plan)
    # Nota: cambiar el precio NO altera suscripciones existentes (precio_plan_aplicado está congelado).
    _aud(db, p, "plan_actualizado", "plan", plan.id, None, ip, antes, out.model_dump(mode="json"))
    return out


# ---------------------------------------------------------------- tenants
async def _tenant(db: AsyncSession, tid: UUID) -> Tenant:
    t = (await db.execute(select(Tenant).where(Tenant.id == tid, Tenant.deleted_at.is_(None)))).scalar_one_or_none()
    if t is None:
        raise AppError("NOT_FOUND", "Negocio no encontrado")
    return t


async def _vigente(db: AsyncSession, tid: UUID) -> TenantSubscription | None:
    return (await db.execute(select(TenantSubscription).where(
        TenantSubscription.tenant_id == tid, TenantSubscription.estado == "vigente"))).scalar_one_or_none()


def _recompute(t: Tenant, sub: TenantSubscription | None) -> None:
    if t.bloqueo_manual:
        return
    if sub is None:
        t.estado = "bloqueada"
        return
    dias = (sub.fecha_fin - _today(t.zona_horaria)).days
    t.estado = estado_por_dias(dias, get_settings().subscription_grace_days)


async def create_tenant(db: AsyncSession, p: Principal, d: TenantCreate, ip: str | None) -> TenantDetail:
    plan = await db.get(Plan, d.plan_id)
    if plan is None or not plan.activo:
        raise AppError("NOT_FOUND", "Plan no encontrado o inactivo")
    if not (await db.execute(text("SELECT 1 FROM business_types WHERE codigo = :c"), {"c": d.business_type})).first():
        raise AppError("VALIDATION_ERROR", "Tipo de negocio desconocido", {"business_type": d.business_type})
    inicio = d.fecha_inicio or _today(d.zona_horaria)
    fin = d.fecha_fin or inicio + timedelta(days=30)
    if fin < inicio:
        raise AppError("VALIDATION_ERROR", "fecha_fin debe ser posterior a fecha_inicio")

    t = Tenant(nombre=d.nombre, nit=d.nit, slug=d.slug, business_type=d.business_type,
               modo="independiente" if plan.es_independiente else "empresa", zona_horaria=d.zona_horaria,
               contacto_nombre=d.contacto_nombre, contacto_email=d.contacto_email, contacto_telefono=d.contacto_telefono)
    sub = TenantSubscription(plan_id=plan.id, fecha_inicio=inicio, fecha_fin=fin,
                             precio_plan_aplicado=d.precio_plan_aplicado if d.precio_plan_aplicado is not None else plan.precio_mensual)
    db.add(t)
    try:
        await db.flush()
        sub.tenant_id = t.id
        db.add(sub)
        db.add(User(tenant_id=t.id, email=d.admin.email, password_hash=hash_password(d.admin.password),
                    nombre=d.admin.nombre, rol="admin"))
        for mid in (await db.execute(select(PlanModule.module_id).where(PlanModule.plan_id == plan.id))).scalars():
            db.add(TenantModule(tenant_id=t.id, module_id=mid, origen="plan", activo=True))
        await db.flush()
    except IntegrityError as e:
        raise AppError("CONFLICT", "Ya existe un negocio con ese slug") from e
    _recompute(t, sub)
    if d.cargar_servicios_ejemplo:
        prm = {"t": t.id, "bt": d.business_type}
        await db.execute(text(
            "INSERT INTO service_categories (tenant_id, nombre, orden) "
            "SELECT CAST(:t AS uuid), categoria, row_number() OVER (ORDER BY categoria) "
            "FROM (SELECT DISTINCT categoria FROM business_type_templates WHERE business_type = :bt) c"), prm)
        await db.execute(text(
            "INSERT INTO services (tenant_id, category_id, nombre, duracion_min, precio) "
            "SELECT CAST(:t AS uuid), sc.id, tp.nombre, tp.duracion_min, tp.precio_sugerido "
            "FROM business_type_templates tp JOIN service_categories sc "
            "ON sc.tenant_id = CAST(:t AS uuid) AND sc.nombre = tp.categoria WHERE tp.business_type = :bt"), prm)
    _aud(db, p, "negocio_creado", "tenant", t.id, t.id, ip,
         despues={"slug": t.slug, "plan": plan.nombre, "fecha_fin": fin.isoformat()})
    return await get_tenant(db, t.id)


async def list_tenants(db: AsyncSession, pp: PageParams, q: str | None, estado: str | None) -> Page[TenantListItem]:
    sub = aliased(TenantSubscription)
    stmt = (select(Tenant, Plan.nombre, sub.fecha_fin)
            .outerjoin(sub, and_(sub.tenant_id == Tenant.id, sub.estado == "vigente"))
            .outerjoin(Plan, Plan.id == sub.plan_id).where(Tenant.deleted_at.is_(None)))
    if estado:
        stmt = stmt.where(Tenant.estado == estado)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(Tenant.nombre.ilike(like) | Tenant.slug.ilike(like) | Tenant.nit.ilike(like))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    col, desc = parse_sort(pp.sort, {"nombre": Tenant.nombre, "created_at": Tenant.created_at,
                                     "fecha_fin": sub.fecha_fin, "estado": Tenant.estado}, "-created_at")
    stmt = stmt.order_by(col.desc() if desc else col.asc()).offset(pp.offset).limit(pp.page_size)  # type: ignore[attr-defined]
    items = [TenantListItem(id=t.id, nombre=t.nombre, slug=t.slug, estado=t.estado, bloqueo_manual=t.bloqueo_manual,
                            plan=plan, fecha_fin=ff, dias_restantes=(ff - _today(t.zona_horaria)).days if ff else None,
                            created_at=t.created_at)
             for t, plan, ff in (await db.execute(stmt)).all()]
    return Page[TenantListItem](items=items, page=pp.page, page_size=pp.page_size, total=total)


async def billing_for(db: AsyncSession, t: Tenant, sub: TenantSubscription | None) -> BillingOut:
    plan = await db.get(Plan, sub.plan_id) if sub else None
    extras = (await db.execute(
        select(Module.nombre, Module.precio_mensual_base, TenantModule.precio_override)
        .join(TenantModule, TenantModule.module_id == Module.id)
        .where(TenantModule.tenant_id == t.id, TenantModule.origen == "adicional", TenantModule.activo.is_(True))
        .order_by(Module.codigo))).all()
    return compute_billing(plan.nombre if plan else None, sub.precio_plan_aplicado if sub else Decimal("0"),
                           [(n, b, o) for n, b, o in extras], t.moneda)


async def get_tenant(db: AsyncSession, tid: UUID) -> TenantDetail:
    t = await _tenant(db, tid)
    sub = await _vigente(db, tid)
    plan = await db.get(Plan, sub.plan_id) if sub else None
    mods = (await db.execute(select(Module.codigo, Module.nombre, TenantModule.origen, TenantModule.activo,
                                    TenantModule.precio_override).join(TenantModule, TenantModule.module_id == Module.id)
                             .where(TenantModule.tenant_id == tid).order_by(Module.codigo))).all()
    users = (await db.execute(select(func.count()).select_from(User).where(
        User.tenant_id == tid, User.deleted_at.is_(None), User.activo.is_(True)))).scalar_one()
    return TenantDetail(
        id=t.id, nombre=t.nombre, nit=t.nit, slug=t.slug, business_type=t.business_type, modo=t.modo,
        zona_horaria=t.zona_horaria, estado=t.estado, bloqueo_manual=t.bloqueo_manual,
        requiere_sesion_caja=t.requiere_sesion_caja, contacto_nombre=t.contacto_nombre,
        contacto_email=t.contacto_email, contacto_telefono=t.contacto_telefono, usuarios_activos=users,
        suscripcion=SubscriptionOut(plan_id=sub.plan_id, plan=plan.nombre, fecha_inicio=sub.fecha_inicio,
                                    fecha_fin=sub.fecha_fin, precio_plan_aplicado=sub.precio_plan_aplicado,
                                    notas=sub.notas) if sub and plan else None,
        modulos=[TenantModuleOut(codigo=c, nombre=n, origen=o, activo=a, precio_override=po) for c, n, o, a, po in mods],
        facturacion=await billing_for(db, t, sub))


async def patch_tenant(db: AsyncSession, p: Principal, tid: UUID, d: TenantPatch, ip: str | None) -> TenantDetail:
    t = await _tenant(db, tid)
    ch = d.model_dump(exclude_unset=True)
    antes = {k: getattr(t, k) for k in ch}
    for k, v in ch.items():
        setattr(t, k, v)
    _aud(db, p, "negocio_actualizado", "tenant", t.id, t.id, ip, antes, ch)
    return await get_tenant(db, tid)


async def set_estado(db: AsyncSession, p: Principal, tid: UUID, accion: str, ip: str | None) -> TenantDetail:
    t = await _tenant(db, tid)
    antes = {"estado": t.estado, "bloqueo_manual": t.bloqueo_manual}
    if accion == "activar":
        t.bloqueo_manual = False
        _recompute(t, await _vigente(db, tid))
    else:
        t.bloqueo_manual = True
        t.estado = "suspendida" if accion == "suspender" else "bloqueada"
    _aud(db, p, f"negocio_{accion}", "tenant", t.id, t.id, ip, antes, {"estado": t.estado, "bloqueo_manual": t.bloqueo_manual})
    return await get_tenant(db, tid)


async def assign_plan(db: AsyncSession, p: Principal, tid: UUID, d: AssignPlanIn, ip: str | None) -> TenantDetail:
    t = await _tenant(db, tid)
    plan = await db.get(Plan, d.plan_id)
    if plan is None or not plan.activo:
        raise AppError("NOT_FOUND", "Plan no encontrado o inactivo")
    users = (await db.execute(select(func.count()).select_from(User).where(
        User.tenant_id == tid, User.deleted_at.is_(None), User.activo.is_(True)))).scalar_one()
    if users > plan.max_usuarios:
        raise AppError("USER_LIMIT_REACHED", "El negocio tiene más usuarios activos que los permitidos por el plan",
                       {"usuarios_activos": users, "max_usuarios": plan.max_usuarios})
    sub = await _vigente(db, tid)
    antes = {"plan_id": str(sub.plan_id), "precio": str(sub.precio_plan_aplicado)} if sub else None
    precio = d.precio_plan_aplicado if d.precio_plan_aplicado is not None else plan.precio_mensual
    if sub is None:     # sin suscripción: queda vigente solo hoy; debe registrarse un pago para extenderla
        hoy = _today(t.zona_horaria)
        sub = TenantSubscription(tenant_id=tid, plan_id=plan.id, fecha_inicio=hoy, fecha_fin=hoy,
                                 precio_plan_aplicado=precio, notas=d.notas)
        db.add(sub)
    else:
        sub.plan_id, sub.precio_plan_aplicado = plan.id, precio
        if d.notas is not None:
            sub.notas = d.notas
    t.modo = "independiente" if plan.es_independiente else "empresa"

    new_ids = set((await db.execute(select(PlanModule.module_id).where(PlanModule.plan_id == plan.id))).scalars())
    existing = {tm.module_id: tm for tm in (await db.execute(select(TenantModule).where(TenantModule.tenant_id == tid))).scalars()}
    for mid, tm in existing.items():
        if mid in new_ids:
            tm.origen, tm.activo, tm.precio_override = "plan", True, None
        elif tm.origen == "plan":
            tm.activo = False
    for mid in new_ids - existing.keys():
        db.add(TenantModule(tenant_id=tid, module_id=mid, origen="plan", activo=True))
    await db.flush()
    _recompute(t, sub)
    _aud(db, p, "plan_asignado", "tenant", tid, tid, ip, antes, {"plan_id": str(plan.id), "precio": str(precio)})
    return await get_tenant(db, tid)


async def set_tenant_module(db: AsyncSession, p: Principal, tid: UUID, codigo: str, d: TenantModuleIn,
                            ip: str | None) -> TenantDetail:
    await _tenant(db, tid)
    m = (await db.execute(select(Module).where(Module.codigo == codigo))).scalar_one_or_none()
    if m is None:
        raise AppError("NOT_FOUND", "Módulo no encontrado")
    tm = (await db.execute(select(TenantModule).where(TenantModule.tenant_id == tid, TenantModule.module_id == m.id))).scalar_one_or_none()
    if tm is not None and tm.origen == "plan" and tm.activo:
        raise AppError("CONFLICT", "El módulo ya está incluido en el plan del negocio")
    antes = {"activo": tm.activo, "precio_override": str(tm.precio_override) if tm.precio_override is not None else None} if tm else None
    if tm is None:
        tm = TenantModule(tenant_id=tid, module_id=m.id, origen="adicional", activo=d.activo, precio_override=d.precio_override)
        db.add(tm)
    else:     # existía (adicional, o de plan ya desactivado por un cambio de plan): se trata como adicional
        tm.origen, tm.activo, tm.precio_override = "adicional", d.activo, d.precio_override
    await db.flush()
    _aud(db, p, "modulo_adicional", "tenant_module", tm.id, tid, ip, antes,
         {"modulo": codigo, "activo": d.activo, "precio_override": str(d.precio_override) if d.precio_override is not None else None})
    return await get_tenant(db, tid)


# ---------------------------------------------------------------- pagos
async def register_payment(db: AsyncSession, p: Principal, tid: UUID, d: PaymentIn, ip: str | None) -> PaymentResult:
    t = await _tenant(db, tid)
    sub = await _vigente(db, tid)
    if sub is None:
        raise AppError("CONFLICT", "El negocio no tiene suscripción vigente: asigna un plan primero")
    pago = SubscriptionPayment(tenant_id=tid, registrado_por=p.id, **d.model_dump())
    db.add(pago)
    antes_fin = sub.fecha_fin
    if d.periodo_hasta > sub.fecha_fin:
        sub.fecha_fin = d.periodo_hasta       # renovación: desbloqueo inmediato (se evalúa en cada request)
    _recompute(t, sub)
    await db.flush()
    _aud(db, p, "pago_registrado", "subscription_payment", pago.id, tid, ip,
         {"fecha_fin": antes_fin.isoformat()}, {"fecha_fin": sub.fecha_fin.isoformat(), "monto": str(d.monto)})
    return PaymentResult(pago=_pay_out(pago), estado_negocio=t.estado, fecha_fin=sub.fecha_fin)


def _pay_out(x: SubscriptionPayment) -> PaymentOut:
    return PaymentOut(id=x.id, monto=x.monto, fecha_pago=x.fecha_pago, periodo_desde=x.periodo_desde,
                      periodo_hasta=x.periodo_hasta, metodo=x.metodo, referencia=x.referencia)


async def list_payments(db: AsyncSession, tid: UUID, pp: PageParams) -> Page[PaymentOut]:
    await _tenant(db, tid)
    base = select(SubscriptionPayment).where(SubscriptionPayment.tenant_id == tid)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (await db.execute(base.order_by(SubscriptionPayment.fecha_pago.desc(), SubscriptionPayment.created_at.desc())
                             .offset(pp.offset).limit(pp.page_size))).scalars()
    return Page[PaymentOut](items=[_pay_out(x) for x in rows], page=pp.page, page_size=pp.page_size, total=total)


# ---------------------------------------------------------------- dashboard, impersonación, jobs
async def dashboard(db: AsyncSession) -> DashboardOut:
    por_estado = {e: n for e, n in (await db.execute(text(
        "SELECT estado, count(*) FROM tenants WHERE deleted_at IS NULL GROUP BY estado"))).all()}
    mrr = (await db.execute(text(
        "SELECT coalesce(sum(s.precio_plan_aplicado + coalesce(x.extra, 0)), 0) FROM tenants t "
        "JOIN tenant_subscriptions s ON s.tenant_id = t.id AND s.estado = 'vigente' "
        "LEFT JOIN LATERAL (SELECT sum(coalesce(tm.precio_override, m.precio_mensual_base)) AS extra "
        "  FROM tenant_modules tm JOIN modules m ON m.id = tm.module_id "
        "  WHERE tm.tenant_id = t.id AND tm.origen = 'adicional' AND tm.activo) x ON true "
        "WHERE t.deleted_at IS NULL AND t.estado IN ('activa', 'por_vencer', 'vencida')"))).scalar_one()
    return DashboardOut(por_estado=por_estado, activas=por_estado.get("activa", 0), por_vencer=por_estado.get("por_vencer", 0),
                        vencidas=por_estado.get("vencida", 0),
                        suspendidas_o_bloqueadas=por_estado.get("suspendida", 0) + por_estado.get("bloqueada", 0), mrr=mrr)


async def impersonate(db: AsyncSession, p: Principal, tid: UUID, ip: str | None) -> ImpersonationOut:
    t = await _tenant(db, tid)
    admin = (await db.execute(select(User).where(User.tenant_id == tid, User.rol == "admin", User.activo.is_(True),
                                                  User.deleted_at.is_(None)).order_by(User.created_at).limit(1))).scalar_one_or_none()
    if admin is None:
        raise AppError("NOT_FOUND", "El negocio no tiene un administrador activo")
    minutes = get_settings().impersonation_minutes
    token, ttl = create_access_token(sub=admin.id, kind="user", tenant_id=tid, rol=admin.rol, imp=p.id, ttl_minutes=minutes)
    _aud(db, p, "impersonacion_inicio", "user", admin.id, t.id, ip, despues={"expira_en_min": minutes})
    return ImpersonationOut(access_token=token, expires_in=ttl, usuario_id=admin.id)


async def sync_subscription_states(db: AsyncSession) -> list[tuple[UUID, str]]:
    """Job diario: recalcula estado por fecha_fin (hora local de cada tenant). Respeta bloqueo_manual."""
    rows = await db.execute(text(
        "WITH calc AS (SELECT q.id, CASE WHEN q.d + CAST(:g AS int) < 0 THEN 'bloqueada' WHEN q.d < 0 THEN 'vencida' "
        "                  WHEN q.d <= 7 THEN 'por_vencer' ELSE 'activa' END AS nuevo "
        "  FROM (SELECT t.id, (s.fecha_fin - (now() AT TIME ZONE t.zona_horaria)::date) AS d FROM tenants t "
        "        JOIN tenant_subscriptions s ON s.tenant_id = t.id AND s.estado = 'vigente' "
        "        WHERE t.deleted_at IS NULL AND NOT t.bloqueo_manual) q) "
        "UPDATE tenants x SET estado = c.nuevo FROM calc c WHERE x.id = c.id AND x.estado <> c.nuevo RETURNING x.id, c.nuevo"),
        {"g": get_settings().subscription_grace_days})
    return [(r[0], r[1]) for r in rows]


async def queue_expiry_reminders(db: AsyncSession) -> int:
    """Avisos al/los admin(s) a 7 y 3 días. Idempotente: no repite el mismo aviso para la misma fecha."""
    r = await db.execute(text(
        "INSERT INTO notifications (tenant_id, canal, destinatario, plantilla, payload) "
        "SELECT t.id, 'email', u.email, 'suscripcion_por_vencer', "
        "       jsonb_build_object('dias', d.dias, 'fecha_fin', s.fecha_fin::text) "
        "FROM tenants t JOIN tenant_subscriptions s ON s.tenant_id = t.id AND s.estado = 'vigente' "
        "JOIN users u ON u.tenant_id = t.id AND u.rol = 'admin' AND u.activo AND u.deleted_at IS NULL "
        "CROSS JOIN LATERAL (SELECT (s.fecha_fin - (now() AT TIME ZONE t.zona_horaria)::date) AS dias) d "
        "WHERE t.deleted_at IS NULL AND NOT t.bloqueo_manual AND d.dias IN (7, 3) "
        "AND NOT EXISTS (SELECT 1 FROM notifications n WHERE n.tenant_id = t.id AND n.destinatario = u.email "
        "  AND n.plantilla = 'suscripcion_por_vencer' AND n.payload->>'fecha_fin' = s.fecha_fin::text "
        "  AND (n.payload->>'dias')::int = d.dias)"))
    return int(getattr(r, "rowcount", 0) or 0)
