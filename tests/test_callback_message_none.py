"""Telegram strips ``message`` from CallbackQuery for callbacks older
than ~48h. The handlers must not AttributeError on that path.
"""

from __future__ import annotations

from datetime import time
from pathlib import Path
from unittest.mock import AsyncMock

from sqlalchemy import select

from src.config import Settings
from src.db.crud import add_card, get_or_create_user
from src.db.engine import session_scope
from src.db.models import ReviewLog, ReviewState
from src.handlers.review import cb_rate, cb_show_answer


class StubCallback:
    """Minimal stand-in for aiogram.types.CallbackQuery with .message = None."""

    def __init__(self, data: str) -> None:
        self.data = data
        self.message = None
        self.answer = AsyncMock()


def _seed_card_and_get_state_id() -> int:
    with session_scope() as s:
        user = get_or_create_user(s, telegram_id=1, username="t", tz="UTC")
        add_card(s, user, front="a", back="b")
        state = s.scalar(select(ReviewState))
        assert state is not None
        return state.id


def _settings() -> Settings:
    return Settings(
        bot_token="x",
        db_path=Path("unused.db"),
        owner_telegram_id=1,
        timezone="UTC",
        reminder_time=time(9, 0),
        log_level="INFO",
        image_dir=Path("/tmp/test-images"),
    )


async def test_cb_show_answer_handles_stale_message() -> None:
    state_id = _seed_card_and_get_state_id()
    cb = StubCallback(data=f"rv:show:{state_id}")

    # Must not raise.
    await cb_show_answer(cb)

    cb.answer.assert_awaited_once()
    _, kwargs = cb.answer.call_args
    assert kwargs.get("show_alert") is True


async def test_cb_rate_handles_stale_message_but_still_persists() -> None:
    state_id = _seed_card_and_get_state_id()
    # rating value 3 == Rating.GOOD
    cb = StubCallback(data=f"rv:rate:{state_id}:3")

    # Must not raise.
    await cb_rate(cb, _settings())

    # DB write must still have happened — the user did rate the card.
    with session_scope() as s:
        state = s.scalar(select(ReviewState))
        assert state is not None
        assert state.reps == 1
        logs = s.scalars(select(ReviewLog)).all()
        assert len(logs) == 1

    cb.answer.assert_awaited_once()
    _, kwargs = cb.answer.call_args
    assert kwargs.get("show_alert") is True
