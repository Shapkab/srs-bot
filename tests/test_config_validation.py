"""REMINDER_TIME must be validated at config load, not deep in scheduler setup."""

from __future__ import annotations

from datetime import time

import pytest

from src.config import load_settings


def _set_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "x")
    monkeypatch.setenv("OWNER_TELEGRAM_ID", "1")


@pytest.mark.parametrize(
    "value, expected",
    [
        ("09:00", time(9, 0)),
        ("23:59", time(23, 59)),
        ("00:00", time(0, 0)),
    ],
)
def test_load_settings_accepts_valid_reminder_time(
    monkeypatch: pytest.MonkeyPatch, value: str, expected: time
) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.setenv("REMINDER_TIME", value)
    settings = load_settings()
    assert settings.reminder_time == expected
    assert isinstance(settings.reminder_time, time)


@pytest.mark.parametrize("value", ["foo", "25:00", "9:00", "09:60", ""])
def test_load_settings_rejects_invalid_reminder_time(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.setenv("REMINDER_TIME", value)
    with pytest.raises(RuntimeError, match="REMINDER_TIME"):
        load_settings()
