"""/undo — roll back the most recent rating, within a 10-minute window.

Restores ``ReviewState.card_json`` (and its denormalized due / last_review
/ state projections) from the snapshot persisted in
``ReviewLog.card_json_before``, decrements ``reps`` (and ``lapses`` if the
undone rating was Again), then deletes the ReviewLog row itself.

Older reviews are considered committed — they cannot be undone. This
limit is intentional: the longer the window, the more it gets in the way
of fsrs-optimizer training accurately on actual user behavior.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import select

from src.config import Settings
from src.db.crud import get_or_create_user
from src.db.engine import session_scope
from src.db.models import Rating, ReviewLog, ReviewState
from src.srs.scheduler import CorruptCardJsonError, restore_from_json

router = Router(name="undo")

UNDO_WINDOW = timedelta(minutes=10)


def _ensure_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


@router.message(Command("undo"))
async def cmd_undo(message: Message, settings: Settings) -> None:
    with session_scope() as s:
        user = get_or_create_user(
            s,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            tz=settings.timezone,
        )

        latest = s.scalar(
            select(ReviewLog)
            .where(ReviewLog.user_id == user.id)
            .order_by(ReviewLog.reviewed_at.desc(), ReviewLog.id.desc())
            .limit(1)
        )
        if latest is None:
            await message.answer("Nothing to undo.")
            return

        if datetime.now(UTC) - _ensure_utc(latest.reviewed_at) > UNDO_WINDOW:
            await message.answer("That review is older than 10 minutes — too late to undo.")
            return

        if not latest.card_json_before:
            # Pre-3.4 ReviewLog row (no snapshot captured). Can't undo
            # without overwriting current state with empty FSRS data.
            await message.answer("This review has no undo snapshot (predates /undo).")
            return

        state = s.scalar(
            select(ReviewState).where(
                ReviewState.user_id == user.id,
                ReviewState.card_id == latest.card_id,
            )
        )
        if state is None:
            await message.answer("Cannot undo — review state no longer exists.")
            return

        try:
            restored = restore_from_json(latest.card_json_before)
        except CorruptCardJsonError:
            await message.answer("Cannot undo — the saved snapshot is corrupt.")
            return

        state.card_json = restored.card_json
        state.due = restored.due
        state.last_review = restored.last_review
        state.state = restored.state
        state.reps = max(0, state.reps - 1)
        if latest.rating == Rating.AGAIN:
            state.lapses = max(0, state.lapses - 1)

        s.delete(latest)

    await message.answer("Undone.")
