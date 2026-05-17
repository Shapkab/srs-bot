"""When lapses cross ``LEECH_LAPSE_THRESHOLD`` the card is auto-suspended
and stops surfacing in ``next_due_card`` / ``due_count``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from src.db import engine as engine_mod
from src.db.crud import (
    LEECH_LAPSE_THRESHOLD,
    add_card,
    due_count,
    get_or_create_user,
    next_due_card,
    persist_review,
)
from src.db.engine import init_db, session_scope
from src.db.models import Rating, ReviewState
from src.srs.scheduler import apply_review


@pytest.fixture(autouse=True)
def fresh_db(tmp_path: Path):
    engine_mod._engine = None
    engine_mod._SessionLocal = None
    init_db(tmp_path / "test.db")
    yield


def _rate(rating: Rating) -> bool:
    """Find a due ReviewState (or force the only one due), apply ``rating``."""
    with session_scope() as s:
        state = s.scalar(select(ReviewState))
        assert state is not None
        # Force due-now in case FSRS pushed it forward.
        state.due = datetime.now(UTC) - timedelta(seconds=1)

    with session_scope() as s:
        state = s.scalar(select(ReviewState))
        result = apply_review(
            card_json=state.card_json,
            prev_due=state.due,
            prev_last_review=state.last_review,
            rating=rating,
        )
        return persist_review(s, state, rating, result)


def test_card_with_threshold_lapses_is_suspended_and_removed_from_queue() -> None:
    with session_scope() as s:
        user = get_or_create_user(s, telegram_id=1, username="t", tz="UTC")
        add_card(s, user, front="leech", back="trap")

    for _ in range(LEECH_LAPSE_THRESHOLD):
        assert _rate(Rating.AGAIN) is True

    with session_scope() as s:
        state = s.scalar(select(ReviewState))
        assert state.lapses == LEECH_LAPSE_THRESHOLD
        assert state.suspended_at is not None

        user = get_or_create_user(s, telegram_id=1, username="t", tz="UTC")
        assert next_due_card(s, user) is None
        assert due_count(s, user) == 0


def test_card_with_fewer_lapses_stays_in_queue() -> None:
    with session_scope() as s:
        user = get_or_create_user(s, telegram_id=1, username="t", tz="UTC")
        add_card(s, user, front="ok", back="ok")

    # One short of the threshold.
    for _ in range(LEECH_LAPSE_THRESHOLD - 1):
        assert _rate(Rating.AGAIN) is True

    with session_scope() as s:
        state = s.scalar(select(ReviewState))
        assert state.lapses == LEECH_LAPSE_THRESHOLD - 1
        assert state.suspended_at is None

        # Card is still due (we keep forcing it in _rate); the queue must
        # still surface it.
        state.due = datetime.now(UTC) - timedelta(seconds=1)

    with session_scope() as s:
        user = get_or_create_user(s, telegram_id=1, username="t", tz="UTC")
        assert next_due_card(s, user) is not None


def test_default_threshold_is_eight() -> None:
    # If this changes, update the README and the user-facing copy.
    assert LEECH_LAPSE_THRESHOLD == 8
