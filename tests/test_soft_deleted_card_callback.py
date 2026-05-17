"""Soft-deleted cards must not resurrect through a stale review keyboard.

Scenario: user runs ``/review``, gets the inline keyboard for card X,
then runs ``/delete X``. The Telegram message with the keyboard is
still visible. A tap on "Show answer" or any rating must NOT advance
the deleted card — no ReviewLog row, no ReviewState change.
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from pathlib import Path
from unittest.mock import AsyncMock

from sqlalchemy import select

from src.config import Settings
from src.db.crud import add_card, get_or_create_user
from src.db.engine import session_scope
from src.db.models import Card, ReviewLog, ReviewState
from src.handlers.review import cb_rate, cb_show_answer


def _settings() -> Settings:
    return Settings(
        bot_token="x",
        db_path=Path("unused.db"),
        owner_telegram_id=1,
        timezone="UTC",
        reminder_time=time(9, 0),
        log_level="INFO",
    )


class StubMessage:
    """Stand-in for the Message attached to a CallbackQuery."""

    def __init__(self) -> None:
        self.edit_text = AsyncMock()


class StubCallback:
    def __init__(self, data: str) -> None:
        self.data = data
        self.message = StubMessage()
        self.answer = AsyncMock()


def _seed_and_soft_delete() -> int:
    """Add a card+state, then mark the card deleted. Returns state_id."""
    with session_scope() as s:
        user = get_or_create_user(s, telegram_id=1, username="t", tz="UTC")
        add_card(s, user, front="zombie", back="resurrects-on-tap")
        state = s.scalar(select(ReviewState))
        assert state is not None
        state_id = state.id

    with session_scope() as s:
        card = s.scalar(select(Card))
        card.deleted_at = datetime.now(UTC)
    return state_id


async def test_cb_show_answer_rejects_deleted_card() -> None:
    state_id = _seed_and_soft_delete()
    cb = StubCallback(data=f"rv:show:{state_id}")

    await cb_show_answer(cb)

    # User-facing alert.
    cb.answer.assert_awaited_once()
    args, kwargs = cb.answer.call_args
    text = args[0] if args else kwargs.get("text", "")
    assert "deleted" in text.lower()
    assert kwargs.get("show_alert") is True
    # Critically, NO edit_text — the keyboard stays put.
    cb.message.edit_text.assert_not_called()


async def test_cb_rate_rejects_deleted_card_and_writes_nothing() -> None:
    state_id = _seed_and_soft_delete()
    # rating value 3 == Rating.GOOD
    cb = StubCallback(data=f"rv:rate:{state_id}:3")

    # Snapshot state for comparison.
    with session_scope() as s:
        before = s.scalar(select(ReviewState))
        before_card_json = before.card_json
        before_reps = before.reps
        before_lapses = before.lapses

    await cb_rate(cb, _settings())

    # User-facing alert.
    cb.answer.assert_awaited_once()
    args, kwargs = cb.answer.call_args
    text = args[0] if args else kwargs.get("text", "")
    assert "deleted" in text.lower()
    assert kwargs.get("show_alert") is True
    cb.message.edit_text.assert_not_called()

    # No DB side effects.
    with session_scope() as s:
        after = s.scalar(select(ReviewState))
        assert after.card_json == before_card_json
        assert after.reps == before_reps
        assert after.lapses == before_lapses
        assert s.scalars(select(ReviewLog)).all() == []
