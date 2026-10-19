from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.pagination import Page, PageParams
from app.core.repository import get_or_404, paginate, scoped
from app.core.tenancy import Principal
from app.modules.audit import service as audit
from app.modules.inventory.models import Product, StockMovement
from app.modules.inventory.schemas import (
    MovementIn,
    MovementOut,
    ProductIn,
    ProductOut,
    ProductPatch,
    SummaryOut,
)

BAJO_STOCK = (Product.stock_minimo > 0) & (Product.stock_actual <= Product.stock_minimo)


def _aud(db: AsyncSession, p: Principal, accion: str, prod: Product, despues: dict | None = None) -> None:  # type: ignore[type-arg]
    audit.record(db, actor_tipo="user", actor_id=p.id, tenant_id=p.tid, accion=accion, entidad="product", entidad_id=prod.id, despues=despues)


def _sku(sku: str | None) -> str | None:
    return (sku.strip() or None) if sku else None


async def _sku_libre(db: AsyncSession, tid: UUID, sku: str | None, excepto: UUID | None = None) -> None:
    if not sku:
        return
    q = scoped(Product, tid).where(func.lower(Product.sku) == sku.lower())
    if excepto:
        q = q.where(Product.id != excepto)
    if (await db.execute(q)).first():
        raise AppError("CONFLICT", "Ya existe un producto con ese código", {"campo": "sku"})


async def list_products(db: AsyncSession, p: Principal, pp: PageParams, q: str | None, activo: bool | None, bajo_stock: bool | None) -> Page[ProductOut]:
    stmt = scoped(Product, p.tid)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Product.nombre.ilike(like), Product.sku.ilike(like)))
    if activo is not None:
        stmt = stmt.where(Product.activo == activo)
    if bajo_stock:
        stmt = stmt.where(BAJO_STOCK)
    rows, total = await paginate(db, stmt, pp, {"nombre": Product.nombre, "stock_actual": Product.stock_actual, "created_at": Product.created_at}, "nombre")
    return Page[ProductOut](items=[ProductOut.model_validate(r) for r in rows], page=pp.page, page_size=pp.page_size, total=total)


async def get_product(db: AsyncSession, p: Principal, pid: UUID) -> ProductOut:
    return ProductOut.model_validate(await get_or_404(db, Product, pid, p.tid, "Producto"))


async def create_product(db: AsyncSession, p: Principal, d: ProductIn) -> ProductOut:
    sku = _sku(d.sku)
    await _sku_libre(db, p.tid, sku)
    prod = Product(tenant_id=p.tid, nombre=d.nombre.strip(), sku=sku, costo=d.costo, precio_venta=d.precio_venta, stock_minimo=d.stock_minimo, activo=d.activo)
    db.add(prod)
    await db.flush()
    if d.stock_inicial:
        await _mover(db, p, prod, "entrada", d.stock_inicial, "Stock inicial", None)
    _aud(db, p, "producto_creado", prod, {"nombre": prod.nombre, "stock_inicial": d.stock_inicial})
    await db.refresh(prod)                       # el dinero vuelve con el formato de la base ("18000.00")
    return ProductOut.model_validate(prod)


async def patch_product(db: AsyncSession, p: Principal, pid: UUID, d: ProductPatch) -> ProductOut:
    prod = await get_or_404(db, Product, pid, p.tid, "Producto")
    ch = d.model_dump(exclude_unset=True)
    if "sku" in ch:
        ch["sku"] = _sku(ch["sku"])
        await _sku_libre(db, p.tid, ch["sku"], excepto=prod.id)
    for k, v in ch.items():
        setattr(prod, k, v.strip() if k == "nombre" and isinstance(v, str) else v)
    await db.flush()
    _aud(db, p, "producto_editado", prod, {k: str(v) for k, v in ch.items()})
    await db.refresh(prod)
    return ProductOut.model_validate(prod)


async def delete_product(db: AsyncSession, p: Principal, pid: UUID) -> None:
    prod = await get_or_404(db, Product, pid, p.tid, "Producto")
    prod.deleted_at = datetime.now(UTC)           # borrado lógico: el historial de movimientos se conserva
    await db.flush()
    _aud(db, p, "producto_eliminado", prod)


async def _mover(db: AsyncSession, p: Principal, prod: Product, tipo: str, cantidad: int, motivo: str | None, costo: Decimal | None) -> StockMovement:
    """Aplica el movimiento con el producto ya bloqueado: dos salidas simultáneas nunca dejan el stock en negativo."""
    if tipo == "entrada":
        delta = cantidad
    elif tipo == "salida":
        if cantidad > prod.stock_actual:
            raise AppError("STOCK_INSUFFICIENT", f"No hay suficiente stock de {prod.nombre}", {"disponible": prod.stock_actual, "solicitado": cantidad})
        delta = -cantidad
    else:
        delta = cantidad - prod.stock_actual
        if delta == 0:
            raise AppError("VALIDATION_ERROR", "El conteo coincide con el stock actual: no hay nada que ajustar", {"stock_actual": prod.stock_actual})
    prod.stock_actual += delta
    if tipo == "entrada" and costo is not None:
        prod.costo = costo
    mov = StockMovement(tenant_id=p.tid, product_id=prod.id, tipo=tipo, delta=delta, stock_resultante=prod.stock_actual,
                        costo_unitario=costo, motivo=motivo.strip() if motivo else None, user_id=p.id)
    db.add(mov)
    await db.flush()
    return mov


async def add_movement(db: AsyncSession, p: Principal, pid: UUID, d: MovementIn) -> MovementOut:
    prod = (await db.execute(scoped(Product, p.tid).where(Product.id == pid).with_for_update())).scalar_one_or_none()   # candado de fila
    if prod is None:
        raise AppError("NOT_FOUND", "Producto no encontrado")
    mov = await _mover(db, p, prod, d.tipo, d.cantidad, d.motivo, d.costo_unitario)
    return MovementOut.model_validate(mov)


async def list_movements(db: AsyncSession, p: Principal, pid: UUID, pp: PageParams) -> Page[MovementOut]:
    await get_or_404(db, Product, pid, p.tid, "Producto")
    stmt = select(StockMovement).where(StockMovement.tenant_id == p.tid, StockMovement.product_id == pid)
    rows, total = await paginate(db, stmt, pp, {"created_at": StockMovement.created_at}, "-created_at")
    return Page[MovementOut](items=[MovementOut.model_validate(r) for r in rows], page=pp.page, page_size=pp.page_size, total=total)


async def summary(db: AsyncSession, p: Principal) -> SummaryOut:
    base = scoped(Product, p.tid).where(Product.activo.is_(True)).subquery()
    n, bajo, valor = (await db.execute(select(
        func.count(), func.count().filter((base.c.stock_minimo > 0) & (base.c.stock_actual <= base.c.stock_minimo)),
        func.coalesce(func.sum(base.c.costo * base.c.stock_actual), 0)).select_from(base))).one()
    return SummaryOut(productos=n, bajo_stock=bajo, valor_inventario=Decimal(valor))
