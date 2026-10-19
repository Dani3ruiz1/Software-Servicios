"""Ocupación = minutos reservados / minutos disponibles. Puro (sin BD): los datos llegan ya cargados."""
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

Interval = tuple[datetime, datetime]


def merge(intervals: list[Interval]) -> list[Interval]:
    out: list[Interval] = []
    for a, b in sorted(intervals):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def overlap_min(a: Interval, b: Interval) -> float:
    s, e = max(a[0], b[0]), min(a[1], b[1])
    return max((e - s).total_seconds() / 60, 0.0)


def day_tramos(day: date, tramos: list[tuple[time, time]], tz: ZoneInfo) -> list[Interval]:
    return [(datetime.combine(day, a, tzinfo=tz).astimezone(UTC), datetime.combine(day, b, tzinfo=tz).astimezone(UTC))
            for a, b in tramos]


def staff_day_minutes(day: date, tramos: list[tuple[time, time]], blocks: list[Interval], appts: list[Interval],
                      tz: ZoneInfo) -> tuple[float, float]:
    """(disponible, reservado) en minutos para un profesional en un día.
    Disponible = horario − bloqueos (fusionados, para no restar dos veces). Reservado = citas recortadas a lo disponible,
    así una cita fuera de horario no puede dar más de 100 %."""
    horario = merge(day_tramos(day, tramos, tz))
    bloq = merge(blocks)
    libres: list[Interval] = []
    for t in horario:
        cur = t[0]
        for b in bloq:
            if b[1] <= cur or b[0] >= t[1]:
                continue
            if b[0] > cur:
                libres.append((cur, b[0]))
            cur = max(cur, b[1])
        if cur < t[1]:
            libres.append((cur, t[1]))
    disponible = sum((b - a).total_seconds() / 60 for a, b in libres)
    reservado = sum(overlap_min(a, libre) for a in merge(appts) for libre in libres)
    return disponible, reservado


def pct(reservado: float, disponible: float) -> float:
    return round(100 * reservado / disponible, 1) if disponible > 0 else 0.0


def daterange(desde: date, hasta: date) -> list[date]:
    return [desde + timedelta(days=i) for i in range((hasta - desde).days + 1)]
