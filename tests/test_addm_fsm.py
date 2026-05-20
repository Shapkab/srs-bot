"""/addm — multi-step FSM happy path.

Drives the FSM step handlers directly with a real ``FSMContext`` bound
to ``MemoryStorage``. No bot or dispatcher needed.
"""

from __future__ import annotations

from datetime import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import select

from src.config import Settings
from src.db.engine import session_scope
from src.db.models import Card
from src.handlers.add_card import (
    AddCardFSM,
    cmd_addm_back,
    cmd_addm_cancel,
    cmd_addm_front,
    cmd_addm_skip_tags,
    cmd_addm_start,
    cmd_addm_tags,
)


@pytest.fixture(autouse=True)
def _stub_pronunciation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Card creation now blocks on OpenAI IPA generation. Stub it so the
    FSM tests never touch the network."""
    monkeypatch.setattr(
        "src.handlers.add_card.generate_pronunciation",
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
    id = 1
    username = "t"


class StubMessage:
    def __init__(self, text: str = "") -> None:
        self.from_user = StubFromUser()
        self.text = text
        self.answer = AsyncMock()


def _fresh_state() -> FSMContext:
    return FSMContext(
        storage=MemoryStorage(),
        key=StorageKey(bot_id=0, user_id=1, chat_id=1),
    )


async def test_addm_full_happy_path_with_tags() -> None:
    state = _fresh_state()

    # 1) /addm — enter FSM
    await cmd_addm_start(StubMessage(), state)
    assert await state.get_state() == AddCardFSM.front.state

    # 2) Send front
    await cmd_addm_front(StubMessage(text="reach out to"), state)
    assert await state.get_state() == AddCardFSM.back.state
    assert (await state.get_data())["front"] == "reach out to"

    # 3) Send back
    await cmd_addm_back(StubMessage(text="contact someone, usually for help"), state)
    assert await state.get_state() == AddCardFSM.tags.state

    # 4) Send tags
    final_msg = StubMessage(text="phrasal-verbs, common")
    await cmd_addm_tags(final_msg, state, _settings())

    # FSM cleared, card persisted.
    assert await state.get_state() is None
    assert "Added card #1" in final_msg.answer.call_args.args[0]
    with session_scope() as s:
        card = s.scalar(select(Card))
        assert card is not None
        assert card.front == "reach out to"
        assert card.back == "contact someone, usually for help"
        assert card.tags == "phrasal-verbs, common"


async def test_addm_skip_tags() -> None:
    state = _fresh_state()
    await cmd_addm_start(StubMessage(), state)
    await cmd_addm_front(StubMessage(text="x"), state)
    await cmd_addm_back(StubMessage(text="y"), state)

    final_msg = StubMessage(text="/skip")
    await cmd_addm_skip_tags(final_msg, state, _settings())

    assert await state.get_state() is None
    with session_scope() as s:
        card = s.scalar(select(Card))
        assert card.front == "x"
        assert card.back == "y"
        assert card.tags is None


async def test_addm_rejects_empty_front() -> None:
    state = _fresh_state()
    await cmd_addm_start(StubMessage(), state)
    msg = StubMessage(text="   ")
    await cmd_addm_front(msg, state)
    # Stayed on the same state; nothing stored.
    assert await state.get_state() == AddCardFSM.front.state
    assert "empty" in msg.answer.call_args.args[0].lower()


async def test_addm_cancel_clears_state_without_inserting() -> None:
    state = _fresh_state()
    await cmd_addm_start(StubMessage(), state)
    await cmd_addm_front(StubMessage(text="x"), state)
    msg = StubMessage(text="/cancel")
    await cmd_addm_cancel(msg, state)
    assert await state.get_state() is None
    assert "cancelled" in msg.answer.call_args.args[0].lower()
    with session_scope() as s:
        assert s.scalar(select(Card)) is None
