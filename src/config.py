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

# Repo root containing alembic.ini / scripts/ / images/ — used to
# resolve relative IMAGE_DIR values (so the bot's notion of "./images"
# doesn't drift with cwd).
_REPO_ROOT = Path(__file__).resolve().parents[1]


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
    # Where card-attached image bytes live (one file per image, named
    # by sha256). Always stored absolute — see _resolved_image_dir.
    # Set to /data/images in the Fly deployment so it sits on the
    # persistent volume alongside the SQLite DB.
    image_dir: Path
    # Hard cap on per-image bytes. Configurable via MAX_IMAGE_BYTES env
    # var; defaulted to 5 MB which is plenty for vocabulary screenshots
    # and well below Telegram's 10 MB photo cap.
    max_image_bytes: int = 5_242_880


_REMINDER_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def _validated_reminder_time(raw: str) -> time:
    m = _REMINDER_TIME_RE.fullmatch(raw)
    if m is None:
        raise RuntimeError(
            f"REMINDER_TIME must be HH:MM in 24-hour form, got: {raw!r}"
        )
    return time(int(m.group(1)), int(m.group(2)))


def _resolved_image_dir(raw: str) -> Path:
    p = Path(raw)
    return p if p.is_absolute() else (_REPO_ROOT / p).resolve()


def _validated_max_image_bytes(raw: str | None) -> int:
    if raw is None:
        return 5_242_880
    try:
        value = int(raw)
    except ValueError as e:
        raise RuntimeError(
            f"MAX_IMAGE_BYTES must be a positive integer, got: {raw!r}"
        ) from e
    if value <= 0:
        raise RuntimeError(
            f"MAX_IMAGE_BYTES must be a positive integer, got: {value}"
        )
    return value


def load_settings() -> Settings:
    return Settings(
        bot_token=_required("BOT_TOKEN"),
        db_path=Path(os.getenv("DB_PATH", "srs.db")),
        owner_telegram_id=int(_required("OWNER_TELEGRAM_ID")),
        timezone=os.getenv("TZ", "UTC"),
        reminder_time=_validated_reminder_time(os.getenv("REMINDER_TIME", "09:00")),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        image_dir=_resolved_image_dir(os.getenv("IMAGE_DIR", "./images")),
        max_image_bytes=_validated_max_image_bytes(os.getenv("MAX_IMAGE_BYTES")),
    )
