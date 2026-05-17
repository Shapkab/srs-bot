"""Environment-driven settings.

Loaded once at startup. Fail fast if required vars are missing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
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
    reminder_time: str  # HH:MM
    log_level: str


def load_settings() -> Settings:
    return Settings(
        bot_token=_required("BOT_TOKEN"),
        db_path=Path(os.getenv("DB_PATH", "srs.db")),
        owner_telegram_id=int(_required("OWNER_TELEGRAM_ID")),
        timezone=os.getenv("TZ", "UTC"),
        reminder_time=os.getenv("REMINDER_TIME", "09:00"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
    )
