"""/repair walks the user's live cards and soft-deletes those whose
``ReviewState.card_json`` no longer round-trips through ``fsrs.Card``.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

from sqlalchemy import select

from src.config import Settings
from src.db.crud import add_card, get_or_create_user
from src.db.engine import session_scope
from src.db.models import Card, ReviewState
from src.handlers.repair import cmd_repair


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


def _seed_cards(n: int) -> list[int]:
    """Add n healthy cards, return their state ids."""
    with session_scope() as s:
        user = get_or_create_user(s, telegram_id=1, username="t", tz="UTC")
        for i in range(n):
            add_card(s, user, front=f"f{i}", back=f"b{i}")
        return [st.id for st in s.scalars(select(ReviewState)).all()]


def _corrupt_state(state_id: int, payload: str = '{"bad":"json"') -> None:
    with session_scope() as s:
        st = s.scalar(select(ReviewState).where(ReviewState.id == state_id))
        st.card_json = payload


async def test_repair_soft_deletes_corrupt_and_leaves_healthy_alone() -> None:
    state_ids = _seed_cards(3)
    # Corrupt the middle one's card_json.
    _corrupt_state(state_ids[1])

    msg = StubMessage()
    await cmd_repair(msg, _settings())

    text = msg.answer.call_args.args[0]
    assert "1 corrupt card" in text.lower() or "1 corrupt card(s)" in text
    assert "2 card(s) checked clean" in text

    with session_scope() as s:
        cards = s.scalars(select(Card).order_by(Card.id)).all()
        # The corrupt card's owner Card has deleted_at set.
        assert cards[1].deleted_at is not None
        # The other two are untouched.
        assert cards[0].deleted_at is None
        assert cards[2].deleted_at is None
        # ReviewState rows survive (we soft-delete the Card, not the state).
        assert len(s.scalars(select(ReviewState)).all()) == 3


async def test_repair_reports_nothing_when_all_healthy() -> None:
    _seed_cards(2)

    msg = StubMessage()
    await cmd_repair(msg, _settings())
    text = msg.answer.call_args.args[0]
    assert "nothing to repair" in text.lower()
    assert "2 card(s)" in text

    with session_scope() as s:
        cards = s.scalars(select(Card)).all()
        assert all(c.deleted_at is None for c in cards)


async def test_repair_skips_already_soft_deleted_cards() -> None:
    """An already-soft-deleted card should not be re-touched by /repair
    even if its card_json is corrupt; it's not in the queue anyway."""
    from datetime import UTC, datetime

    state_ids = _seed_cards(2)
    pre_delete_at = datetime(2020, 1, 1, tzinfo=UTC)
    # Soft-delete card 1 with a known (old) timestamp.
    with session_scope() as s:
        cards = s.scalars(select(Card).order_by(Card.id)).all()
        cards[0].deleted_at = pre_delete_at
        card1_id = cards[0].id
        card2_id = cards[1].id
    # Also corrupt card 1's state (which is already deleted) AND card 2.
    _corrupt_state(state_ids[0])
    _corrupt_state(state_ids[1])

    msg = StubMessage()
    await cmd_repair(msg, _settings())
    text = msg.answer.call_args.args[0]
    # Only card 2 should be reported as repaired this run.
    assert "1 corrupt card" in text
    assert f"#{card2_id}" in text
    assert f"#{card1_id}" not in text

    with session_scope() as s:
        cards = s.scalars(select(Card).order_by(Card.id)).all()
        # Card 1's deleted_at is still the old (2020) timestamp, not
        # the now timestamp /repair would have stamped.
        assert cards[0].deleted_at is not None
        assert cards[0].deleted_at.year == 2020
        # Card 2 is now soft-deleted.
        assert cards[1].deleted_at is not None
        assert cards[1].deleted_at.year >= 2026
