"""Reglas puras de la factura electrónica colombiana (sin BD ni red).

Aquí vive lo que es NUESTRO: numeración dentro del rango de la resolución, impuestos, estados y reintentos.
Lo que depende del proveedor tecnológico (XML UBL, firma, envío a la DIAN) está detrás de `provider.py`.
"""
import hashlib
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.core.errors import AppError

CENT = Decimal("0.01")
_NIT_WEIGHTS = (3, 7, 13, 17, 19, 23, 29, 37, 41, 43, 47, 53, 59, 67, 71)
CONSUMIDOR_FINAL = {"tipo_documento": "13", "documento": "222222222222", "nombre": "Consumidor final", "email": None}
# Tipos de documento DIAN (los más usados): 13 cédula, 22 cédula de extranjería, 31 NIT, 41 pasaporte, 42 doc. extranjero, 11 reg. civil, 12 tarjeta identidad
TIPOS_DOC = {"11", "12", "13", "21", "22", "31", "41", "42", "47", "50", "91"}
MOTIVOS_NC = {1: "Devolución parcial de los bienes y/o no aceptación parcial del servicio", 2: "Anulación de factura electrónica",
              3: "Rebaja total aplicada", 4: "Descuento total aplicado", 5: "Rescisión: nulidad por falta de requisitos", 6: "Otros"}
MAX_ATTEMPTS = 8
_BACKOFF_MIN = (1, 2, 5, 15, 30, 60, 120, 240)
ACTIVE_STATES = ("emitida", "enviada", "aceptada")


def money(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------- NIT
def nit_dv(nit: str) -> int:
    """Dígito de verificación (módulo 11, pesos DIAN). Ej.: 800197268 -> 4 (el NIT de la propia DIAN)."""
    digits = [int(c) for c in nit if c.isdigit()]
    if not 5 <= len(digits) <= 15 or len(digits) != len(nit):
        raise AppError("VALIDATION_ERROR", "El NIT debe tener solo números (sin puntos ni guion ni DV)", {"campo": "nit"})
    total = sum(d * w for d, w in zip(reversed(digits), _NIT_WEIGHTS, strict=False))
    r = total % 11
    return r if r in (0, 1) else 11 - r


def check_nit(nit: str, dv: int | None) -> int:
    calc = nit_dv(nit)
    if dv is not None and dv != calc:
        raise AppError("VALIDATION_ERROR", f"El dígito de verificación no corresponde al NIT (debería ser {calc})", {"campo": "dv", "esperado": calc})
    return calc


# ---------------------------------------------------------------- resolución y numeración
@dataclass(frozen=True)
class Resolution:
    prefijo: str
    desde: int
    hasta: int
    vigencia_hasta: date | None
    numero: str | None = None


def next_number(last: int | None, desde: int) -> int:
    """Siguiente consecutivo: nunca por debajo del inicio autorizado; sin huecos si ya hubo emisión."""
    return desde if last is None else max(last + 1, desde)


def check_resolution(res: Resolution, numero: int, today: date) -> None:
    if res.vigencia_hasta and today > res.vigencia_hasta:
        raise AppError("RESOLUTION_EXPIRED", f"La resolución de numeración venció el {res.vigencia_hasta.isoformat()}: solicita una nueva a la DIAN",
                       {"vigencia_hasta": res.vigencia_hasta.isoformat()})
    if numero > res.hasta:
        raise AppError("RESOLUTION_EXHAUSTED", f"Se agotó el rango autorizado ({res.desde}–{res.hasta}): solicita una nueva resolución a la DIAN",
                       {"rango_hasta": res.hasta})


def remaining(last: int | None, res: Resolution) -> int:
    return max(res.hasta - (next_number(last, res.desde) - 1), 0)


def full_number(prefijo: str | None, numero: int) -> str:
    return f"{prefijo or ''}{numero}"


# ---------------------------------------------------------------- líneas, impuestos, medios de pago
def iva_pct(base: Decimal, iva: Decimal) -> Decimal:
    """Tarifa efectiva de una línea. Con redondeo a centavos puede quedar 18,99 en vez de 19: se ajusta a la tarifa conocida."""
    if base <= 0 or iva <= 0:
        return Decimal("0")
    pct = (iva / base * 100).quantize(CENT)
    for known in (Decimal("19"), Decimal("5"), Decimal("8")):
        if abs(pct - known) <= Decimal("0.15"):
            return known
    return pct


def build_lines(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for i, it in enumerate(items, start=1):
        base, iva = Decimal(str(it["precio_cobrado"])), Decimal(str(it["iva"]))
        out.append({"n": i, "item_id": str(it["id"]), "descripcion": it["descripcion"], "cantidad": 1, "precio_unitario": str(base),
                    "base": str(base), "iva_porcentaje": str(iva_pct(base, iva)), "iva_valor": str(iva), "total": str(base + iva)})
    return out


def group_taxes(lines: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Resumen por tarifa (lo que exige la factura): base e IVA por cada porcentaje."""
    by: dict[str, list[Decimal]] = {}
    for ln in lines:
        acc = by.setdefault(ln["iva_porcentaje"], [Decimal("0"), Decimal("0")])
        acc[0] += Decimal(ln["base"])
        acc[1] += Decimal(ln["iva_valor"])
    return [{"codigo": "01", "nombre": "IVA", "porcentaje": pct, "base": str(b), "valor": str(v)} for pct, (b, v) in sorted(by.items(), key=lambda kv: Decimal(kv[0]))]


def totals(lines: list[dict[str, Any]]) -> dict[str, str]:
    sub = sum((Decimal(x["base"]) for x in lines), Decimal("0"))
    iva = sum((Decimal(x["iva_valor"]) for x in lines), Decimal("0"))
    return {"subtotal": str(sub), "iva": str(iva), "total": str(sub + iva)}


_PAGO_DIAN = {"efectivo": "10", "transferencia": "47", "datafono": "1", "otro": "1"}      # 10 efectivo · 47 transferencia débito bancaria · 1 no definido


def medios_pago(pagos: list[dict[str, Any]]) -> list[dict[str, str]]:
    seen: dict[str, Decimal] = {}
    for p in pagos:
        code = _PAGO_DIAN.get(p["metodo"], "1")
        seen[code] = seen.get(code, Decimal("0")) + Decimal(str(p["monto"]))
    return [{"codigo": c, "monto": str(m)} for c, m in seen.items()]


def adquirente_for(customer: dict[str, Any] | None, override: dict[str, Any] | None) -> dict[str, Any]:
    """Quién compra. Prioridad: lo enviado en la petición > datos del cliente > consumidor final."""
    if override:
        a = {"tipo_documento": override["tipo_documento"], "documento": override["documento"], "nombre": override["nombre"], "email": override.get("email")}
    elif customer and (customer.get("documento") or "").strip():
        a = {"tipo_documento": "13", "documento": customer["documento"].strip(), "nombre": customer["nombre"], "email": customer.get("email")}   # supuesto: cédula
    else:
        a = dict(CONSUMIDOR_FINAL)
    if a["tipo_documento"] not in TIPOS_DOC:
        raise AppError("VALIDATION_ERROR", "Tipo de documento no válido", {"campo": "tipo_documento", "permitidos": sorted(TIPOS_DOC)})
    return a


def cufe_reference(*, numero: str, fecha: datetime, valor_factura: Decimal, iva: Decimal, total: Decimal, nit_emisor: str, doc_adquirente: str,
                   clave_tecnica: str, ambiente: str) -> str:
    """CUFE de REFERENCIA (SHA-384 de los campos del anexo técnico). Solo lo usa el proveedor simulado.
    El CUFE oficial lo calcula y valida el proveedor tecnológico autorizado; nunca se presenta este valor como oficial."""
    amb = "2" if ambiente == "habilitacion" else "1"
    raw = (f"{numero}{fecha:%Y-%m-%d}{fecha:%H:%M:%S}-05:00{money(valor_factura)}01{money(iva)}04{Decimal('0.00')}03{Decimal('0.00')}"
           f"{money(total)}{nit_emisor}{doc_adquirente}{clave_tecnica}{amb}")
    return hashlib.sha384(raw.encode()).hexdigest()


# ---------------------------------------------------------------- estados y reintentos
def backoff_minutes(intentos: int) -> int:
    return _BACKOFF_MIN[min(max(intentos, 1), len(_BACKOFF_MIN)) - 1]


def can_retry(estado: str) -> bool:
    return estado in ("rechazada", "error")


def credit_note_room(factura_total: Decimal, ya_acreditado: Decimal, solicitado: Decimal) -> None:
    if solicitado <= 0:
        raise AppError("VALIDATION_ERROR", "La nota crédito no tiene valor")
    if ya_acreditado + solicitado > factura_total:
        raise AppError("INVOICE_NOT_ALLOWED", "Las notas crédito superarían el valor de la factura",
                       {"factura": str(factura_total), "acreditado": str(ya_acreditado), "solicitado": str(solicitado)})
