"""/add handler.

Simple form for v1: <code>/add front | back</code>.
A multi-step FSM (separate prompts for front, back, tags) is a v1.1
improvement; not needed for the smallest working loop.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.config import Settings
from src.db.crud import add_card, get_or_create_user
from src.db.engine import session_scope

router = Router(name="add_card")


@router.message(Command("add"))
async def cmd_add(message: Message, settings: Settings) -> None:
    # message.text starts with "/add ". Strip the command, split on "|".
    text = (message.text or "").removeprefix("/add").strip()
    if "|" not in text:
        await message.answer(
            "Use the format: <code>/add front | back</code>\n"
            "Example: <code>/add reach out to | contact someone, usually for help</code>"
        )
        return

    front, back = (part.strip() for part in text.split("|", 1))
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
        card = add_card(s, user, front=front, back=back)
        card_id = card.id

    await message.answer(f"Added card #{card_id}. It will appear in your next /review.")
