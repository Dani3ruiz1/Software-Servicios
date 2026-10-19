"""Motor de disponibilidad (regla 7). Funciones PURAS: sin BD ni reloj implícito → fáciles de probar.

Cruza: horario semanal, bloqueos, citas existentes, duración, buffer, cupos simultáneos y
anticipación mínima/máxima. Toda la aritmética se hace en UTC (los tramos del horario son hora
local del tenant y se convierten una sola vez, así el horario de verano no corrompe las sumas).
"""
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

# Estados de cita: qué transiciones son válidas (regla de negocio de la agenda)
TRANSITIONS: dict[str, frozenset[str]] = {
    "pendiente": frozenset({"confirmada", "en_atencion", "cancelada", "no_asistio"}),
    "confirmada": frozenset({"en_atencion", "cancelada", "no_asistio"}),
    "en_atencion": frozenset({"atendida", "cancelada"}),
    "atendida": frozenset(),
    "cancelada": frozenset(),
    "no_asistio": frozenset(),
}
ESTADOS_QUE_OCUPAN = ("pendiente", "confirmada", "en_atencion", "atendida")   # cancelada/no_asistio liberan


def can_transition(actual: str, nuevo: str) -> bool:
    return nuevo in TRANSITIONS.get(actual, frozenset())


@dataclass(frozen=True)
class Interval:
    start: datetime
    end: datetime

    def overlaps(self, start: datetime, end: datetime) -> bool:
        return self.start < end and start < self.end


@dataclass
class StaffDay:
    staff_id: UUID
    tramos: list[tuple[time, time]]                       # horario local de ese día de la semana
    blocks: list[Interval] = field(default_factory=list)
    appts: list[Interval] = field(default_factory=list)   # citas que ocupan al profesional


@dataclass
class Cap:
    """Regla de cupos: máximo `cupos` citas simultáneas dentro del alcance (`appts` ya filtradas)."""
    cupos: int
    appts: list[Interval]


@dataclass(frozen=True)
class Slot:
    inicio: datetime
    fin: datetime
    staff_ids: tuple[UUID, ...]


def staff_is_free(sd: StaffDay, start: datetime, end: datetime, buffer: timedelta) -> bool:
    if any(b.overlaps(start, end) for b in sd.blocks):
        return False
    # Buffer simétrico: separación mínima entre el fin de una cita y el inicio de la otra
    return not any(a.overlaps(start - buffer, end + buffer) for a in sd.appts)


def compute_slots(*, day: date, tz: ZoneInfo, duration_min: int, step_min: int, buffer_min: int,
                  now: datetime, min_ahead: timedelta, max_ahead: timedelta, staff: list[StaffDay],
                  unassigned: list[Interval], caps: list[Cap]) -> list[Slot]:
    dur, buf = timedelta(minutes=duration_min), timedelta(minutes=buffer_min)
    earliest, latest = now + min_ahead, now + max_ahead
    free: dict[datetime, list[UUID]] = {}
    for sd in staff:
        for t_ini, t_fin in sd.tramos:
            cur = datetime.combine(day, t_ini, tzinfo=tz).astimezone(UTC)
            end_t = datetime.combine(day, t_fin, tzinfo=tz).astimezone(UTC)
            while cur + dur <= end_t:
                if earliest <= cur <= latest and staff_is_free(sd, cur, cur + dur, buf):
                    free.setdefault(cur, []).append(sd.staff_id)
                cur += timedelta(minutes=step_min)
    out: list[Slot] = []
    for start in sorted(free):
        end = start + dur
        ids = free[start]
        if any(sum(a.overlaps(start, end) for a in c.appts) >= c.cupos for c in caps):
            continue
        # Citas sin profesional ("en recepción") consumen capacidad: no se puede prometer más de lo que hay
        if len(ids) <= sum(a.overlaps(start, end) for a in unassigned):
            continue
        out.append(Slot(start, end, tuple(ids)))
    return out


def rank_staff(candidates: list[UUID], citas_del_dia: dict[UUID, int]) -> list[UUID]:
    """Asignación automática: primero el profesional con MENOS citas ese día (desempate estable por id)."""
    return sorted(candidates, key=lambda i: (citas_del_dia.get(i, 0), str(i)))


def free_staff_exact(*, tz: ZoneInfo, start: datetime, end: datetime, buffer_min: int, staff: list[StaffDay],
                     unassigned: list[Interval], caps: list[Cap]) -> list[UUID]:
    """Profesionales libres para una hora EXACTA (sin depender de la rejilla de franjas).
    Misma lógica que compute_slots: dentro de su horario, sin bloqueos/citas/buffer, sin romper cupos."""
    buf = timedelta(minutes=buffer_min)
    day = start.astimezone(tz).date()
    ids: list[UUID] = []
    for sd in staff:
        en_horario = any(datetime.combine(day, a, tzinfo=tz).astimezone(UTC) <= start
                         and end <= datetime.combine(day, b, tzinfo=tz).astimezone(UTC) for a, b in sd.tramos)
        if en_horario and staff_is_free(sd, start, end, buf):
            ids.append(sd.staff_id)
    if any(sum(a.overlaps(start, end) for a in c.appts) >= c.cupos for c in caps):
        return []
    if len(ids) <= sum(a.overlaps(start, end) for a in unassigned):
        return []
    return ids
