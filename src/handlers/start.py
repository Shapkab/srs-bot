"""/start and /help handlers."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from src.config import Settings
from src.db.crud import due_count, get_or_create_user
from src.db.engine import session_scope

router = Router(name="start")


HELP_TEXT = (
    "<b>SRS vocabulary bot</b>\n\n"
    "/add front | back — add a card (separate with <code>|</code>)\n"
    "/addm — add a card step-by-step\n"
    "/review — start a review session\n"
    "/undo — undo the most recent rating (within 10 min)\n"
    "/due — how many cards are due now\n"
    "/cards [page] — list your cards (20 per page)\n"
    "/edit &lt;id&gt; front | back — edit a card (keeps FSRS state)\n"
    "/delete &lt;id&gt; — delete a card (history preserved)\n"
    "/export — download all your cards and review history as JSONL\n"
    "/help — this message"
)


@router.message(CommandStart())
async def cmd_start(message: Message, settings: Settings) -> None:
    with session_scope() as s:
        get_or_create_user(
            s,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            tz=settings.timezone,
        )
    await message.answer(HELP_TEXT)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT)


@router.message(Command("due"))
async def cmd_due(message: Message, settings: Settings) -> None:
    with session_scope() as s:
        user = get_or_create_user(
            s,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            tz=settings.timezone,
        )
        n = due_count(s, user)
    await message.answer(f"You have <b>{n}</b> cards due right now.")
