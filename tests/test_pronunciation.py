"""IPA pronunciation: the OpenAI helper, /add blocking behaviour,
/edit regeneration, and /review rendering.

No test touches the network — the OpenAI client is replaced with a
programmable fake, and the handler-level tests stub
``generate_pronunciation`` directly.
"""

from __future__ import annotations

import asyncio
from datetime import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from src.config import Settings
from src.db.crud import add_card, get_or_create_user
from src.db.engine import session_scope
from src.db.models import Card
from src.handlers.add_card import cmd_add
from src.handlers.cards import cmd_edit
from src.handlers.review import _format_front
from src.utils.pronunciation import generate_pronunciation


def _settings() -> Settings:
    return Settings(
        bot_token="x",
        db_path=Path("unused.db"),
        owner_telegram_id=1,
        timezone="UTC",
        reminder_time=time(9, 0),
        log_level="INFO",
        image_dir=Path("/tmp/test-images"),
        openai_api_key="sk-test",
    )


# ---------------------------------------------------------------------------
# A programmable fake OpenAI client


def _make_response(content: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


class _FakeClient:
    """Plays back a pre-programmed list of outcomes — a str is returned
    as a completion, an Exception instance is raised."""

    def __init__(self, outcomes: list) -> None:
        self._outcomes = outcomes
        self.calls = 0
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create)
        )

    def _create(self, **kwargs):
        outcome = self._outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return _make_response(outcome)


def _patch_openai(monkeypatch: pytest.MonkeyPatch, outcomes: list) -> _FakeClient:
    client = _FakeClient(outcomes)
    monkeypatch.setattr(
        "src.utils.pronunciation.OpenAI", lambda api_key: client
    )
    # No real backoff sleeps in tests.
    monkeypatch.setattr("src.utils.pronunciation.time.sleep", lambda _s: None)
    return client


# ---------------------------------------------------------------------------
# Helper: generate_pronunciation


def test_generate_pronunciation_normalizes_missing_slashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_openai(monkeypatch, ["həˈloʊ"])  # model returned bare IPA
    assert generate_pronunciation("hello", "sk-test") == "/həˈloʊ/"


def test_generate_pronunciation_keeps_existing_slashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_openai(monkeypatch, ["/riːtʃ aʊt tuː/"])
    assert generate_pronunciation("reach out to", "sk-test") == "/riːtʃ aʊt tuː/"


def test_generate_pronunciation_retries_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _patch_openai(
        monkeypatch,
        [RuntimeError("rate limited"), RuntimeError("rate limited"), "wɜːrd"],
    )
    assert generate_pronunciation("word", "sk-test") == "/wɜːrd/"
    assert client.calls == 3


def test_generate_pronunciation_raises_after_all_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _patch_openai(
        monkeypatch,
        [RuntimeError("boom")] * 3,
    )
    with pytest.raises(RuntimeError, match="boom"):
        generate_pronunciation("word", "sk-test", max_retries=3)
    assert client.calls == 3


# ---------------------------------------------------------------------------
# /add blocking behaviour


class _StubFromUser:
    id = 1
    username = "t"


class _StubMessage:
    def __init__(self, text: str) -> None:
        self.from_user = _StubFromUser()
        self.text = text
        self.answer = AsyncMock()


def test_add_creates_card_with_pronunciation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "src.handlers.add_card.generate_pronunciation",
        lambda text, api_key, **kw: "/həˈloʊ/",
    )
    msg = _StubMessage("/add hello | a greeting")
    asyncio.run(cmd_add(msg, _settings()))

    assert "Added card" in msg.answer.call_args.args[0]
    with session_scope() as s:
        card = s.scalar(select(Card))
        assert card is not None
        assert card.front == "hello"
        assert card.front_pronunciation == "/həˈloʊ/"


def test_add_blocked_when_openai_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Testing-checklist contract: OpenAI failure during /add shows a
    user-friendly error and does NOT create the card."""

    def _boom(text: str, api_key: str, **kw) -> str:
        raise RuntimeError("openai down")

    monkeypatch.setattr("src.handlers.add_card.generate_pronunciation", _boom)
    msg = _StubMessage("/add hello | a greeting")
    asyncio.run(cmd_add(msg, _settings()))

    assert "Failed to generate pronunciation" in msg.answer.call_args.args[0]
    with session_scope() as s:
        assert s.scalar(select(Card)) is None


# ---------------------------------------------------------------------------
# /review rendering


def test_format_front_includes_ipa_with_separator() -> None:
    card = Card(front="hello", back="greeting", front_pronunciation="/həˈloʊ/")
    out = _format_front(card)
    assert "<b>hello</b>" in out
    assert "───" in out
    assert "/həˈloʊ/" in out


def test_format_front_without_pronunciation_is_plain() -> None:
    card = Card(front="hello", back="greeting", front_pronunciation=None)
    out = _format_front(card)
    assert out == "<b>hello</b>"
    assert "───" not in out


# ---------------------------------------------------------------------------
# /edit regeneration


class _StubCommand:
    def __init__(self, args: str) -> None:
        self.args = args


def _seed_card(front: str, back: str, ipa: str) -> int:
    with session_scope() as s:
        user = get_or_create_user(s, telegram_id=1, username="t", tz="UTC")
        card = add_card(s, user, front=front, back=back, front_pronunciation=ipa)
        return card.id


def test_edit_regenerates_pronunciation_when_front_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    card_id = _seed_card("hello", "greeting", "/həˈloʊ/")
    monkeypatch.setattr(
        "src.handlers.cards.generate_pronunciation",
        lambda text, api_key, **kw: "/ɡʊdˈbaɪ/",
    )

    msg = _StubMessage("")
    asyncio.run(cmd_edit(msg, _StubCommand(f"{card_id} goodbye | farewell"), _settings()))

    with session_scope() as s:
        card = s.scalar(select(Card).where(Card.id == card_id))
        assert card.front == "goodbye"
        assert card.front_pronunciation == "/ɡʊdˈbaɪ/"


def test_edit_keeps_pronunciation_when_front_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    card_id = _seed_card("hello", "greeting", "/həˈloʊ/")
    # If this were called, the IPA would change — assert it is NOT.
    monkeypatch.setattr(
        "src.handlers.cards.generate_pronunciation",
        lambda text, api_key, **kw: "/WRONG/",
    )

    msg = _StubMessage("")
    asyncio.run(cmd_edit(msg, _StubCommand(f"{card_id} hello | a warm greeting"), _settings()))

    with session_scope() as s:
        card = s.scalar(select(Card).where(Card.id == card_id))
        assert card.back == "a warm greeting"
        assert card.front_pronunciation == "/həˈloʊ/"  # untouched


def test_edit_survives_pronunciation_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A regeneration failure on /edit does NOT block the edit — front
    and back still save; the user is told the IPA is now stale."""
    card_id = _seed_card("hello", "greeting", "/həˈloʊ/")

    def _boom(text: str, api_key: str, **kw) -> str:
        raise RuntimeError("openai down")

    monkeypatch.setattr("src.handlers.cards.generate_pronunciation", _boom)

    msg = _StubMessage("")
    asyncio.run(cmd_edit(msg, _StubCommand(f"{card_id} goodbye | farewell"), _settings()))

    assert "regeneration failed" in msg.answer.call_args.args[0].lower()
    with session_scope() as s:
        card = s.scalar(select(Card).where(Card.id == card_id))
        assert card.front == "goodbye"  # edit still applied
        assert card.back == "farewell"
