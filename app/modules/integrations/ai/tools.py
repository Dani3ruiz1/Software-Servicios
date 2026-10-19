"""Ejecuta las herramientas del agente contra el motor REAL (disponibilidad, reservas, citas).

Seguridad: el modelo nunca elige el negocio ni el teléfono. Ambos salen de la conversación autenticada por la firma del
webhook; todo lo que el modelo manda (ids, fechas, texto) se trata como entrada no confiable y se valida.
"""
import json
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.modules.appointments import service as apps
from app.modules.appointments.models import Appointment
from app.modules.booking import rules
from app.modules.booking import service as booking
from app.modules.booking.schemas import BookingIn, ClientIn
from app.modules.customers.models import Customer
from app.modules.integrations.models import Conversation

MAX_SLOTS_PER_DAY = 10
MAX_DAYS = 7


def _j(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def _uuid(v: Any) -> UUID:
    try:
        return UUID(str(v))
    except ValueError as e:
        raise AppError("VALIDATION_ERROR", "Identificador inválido") from e


def spread(items: list[Any], n: int) -> list[Any]:
    """Si hay más de n horarios, devuelve n repartidos a lo largo del día (no solo los de la mañana)."""
    if len(items) <= n:
        return items
    step = (len(items) - 1) / (n - 1)
    return [items[round(i * step)] for i in range(n)]


class ToolBox:
    def __init__(self, db: AsyncSession, ctx: booking.PublicCtx, conv: Conversation) -> None:
        self.db, self.ctx, self.conv = db, ctx, conv
        self.phone = rules.normalize_phone(conv.contacto_externo)
        self.escalado: str | None = None

    async def run(self, name: str, args: dict[str, Any]) -> tuple[str, bool]:
        """(resultado, es_error). Cada herramienta corre en un savepoint: si falla no deja escrituras a medias."""
        fn = {"consultar_disponibilidad": self.disponibilidad, "crear_reserva": self.reservar, "consultar_mis_citas": self.mis_citas,
              "cancelar_cita": self.cancelar, "escalar_a_recepcion": self.escalar}.get(name)
        if fn is None:
            return _j({"error": f"Herramienta desconocida: {name}"}), True
        try:
            async with self.db.begin_nested():
                return _j(await fn(args if isinstance(args, dict) else {})), False
        except AppError as e:
            return _j({"error": e.message, "codigo": e.code}), True
        except (KeyError, ValueError, TypeError) as e:
            return _j({"error": f"Datos inválidos: {e}"}), True

    # ------------------------------------------------------------------
    async def _customer(self) -> Customer | None:
        row = (await self.db.execute(text(
            "SELECT id FROM customers WHERE tenant_id = :t AND deleted_at IS NULL "
            "AND regexp_replace(coalesce(telefono, ''), '\\D', '', 'g') IN (:d, :d57) ORDER BY created_at LIMIT 1"),
            {"t": self.ctx.tenant_id, "d": self.phone, "d57": "57" + self.phone})).first()
        return await self.db.get(Customer, row.id) if row else None

    async def disponibilidad(self, a: dict[str, Any]) -> dict[str, Any]:
        desde = date.fromisoformat(str(a["fecha_desde"]))
        hasta = date.fromisoformat(str(a.get("fecha_hasta") or desde))
        if hasta < desde:
            raise AppError("VALIDATION_ERROR", "fecha_hasta es anterior a fecha_desde")
        hasta = min(hasta, desde + timedelta(days=MAX_DAYS - 1))
        ids = [_uuid(x) for x in a.get("servicio_ids") or []]
        staff = _uuid(a["profesional_id"]) if a.get("profesional_id") else None
        av = await booking.public_availability(self.db, self.ctx, desde, hasta, ids, staff)
        dias = []
        for d in av.dias:
            sl = spread(d.horarios, MAX_SLOTS_PER_DAY)
            dias.append({"fecha": d.fecha.isoformat(), "dia": ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"][d.fecha.weekday()],
                         "horarios": [{"hora": h.strftime("%H:%M"), "inicio": h.isoformat()} for h in sl],
                         "hay_mas_horarios": len(d.horarios) > len(sl), "total_libres": len(d.horarios)})
        return {"zona_horaria": av.zona_horaria, "duracion_total_min": av.duracion_min, "dias": dias,
                "nota": "Solo ofrece horas de esta lista. Si no hay, dilo y propón otro día."}

    async def reservar(self, a: dict[str, Any]) -> dict[str, Any]:
        cust = await self._customer()
        nombre = (cust.nombre if cust else None) or str(a.get("nombre_cliente") or "").strip() or (self.conv.nombre_contacto or "").strip()
        if len(nombre) < 2:
            raise AppError("VALIDATION_ERROR", "Falta el nombre del cliente: pídeselo antes de reservar")
        consent = bool(cust and cust.consentimiento_datos) or a.get("acepta_tratamiento_datos") is True
        if not consent:
            raise AppError("VALIDATION_ERROR", "Falta la autorización de tratamiento de datos: pídesela al cliente y repite con acepta_tratamiento_datos=true")
        inicio = datetime.fromisoformat(str(a["inicio"]))
        if inicio.tzinfo is None:
            raise AppError("VALIDATION_ERROR", "`inicio` debe incluir la zona horaria; usa el valor exacto de consultar_disponibilidad")
        body = BookingIn(service_ids=[_uuid(x) for x in a.get("servicio_ids") or []], inicio=inicio,
                         staff_id=_uuid(a["profesional_id"]) if a.get("profesional_id") else None,
                         cliente=ClientIn(nombre=nombre, telefono=self.phone), consentimiento_datos=True)
        out = await booking.create_booking(self.db, self.ctx, body, origen="whatsapp")
        if not self.conv.customer_id:
            c2 = await self._customer()
            self.conv.customer_id = c2.id if c2 else None
        return {"reservada": True, "estado": out.estado, "fecha_hora": out.inicio.isoformat(), "hora": out.inicio.strftime("%H:%M"),
                "profesional": out.profesional, "servicios": [s.nombre for s in out.servicios], "total": str(out.total),
                "puede_cancelar_hasta": out.puede_cancelar_hasta.isoformat()}

    async def mis_citas(self, a: dict[str, Any]) -> dict[str, Any]:
        cust = await self._customer()
        if not cust:
            return {"citas": [], "nota": "No hay citas registradas con este número."}
        rows = list((await self.db.execute(select(Appointment).where(
            Appointment.tenant_id == self.ctx.tenant_id, Appointment.customer_id == cust.id, Appointment.deleted_at.is_(None),
            Appointment.estado.in_(("pendiente", "confirmada")), Appointment.inicio > datetime.now(UTC)).order_by(Appointment.inicio).limit(10))).scalars())
        outs = await apps._outs(self.db, self.ctx.tenant_id, rows, (await apps.get_ctx(self.db, self.ctx.tenant_id)).tz)
        return {"citas": [{"cita_id": str(o.id), "fecha_hora": o.inicio.isoformat(), "hora": o.inicio.strftime("%H:%M"),
                           "servicios": [s.nombre for s in o.servicios], "profesional": o.profesional.nombre_publico if o.profesional else None,
                           "estado": o.estado} for o in outs]}

    async def cancelar(self, a: dict[str, Any]) -> dict[str, Any]:
        cust = await self._customer()
        appt = (await self.db.execute(select(Appointment).where(
            Appointment.tenant_id == self.ctx.tenant_id, Appointment.id == _uuid(a["cita_id"]), Appointment.deleted_at.is_(None))
            .with_for_update())).scalar_one_or_none()
        if appt is None or cust is None or appt.customer_id != cust.id:         # solo SUS citas; mismo error si no existe
            raise AppError("NOT_FOUND", "No encontré esa cita entre las del cliente")
        await booking.cancel_appointment_for_customer(self.db, self.ctx, appt, str(a.get("motivo") or "") or None)
        return {"cancelada": True, "cita_id": str(appt.id)}

    async def escalar(self, a: dict[str, Any]) -> dict[str, Any]:
        motivo = rules.clean(str(a.get("motivo") or "sin motivo"), 300)
        self.conv.escalado_a_humano, self.conv.escalado_motivo = True, motivo
        self.escalado = motivo
        await self.db.execute(text("INSERT INTO notifications (tenant_id, canal, destinatario, plantilla, payload) "
                                   "VALUES (:t, 'interna', 'recepcion', 'conversacion_escalada', CAST(:p AS jsonb))"),
                              {"t": self.ctx.tenant_id, "p": _j({"conversation_id": str(self.conv.id), "motivo": motivo})})
        return {"escalada": True, "nota": "Despídete amablemente: una persona del equipo continuará la conversación."}

