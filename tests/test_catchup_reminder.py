"""Catch-up logic for the daily reminder.

The pure decision function ``should_run_catchup`` is the unit under test
here. The async wrapper ``run_catchup_if_needed`` is exercised
indirectly via the KV state it reads/writes.
"""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import select

from src.db.engine import session_scope
from src.db.models import KV
from src.jobs.daily_reminder import (
    LAST_FIRED_KEY,
    _last_fired,
    _mark_fired,
    should_run_catchup,
)

NINE_AM = time(9, 0)


def _local(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo("UTC"))


def test_should_fire_when_last_fired_yesterday_and_time_passed() -> None:
    now = _local(2026, 5, 17, 10, 0)
    assert should_run_catchup(now, NINE_AM, "2026-05-16") is True


def test_should_not_fire_when_already_fired_today() -> None:
    now = _local(2026, 5, 17, 10, 0)
    assert should_run_catchup(now, NINE_AM, "2026-05-17") is False


def test_should_not_fire_when_now_is_before_reminder_time() -> None:
    now = _local(2026, 5, 17, 8, 30)
    assert should_run_catchup(now, NINE_AM, "2026-05-16") is False


def test_should_fire_when_never_fired_before_and_time_passed() -> None:
    now = _local(2026, 5, 17, 10, 0)
    assert should_run_catchup(now, NINE_AM, None) is True


def test_should_fire_at_exactly_reminder_time() -> None:
    now = _local(2026, 5, 17, 9, 0)
    assert should_run_catchup(now, NINE_AM, "2026-05-16") is True


def test_should_treat_malformed_last_fired_as_not_today() -> None:
    now = _local(2026, 5, 17, 10, 0)
    # Garbage value should not block a fire — we'd rather over-fire than
    # silently never fire again.
    assert should_run_catchup(now, NINE_AM, "garbage") is True


def test_mark_fired_writes_kv_and_last_fired_reads_it_back() -> None:
    from datetime import date

    assert _last_fired() is None
    _mark_fired(date(2026, 5, 17))
    assert _last_fired() == "2026-05-17"

    # Idempotent update.
    _mark_fired(date(2026, 5, 18))
    assert _last_fired() == "2026-05-18"

    with session_scope() as s:
        rows = list(s.scalars(select(KV)))
        assert len(rows) == 1
        assert rows[0].key == LAST_FIRED_KEY
