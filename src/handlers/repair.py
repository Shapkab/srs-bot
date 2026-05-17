"""/repair — soft-delete cards whose FSRS card_json no longer parses.

Triggered manually by the owner after seeing the "This card's state is
corrupt; please run /repair" message from the review flow. Walks every
live (non-deleted) ReviewState for the user, attempts
``restore_from_json``, and soft-deletes the underlying Card on failure
so it stops blocking ``/review``.

Soft-delete rather than hard-delete: the ReviewLog rows for the card
are still useful to fsrs-optimizer, and the user may want to manually
recreate the card from the front/back content.
"""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import select

from src.config import Settings
from src.db.crud import get_or_create_user
from src.db.engine import session_scope
from src.db.models import Card, ReviewState, utcnow
from src.srs.scheduler import CorruptCardJsonError, restore_from_json

log = logging.getLogger(__name__)

router = Router(name="repair")


@router.message(Command("repair"))
async def cmd_repair(message: Message, settings: Settings) -> None:
    repaired: list[int] = []
    healthy = 0

    with session_scope() as s:
        user = get_or_create_user(
            s,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            tz=settings.timezone,
        )
        states = s.scalars(
            select(ReviewState)
            .join(Card, ReviewState.card_id == Card.id)
            .where(
                ReviewState.user_id == user.id,
                Card.deleted_at.is_(None),
            )
        ).all()

        now = utcnow()
        for state in states:
            try:
                restore_from_json(state.card_json)
                healthy += 1
            except CorruptCardJsonError:
                log.warning(
                    "/repair: soft-deleting card",
                    extra={
                        "event": "repair_soft_delete",
                        "user_id": user.id,
                        "card_id": state.card_id,
                    },
                )
                # Mark the underlying card deleted; the ReviewState row
                # is kept so the ReviewLog history can still be exported.
                state.card.deleted_at = now
                repaired.append(state.card_id)

    if repaired:
        ids = ", ".join(f"#{i}" for i in repaired)
        await message.answer(
            f"Soft-deleted {len(repaired)} corrupt card(s): {ids}.\n"
            f"{healthy} card(s) checked clean."
        )
    else:
        await message.answer(
            f"Nothing to repair — {healthy} card(s) checked clean."
        )
