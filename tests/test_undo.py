"""/undo restores ReviewState to its pre-review snapshot and removes
the ReviewLog row. Outside the 10-minute window, /undo refuses.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

from sqlalchemy import select

from src.config import Settings
from src.db.crud import add_card, get_or_create_user, persist_review
from src.db.engine import session_scope
from src.db.models import Rating, ReviewLog, ReviewState
from src.handlers.undo import cmd_undo
from src.srs.scheduler import apply_review


def _settings() -> Settings:
    return Settings(
        bot_token="x",
        db_path=Path("unused.db"),
        owner_telegram_id=1,
        timezone="UTC",
        reminder_time="09:00",
        log_level="INFO",
    )


class StubFromUser:
    id = 1
    username = "t"


class StubMessage:
    def __init__(self) -> None:
        self.from_user = StubFromUser()
        self.answer = AsyncMock()


def _seed_one_review() -> tuple[str, datetime | None, int]:
    """Add a card, run one Good review, return (pre_review_card_json, pre_due, ...).

    Returns a tuple of (original_card_json, original_due, original_reps)
    captured BEFORE the review took place.
    """
    with session_scope() as s:
        user = get_or_create_user(s, telegram_id=1, username="t", tz="UTC")
        add_card(s, user, front="x", back="y")
        state = s.scalar(select(ReviewState))
        pre_card_json = state.card_json
        pre_due = state.due
        pre_reps = state.reps

    with session_scope() as s:
        state = s.scalar(select(ReviewState))
        result = apply_review(
            card_json=state.card_json,
            prev_due=state.due,
            prev_last_review=state.last_review,
            rating=Rating.GOOD,
        )
        assert persist_review(s, state, Rating.GOOD, result) is True

    return pre_card_json, pre_due, pre_reps


async def test_undo_restores_pre_review_state_and_deletes_log() -> None:
    pre_card_json, pre_due, pre_reps = _seed_one_review()

    # Sanity: post-review state is advanced.
    with session_scope() as s:
        state = s.scalar(select(ReviewState))
        assert state.reps == pre_reps + 1
        assert state.card_json != pre_card_json
        assert len(s.scalars(select(ReviewLog)).all()) == 1

    msg = StubMessage()
    await cmd_undo(msg, _settings())
    assert "undone" in msg.answer.call_args.args[0].lower()

    with session_scope() as s:
        state = s.scalar(select(ReviewState))
        assert state.card_json == pre_card_json
        assert state.reps == pre_reps
        # last_review reverted to None (pre-first-review).
        assert state.last_review is None
        # ReviewLog row removed.
        assert s.scalars(select(ReviewLog)).all() == []


async def test_undo_with_no_reviews_says_nothing_to_undo() -> None:
    with session_scope() as s:
        get_or_create_user(s, telegram_id=1, username="t", tz="UTC")
    msg = StubMessage()
    await cmd_undo(msg, _settings())
    assert "nothing to undo" in msg.answer.call_args.args[0].lower()


async def test_undo_refuses_outside_10_minute_window() -> None:
    _seed_one_review()
    # Backdate the log so it's older than the window.
    with session_scope() as s:
        log = s.scalar(select(ReviewLog))
        log.reviewed_at = datetime.now(UTC) - timedelta(minutes=11)

    msg = StubMessage()
    await cmd_undo(msg, _settings())
    text = msg.answer.call_args.args[0].lower()
    assert "older than 10" in text or "too late" in text

    # Log row must NOT have been deleted on refusal.
    with session_scope() as s:
        assert len(s.scalars(select(ReviewLog)).all()) == 1


async def test_undo_again_decrements_lapses() -> None:
    # First a Good review (so we can force the card due again and rate Again).
    _seed_one_review()
    with session_scope() as s:
        state = s.scalar(select(ReviewState))
        state.due = datetime.now(UTC) - timedelta(days=1)
        first_lapses = state.lapses

    with session_scope() as s:
        state = s.scalar(select(ReviewState))
        result = apply_review(
            card_json=state.card_json,
            prev_due=state.due,
            prev_last_review=state.last_review,
            rating=Rating.AGAIN,
        )
        assert persist_review(s, state, Rating.AGAIN, result) is True

    with session_scope() as s:
        state = s.scalar(select(ReviewState))
        assert state.lapses == first_lapses + 1

    msg = StubMessage()
    await cmd_undo(msg, _settings())

    with session_scope() as s:
        state = s.scalar(select(ReviewState))
        assert state.lapses == first_lapses
