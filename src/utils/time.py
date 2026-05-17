"""Cross-module time helpers.

Single home for ``ensure_utc`` so the scheduler, /undo, and /export
don't each carry their own copy. Add new general-purpose time helpers
here rather than re-implementing them per module.
"""

from __future__ import annotations

from datetime import UTC, datetime


def ensure_utc(dt: datetime) -> datetime:
    """Return ``dt`` as a UTC-aware datetime.

    SQLite via SQLAlchemy may return naive datetimes depending on the
    driver path. py-fsrs requires UTC-aware datetimes everywhere. Pass
    every datetime that crosses module boundaries through this helper.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)
