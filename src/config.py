"""Environment-driven settings.

Loaded once at startup. Fail fast if required vars are missing.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


@dataclass(frozen=True)
class Settings:
    bot_token: str
    db_path: Path
    owner_telegram_id: int
    timezone: str
    # Parsed once at config load; downstream code reads .hour / .minute
    # instead of re-splitting an HH:MM string.
    reminder_time: time
    log_level: str


_REMINDER_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def _validated_reminder_time(raw: str) -> time:
    m = _REMINDER_TIME_RE.fullmatch(raw)
    if m is None:
        raise RuntimeError(
            f"REMINDER_TIME must be HH:MM in 24-hour form, got: {raw!r}"
        )
    return time(int(m.group(1)), int(m.group(2)))


def load_settings() -> Settings:
    return Settings(
        bot_token=_required("BOT_TOKEN"),
        db_path=Path(os.getenv("DB_PATH", "srs.db")),
        owner_telegram_id=int(_required("OWNER_TELEGRAM_ID")),
        timezone=os.getenv("TZ", "UTC"),
        reminder_time=_validated_reminder_time(os.getenv("REMINDER_TIME", "09:00")),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
    )
