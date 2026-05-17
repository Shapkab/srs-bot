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
    "/add front | back — add a card (separate front and back with <code>|</code>)\n"
    "/review — start a review session\n"
    "/due — how many cards are due now\n"
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
