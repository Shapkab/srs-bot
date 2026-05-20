"""Card management commands.

  /cards [page]            list up to 20 most recently created cards
  /edit <id> front | back  edit a card; FSRS state untouched
  /delete <id>             soft-delete a card (tombstone, history preserved)

Soft-delete is used so ReviewLog rows (which fsrs-optimizer will later
want) survive a card removal. Filtering of deleted cards happens in
``next_due_card``/``due_count`` and in this module's ``/cards`` query.
"""

from __future__ import annotations

import html
import logging
from datetime import UTC, datetime

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from sqlalchemy import select

from src.config import Settings
from src.db.crud import get_or_create_user
from src.db.engine import session_scope
from src.db.models import Card
from src.utils.pronunciation import generate_pronunciation

log = logging.getLogger(__name__)

router = Router(name="cards")

PAGE_SIZE = 20
FRONT_PREVIEW_LEN = 60


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"  # ellipsis


def _parse_page(arg: str | None) -> int:
    if arg is None:
        return 1
    arg = arg.strip()
    if not arg:
        return 1
    try:
        page = int(arg)
    except ValueError:
        return 1
    return page if page >= 1 else 1


@router.message(Command("cards"))
async def cmd_cards(message: Message, command: CommandObject, settings: Settings) -> None:
    page = _parse_page(command.args)
    offset = (page - 1) * PAGE_SIZE

    with session_scope() as s:
        user = get_or_create_user(
            s,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            tz=settings.timezone,
        )
        rows = s.scalars(
            select(Card)
            .where(Card.owner_id == user.id, Card.deleted_at.is_(None))
            .order_by(Card.created_at.desc(), Card.id.desc())
            .offset(offset)
            .limit(PAGE_SIZE)
        ).all()

    if not rows:
        if page == 1:
            await message.answer("You have no cards yet. /add to create one.")
        else:
            await message.answer(f"No cards on page {page}.")
        return

    lines = [
        f"#{c.id} {html.escape(_truncate(c.front, FRONT_PREVIEW_LEN), quote=False)}"
        for c in rows
    ]
    header = f"<b>Cards — page {page}</b>"
    await message.answer(header + "\n" + "\n".join(lines))


@router.message(Command("edit"))
async def cmd_edit(message: Message, command: CommandObject, settings: Settings) -> None:
    args = (command.args or "").strip()
    if not args:
        await message.answer(
            "Use the format: <code>/edit &lt;id&gt; front | back</code>"
        )
        return

    head, sep, rest = args.partition(" ")
    if not sep or "|" not in rest:
        await message.answer(
            "Use the format: <code>/edit &lt;id&gt; front | back</code>"
        )
        return
    try:
        card_id = int(head)
    except ValueError:
        await message.answer("Card id must be an integer.")
        return

    front, back = (part.strip() for part in rest.split("|", 1))
    if not front or not back:
        await message.answer("Both front and back are required.")
        return

    with session_scope() as s:
        user = get_or_create_user(
            s,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            tz=settings.timezone,
        )
        card = s.scalar(
            select(Card).where(
                Card.id == card_id,
                Card.owner_id == user.id,
                Card.deleted_at.is_(None),
            )
        )
        if card is None:
            await message.answer(f"Card #{card_id} not found.")
            return

        front_changed = card.front != front
        card.front = front
        card.back = back

        # Regenerate IPA only when the front text actually changed. A
        # failure here does NOT block the edit — front/back are already
        # updated; we just warn that the pronunciation is now stale.
        pronunciation_failed = False
        if front_changed:
            try:
                card.front_pronunciation = generate_pronunciation(
                    front, settings.openai_api_key
                )
            except Exception:
                log.warning("pronunciation regeneration failed for /edit", exc_info=True)
                pronunciation_failed = True

    if pronunciation_failed:
        await message.answer(
            f"Card #{card_id} updated, but pronunciation regeneration failed."
        )
    else:
        await message.answer(f"Card #{card_id} updated.")


@router.message(Command("delete"))
async def cmd_delete(message: Message, command: CommandObject, settings: Settings) -> None:
    args = (command.args or "").strip()
    if not args:
        await message.answer("Use the format: <code>/delete &lt;id&gt;</code>")
        return
    try:
        card_id = int(args)
    except ValueError:
        await message.answer("Card id must be an integer.")
        return

    with session_scope() as s:
        user = get_or_create_user(
            s,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            tz=settings.timezone,
        )
        card = s.scalar(
            select(Card).where(
                Card.id == card_id,
                Card.owner_id == user.id,
                Card.deleted_at.is_(None),
            )
        )
        if card is None:
            await message.answer(f"Card #{card_id} not found.")
            return

        card.deleted_at = datetime.now(UTC)

    await message.answer(f"Card #{card_id} deleted. (History preserved.)")
