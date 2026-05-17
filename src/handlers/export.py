"""/export — emit every Card / ReviewState / ReviewLog row for the owner
as a single JSONL document.

JSONL schema (one object per line, UTF-8):

    {"type": "card",         "id": int, "owner_id": int, "front": str,
     "back": str, "tags": str|null, "source": str, "created_at": iso8601,
     "deleted_at": iso8601|null}

    {"type": "review_state", "id": int, "user_id": int, "card_id": int,
     "card_json": str, "due": iso8601, "last_review": iso8601|null,
     "state": int, "reps": int, "lapses": int}

    {"type": "review_log",   "id": int, "card_id": int, "user_id": int,
     "rating": int, "reviewed_at": iso8601, "elapsed_days": float,
     "scheduled_days": float, "state_before": int,
     "card_json_before": str}

All datetimes are ISO 8601 with UTC offset. ``card_json`` is passed through
verbatim — it is the FSRS library's own serialization and we don't touch it.

Row ordering in the file: every ``card`` row first (by Card.id ascending),
then every ``review_state`` row (by ReviewState.id ascending), then every
``review_log`` row (by ``reviewed_at`` ascending). This makes the file
diff-friendly across exports and trivial to re-import.

The Telegram-sending shell is intentionally thin — the JSONL builder is a
pure function that takes a Session and a user_id, so it is testable
without any aiogram involvement.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, Message
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.config import Settings
from src.db.crud import get_or_create_user
from src.db.engine import session_scope
from src.db.models import Card, ReviewLog, ReviewState
from src.utils.time import ensure_utc

router = Router(name="export")


def _iso(dt: datetime | None) -> str | None:
    return ensure_utc(dt).isoformat() if dt is not None else None


def build_export_jsonl(s: Session, user_id: int) -> bytes:
    """Serialize all Card / ReviewState / ReviewLog rows for ``user_id``
    as JSONL bytes. Pure function — easy to unit-test."""
    lines: list[str] = []

    cards = s.scalars(
        select(Card).where(Card.owner_id == user_id).order_by(Card.id.asc())
    ).all()
    for c in cards:
        lines.append(
            json.dumps(
                {
                    "type": "card",
                    "id": c.id,
                    "owner_id": c.owner_id,
                    "front": c.front,
                    "back": c.back,
                    "tags": c.tags,
                    "source": c.source,
                    "created_at": _iso(c.created_at),
                    "deleted_at": _iso(c.deleted_at),
                },
                ensure_ascii=False,
            )
        )

    states = s.scalars(
        select(ReviewState)
        .where(ReviewState.user_id == user_id)
        .order_by(ReviewState.id.asc())
    ).all()
    for st in states:
        lines.append(
            json.dumps(
                {
                    "type": "review_state",
                    "id": st.id,
                    "user_id": st.user_id,
                    "card_id": st.card_id,
                    "card_json": st.card_json,
                    "due": _iso(st.due),
                    "last_review": _iso(st.last_review),
                    "state": int(st.state),
                    "reps": st.reps,
                    "lapses": st.lapses,
                },
                ensure_ascii=False,
            )
        )

    logs = s.scalars(
        select(ReviewLog)
        .where(ReviewLog.user_id == user_id)
        .order_by(ReviewLog.reviewed_at.asc(), ReviewLog.id.asc())
    ).all()
    for lg in logs:
        lines.append(
            json.dumps(
                {
                    "type": "review_log",
                    "id": lg.id,
                    "card_id": lg.card_id,
                    "user_id": lg.user_id,
                    "rating": int(lg.rating),
                    "reviewed_at": _iso(lg.reviewed_at),
                    "elapsed_days": lg.elapsed_days,
                    "scheduled_days": lg.scheduled_days,
                    "state_before": lg.state_before,
                    "card_json_before": lg.card_json_before,
                },
                ensure_ascii=False,
            )
        )

    return ("\n".join(lines) + "\n").encode("utf-8") if lines else b""


@router.message(Command("export"))
async def cmd_export(message: Message, settings: Settings) -> None:
    with session_scope() as s:
        user = get_or_create_user(
            s,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            tz=settings.timezone,
        )
        payload = build_export_jsonl(s, user.id)

    if not payload:
        await message.answer("Nothing to export yet.")
        return

    filename = f"srs-export-{datetime.now(UTC):%Y%m%d}.jsonl"
    await message.answer_document(
        BufferedInputFile(payload, filename=filename),
        caption=f"{len(payload):,} bytes",
    )
