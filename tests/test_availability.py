"""Motor de disponibilidad y máquina de estados: pruebas puras (sin BD)."""
from datetime import UTC, date, datetime, time, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.modules.appointments.availability import (
    Cap,
    Interval,
    Slot,
    StaffDay,
    can_transition,
    compute_slots,
    rank_staff,
)

TZ = ZoneInfo("America/Bogota")
DAY = date(2026, 10, 20)
LONG_AGO = datetime(2026, 1, 1, tzinfo=UTC)


def at(h: int, m: int = 0) -> datetime:           # hora local de Bogotá -> UTC
    return datetime.combine(DAY, time(h, m), tzinfo=TZ).astimezone(UTC)


def slots(staff, *, dur=30, step=30, buf=0, now=LONG_AGO, min_ahead=timedelta(0), max_ahead=timedelta(days=3650),
          unassigned=(), caps=()):
    return compute_slots(day=DAY, tz=TZ, duration_min=dur, step_min=step, buffer_min=buf, now=now,
                         min_ahead=min_ahead, max_ahead=max_ahead, staff=staff, unassigned=list(unassigned), caps=list(caps))


def starts(result: list[Slot]) -> list[str]:
    return [s.inicio.astimezone(TZ).strftime("%H:%M") for s in result]


def one(tramos=((time(9), time(13)),), **kw) -> StaffDay:
    return StaffDay(staff_id=uuid4(), tramos=list(tramos), **kw)


def test_basic_day_grid():
    assert starts(slots([one()])) == ["09:00", "09:30", "10:00", "10:30", "11:00", "11:30", "12:00", "12:30"]


def test_lunch_break_is_gap_between_tramos():
    s = one(tramos=[(time(9), time(12)), (time(14), time(16))])
    assert starts(slots([s], dur=60, step=60)) == ["09:00", "10:00", "11:00", "14:00", "15:00"]


def test_service_longer_than_remaining_time_does_not_fit():
    assert "12:30" not in starts(slots([one()], dur=60, step=30))      # 12:30-13:30 se sale del turno
    assert starts(slots([one()], dur=60, step=30))[-1] == "12:00"


def test_existing_appointment_blocks_overlapping_slots():
    s = one(appts=[Interval(at(10), at(10, 30))])
    r = starts(slots([s]))
    assert "10:00" not in r and "09:30" in r and "10:30" in r


def test_buffer_is_enforced_on_both_sides():
    s = one(appts=[Interval(at(10), at(10, 30))])
    r = starts(slots([s], buf=15))
    assert "09:30" not in r and "10:00" not in r and "10:30" not in r and "09:00" in r and "11:00" in r


def test_schedule_block_removes_slots():
    s = one(blocks=[Interval(at(11), at(12))])
    r = starts(slots([s]))
    assert "11:00" not in r and "11:30" not in r and "10:30" in r and "12:00" in r


def test_min_and_max_anticipation():
    now = at(9, 50)
    r = starts(slots([one()], now=now, min_ahead=timedelta(hours=1)))
    assert r[0] == "11:00"                                              # >= 10:50 y en la rejilla de 30 min
    r2 = starts(slots([one()], now=at(8), max_ahead=timedelta(hours=2)))
    assert r2 == ["09:00", "09:30", "10:00"]


def test_two_staff_slot_lists_all_free_professionals():
    a, b = one(), one(appts=[Interval(at(9), at(9, 30))])
    r = slots([a, b])
    assert set(r[0].staff_ids) == {a.staff_id}                          # 09:00 solo A
    assert set(r[1].staff_ids) == {a.staff_id, b.staff_id}              # 09:30 ambos


def test_simultaneous_cap_limits_business_wide():
    other = [Interval(at(10), at(10, 30))]
    s = one()
    assert "10:00" not in starts(slots([s], caps=[Cap(cupos=1, appts=other)]))
    assert "10:00" in starts(slots([s], caps=[Cap(cupos=2, appts=other)]))


def test_unassigned_appointments_consume_capacity():
    unassigned = [Interval(at(10), at(10, 30))]
    assert "10:00" not in starts(slots([one()], unassigned=unassigned))               # 1 libre, 1 prometida
    assert "10:00" in starts(slots([one(), one()], unassigned=unassigned))            # 2 libres, 1 prometida


def test_no_schedule_no_slots():
    assert slots([one(tramos=[])]) == []


def test_rank_staff_least_busy_first_with_stable_tiebreak():
    a, b, c = uuid4(), uuid4(), uuid4()
    r = rank_staff([a, b, c], {a: 3, b: 1, c: 1})
    assert r[2] == a and set(r[:2]) == {b, c}
    assert rank_staff([a, b, c], {a: 3, b: 1, c: 1}) == r


def test_state_machine():
    assert can_transition("pendiente", "confirmada") and can_transition("en_atencion", "atendida")
    assert not can_transition("atendida", "cancelada") and not can_transition("cancelada", "pendiente")
    assert not can_transition("confirmada", "atendida")                                # debe pasar por en_atencion
