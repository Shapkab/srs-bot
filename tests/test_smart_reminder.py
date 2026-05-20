"""Smart reminders: hourly backlog check + 24h spam guard + /remind.

The pure decision (``_due_for_reminder``) and the async tick
(``send_smart_reminders``) are exercised directly; Telegram is stubbed
with AsyncMock so no network call happens.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

from sqlalchemy import select

from src.config import Settings
from src.db.crud import add_card, get_or_create_user
from src.db.engine import session_scope
from src.db.models import User
from src.jobs.smart_reminder import (
    REMINDER_COOLDOWN,
    _due_for_reminder,
    send_smart_reminders,
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


def _seed_user(
    *,
    telegram_id: int = 1,
    enabled: bool = True,
    threshold: int = 5,
    last_sent: datetime | None = None,
    n_cards: int = 0,
) -> int:
    """Create a user with the given reminder settings + n due cards.
    Returns the user's row id."""
    with session_scope() as s:
        user = get_or_create_user(
            s, telegram_id=telegram_id, username="t", tz="UTC"
        )
        user.reminder_enabled = enabled
        user.reminder_threshold = threshold
        user.last_reminder_sent_at = last_sent
        for i in range(n_cards):
            add_card(s, user, front=f"f{i}", back=f"b{i}")
        return user.id


def _get_user(user_id: int) -> User:
    with session_scope() as s:
        u = s.get(User, user_id)
        # Detach a lightweight snapshot the caller can read after close.
        s.expunge(u)
        return u


# ---------------------------------------------------------------------------
# Pure decision function


def test_due_for_reminder_below_threshold_is_false() -> None:
    now = datetime.now(UTC)
    user = User(reminder_enabled=True, reminder_threshold=5, last_reminder_sent_at=None)
    assert _due_for_reminder(user, due=4, now=now) is False


def test_due_for_reminder_at_threshold_is_true() -> None:
    now = datetime.now(UTC)
    user = User(reminder_enabled=True, reminder_threshold=5, last_reminder_sent_at=None)
    assert _due_for_reminder(user, due=5, now=now) is True


def test_due_for_reminder_disabled_is_false() -> None:
    now = datetime.now(UTC)
    user = User(reminder_enabled=False, reminder_threshold=5, last_reminder_sent_at=None)
    assert _due_for_reminder(user, due=99, now=now) is False


def test_due_for_reminder_within_cooldown_is_false() -> None:
    now = datetime.now(UTC)
    recent = now - (REMINDER_COOLDOWN - timedelta(hours=1))
    user = User(
        reminder_enabled=True, reminder_threshold=5, last_reminder_sent_at=recent
    )
    assert _due_for_reminder(user, due=10, now=now) is False


def test_due_for_reminder_past_cooldown_is_true() -> None:
    now = datetime.now(UTC)
    old = now - (REMINDER_COOLDOWN + timedelta(hours=1))
    user = User(
        reminder_enabled=True, reminder_threshold=5, last_reminder_sent_at=old
    )
    assert _due_for_reminder(user, due=10, now=now) is True


# ---------------------------------------------------------------------------
# The hourly tick


def _run_tick() -> AsyncMock:
    """Run send_smart_reminders with a stub bot; return the stub."""
    import asyncio

    bot = AsyncMock()
    asyncio.run(send_smart_reminders(bot, _settings()))
    return bot


def test_tick_sends_and_stamps_when_over_threshold() -> None:
    user_id = _seed_user(enabled=True, threshold=5, n_cards=6)

    bot = _run_tick()

    bot.send_message.assert_awaited_once()
    args, _ = bot.send_message.call_args
    assert args[0] == 1  # telegram_id
    assert "6 cards are due" in args[1]

    # last_reminder_sent_at was stamped.
    assert _get_user(user_id).last_reminder_sent_at is not None


def test_tick_skips_below_threshold() -> None:
    user_id = _seed_user(enabled=True, threshold=5, n_cards=3)

    bot = _run_tick()

    bot.send_message.assert_not_called()
    assert _get_user(user_id).last_reminder_sent_at is None


def test_tick_skips_disabled_user() -> None:
    user_id = _seed_user(enabled=False, threshold=5, n_cards=20)

    bot = _run_tick()

    bot.send_message.assert_not_called()
    assert _get_user(user_id).last_reminder_sent_at is None


def test_tick_suppressed_within_cooldown() -> None:
    recent = datetime.now(UTC) - timedelta(hours=2)
    user_id = _seed_user(
        enabled=True, threshold=5, n_cards=10, last_sent=recent
    )

    bot = _run_tick()

    bot.send_message.assert_not_called()
    # The stamp is unchanged (still the original recent value, not "now").
    after = _get_user(user_id).last_reminder_sent_at
    assert after is not None
    assert abs((after.replace(tzinfo=UTC) - recent).total_seconds()) < 2


def test_tick_does_not_stamp_when_send_fails() -> None:
    """A transient Telegram failure must not burn the 24h cooldown —
    last_reminder_sent_at stays NULL so the next tick retries."""
    import asyncio

    user_id = _seed_user(enabled=True, threshold=5, n_cards=8)

    bot = AsyncMock()
    bot.send_message.side_effect = ConnectionError("telegram down")
    asyncio.run(send_smart_reminders(bot, _settings()))

    bot.send_message.assert_awaited_once()
    assert _get_user(user_id).last_reminder_sent_at is None


# ---------------------------------------------------------------------------
# /remind command


class _StubFromUser:
    id = 1
    username = "t"


class _StubMessage:
    def __init__(self) -> None:
        self.from_user = _StubFromUser()
        self.answer = AsyncMock()


class _StubCommand:
    def __init__(self, args: str | None) -> None:
        self.args = args


def _run_remind(args: str | None) -> _StubMessage:
    import asyncio

    from src.handlers.remind import cmd_remind

    msg = _StubMessage()
    asyncio.run(cmd_remind(msg, _StubCommand(args), _settings()))
    return msg


def test_remind_on_enables_and_off_disables() -> None:
    _seed_user(enabled=False)

    msg = _run_remind("on")
    assert "enabled" in msg.answer.call_args.args[0].lower()
    with session_scope() as s:
        assert s.scalar(select(User)).reminder_enabled is True

    msg = _run_remind("off")
    assert "disabled" in msg.answer.call_args.args[0].lower()
    with session_scope() as s:
        assert s.scalar(select(User)).reminder_enabled is False


def test_remind_threshold_sets_value_and_rejects_garbage() -> None:
    _seed_user(threshold=5)

    msg = _run_remind("threshold 12")
    assert "12" in msg.answer.call_args.args[0]
    with session_scope() as s:
        assert s.scalar(select(User)).reminder_threshold == 12

    # Non-numeric is rejected; value unchanged.
    msg = _run_remind("threshold abc")
    assert "whole number" in msg.answer.call_args.args[0].lower()
    with session_scope() as s:
        assert s.scalar(select(User)).reminder_threshold == 12

    # Below 1 is rejected.
    msg = _run_remind("threshold 0")
    assert "at least 1" in msg.answer.call_args.args[0].lower()
    with session_scope() as s:
        assert s.scalar(select(User)).reminder_threshold == 12


def test_remind_no_args_shows_status() -> None:
    _seed_user(enabled=True, threshold=7)
    msg = _run_remind(None)
    text = msg.answer.call_args.args[0]
    assert "on" in text.lower()
    assert "7" in text
