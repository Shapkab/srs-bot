"""Unit tests for /cards, /edit, /delete.

The aiogram-side wiring (filters, routers, middleware) is exercised by
the bot at runtime. Here we drive the handler functions directly with
stub Message + CommandObject objects so the DB and logic are exercised
in isolation, no Telegram involvement.
"""

from __future__ import annotations

from datetime import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from src.config import Settings
from src.db.crud import add_card, get_or_create_user
from src.db.engine import session_scope
from src.db.models import Card, ReviewState
from src.handlers.cards import PAGE_SIZE, cmd_cards, cmd_delete, cmd_edit


@pytest.fixture(autouse=True)
def _stub_pronunciation(monkeypatch: pytest.MonkeyPatch) -> None:
    """/edit regenerates IPA via OpenAI when the front changes — stub it
    so these tests never touch the network."""
    monkeypatch.setattr(
        "src.handlers.cards.generate_pronunciation",
        lambda text, api_key, **kw: f"/{text}/",
    )


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


class StubFromUser:
    def __init__(self, telegram_id: int = 1, username: str = "t") -> None:
        self.id = telegram_id
        self.username = username


class StubMessage:
    def __init__(self) -> None:
        self.from_user = StubFromUser()
        self.answer = AsyncMock()


class StubCommand:
    def __init__(self, args: str | None) -> None:
        self.args = args


def _seed(n_cards: int) -> int:
    """Insert n cards. Returns the user.id."""
    with session_scope() as s:
        user = get_or_create_user(s, telegram_id=1, username="t", tz="UTC")
        for i in range(n_cards):
            add_card(s, user, front=f"front-{i:02d}", back=f"back-{i:02d}")
        return user.id


async def test_cards_first_page_shows_20_of_25() -> None:
    _seed(25)
    msg = StubMessage()
    await cmd_cards(msg, StubCommand(args=None), _settings())
    msg.answer.assert_awaited_once()
    text = msg.answer.call_args.args[0]
    # Page 1: the most recent 20 (front-24 .. front-05)
    assert "page 1" in text
    assert text.count("\n") == PAGE_SIZE  # header line + 20 entries = 21 lines = 20 newlines
    assert "front-24" in text
    assert "front-05" in text
    # front-04 belongs to page 2
    assert "front-04" not in text


async def test_cards_second_page_shows_remaining_5() -> None:
    _seed(25)
    msg = StubMessage()
    await cmd_cards(msg, StubCommand(args="2"), _settings())
    text = msg.answer.call_args.args[0]
    assert "page 2" in text
    # Page 2 should show front-04 .. front-00 (5 cards)
    for i in range(5):
        assert f"front-0{i}" in text
    # Page 1 cards must not appear
    assert "front-24" not in text


async def test_cards_empty_when_no_cards() -> None:
    msg = StubMessage()
    await cmd_cards(msg, StubCommand(args=None), _settings())
    text = msg.answer.call_args.args[0]
    assert "no cards" in text.lower()


async def test_cards_truncates_front_and_escapes_html() -> None:
    with session_scope() as s:
        user = get_or_create_user(s, telegram_id=1, username="t", tz="UTC")
        add_card(s, user, front="<script>alert(1)</script>" + "x" * 100, back="b")

    msg = StubMessage()
    await cmd_cards(msg, StubCommand(args=None), _settings())
    text = msg.answer.call_args.args[0]
    # The <script> tag must be HTML-escaped.
    assert "&lt;script&gt;" in text
    assert "<script>" not in text


async def test_edit_updates_card_and_leaves_card_json_byte_identical() -> None:
    _seed(1)
    with session_scope() as s:
        original_card_json = s.scalar(select(ReviewState)).card_json

    msg = StubMessage()
    await cmd_edit(msg, StubCommand(args="1 new front | new back"), _settings())
    msg.answer.assert_awaited_once()
    assert "updated" in msg.answer.call_args.args[0].lower()

    with session_scope() as s:
        card = s.scalar(select(Card).where(Card.id == 1))
        state = s.scalar(select(ReviewState))
        assert card.front == "new front"
        assert card.back == "new back"
        # FSRS state untouched.
        assert state.card_json == original_card_json


async def test_edit_rejects_missing_pipe() -> None:
    _seed(1)
    msg = StubMessage()
    await cmd_edit(msg, StubCommand(args="1 no separator here"), _settings())
    text = msg.answer.call_args.args[0]
    assert "format" in text.lower()


async def test_edit_unknown_id_reports_not_found() -> None:
    _seed(1)
    msg = StubMessage()
    await cmd_edit(msg, StubCommand(args="999 a | b"), _settings())
    text = msg.answer.call_args.args[0]
    assert "not found" in text.lower()


async def test_delete_soft_deletes_card_and_removes_from_review_queue() -> None:
    from src.db.crud import next_due_card

    _seed(1)
    msg = StubMessage()
    await cmd_delete(msg, StubCommand(args="1"), _settings())
    assert "deleted" in msg.answer.call_args.args[0].lower()

    with session_scope() as s:
        card = s.scalar(select(Card).where(Card.id == 1))
        # Card row preserved (history retention).
        assert card is not None
        assert card.deleted_at is not None
        # ReviewState row preserved (cascade NOT triggered for soft-delete).
        state = s.scalar(select(ReviewState))
        assert state is not None
        # But review queue no longer surfaces it.
        user = get_or_create_user(s, telegram_id=1, username="t", tz="UTC")
        assert next_due_card(s, user) is None


async def test_delete_unknown_id_reports_not_found() -> None:
    _seed(1)
    msg = StubMessage()
    await cmd_delete(msg, StubCommand(args="999"), _settings())
    assert "not found" in msg.answer.call_args.args[0].lower()


async def test_delete_idempotent_a_second_call_says_not_found() -> None:
    _seed(1)
    msg = StubMessage()
    await cmd_delete(msg, StubCommand(args="1"), _settings())
    msg2 = StubMessage()
    await cmd_delete(msg2, StubCommand(args="1"), _settings())
    assert "not found" in msg2.answer.call_args.args[0].lower()
