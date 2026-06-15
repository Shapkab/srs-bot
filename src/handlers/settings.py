"""/limit command to adjust daily new card limit."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.config import Settings
from src.db.crud import get_or_create_user
from src.db.engine import session_scope

router = Router(name="settings")


@router.message(Command("limit"))
async def cmd_limit(message: Message, settings: Settings) -> None:
    """Show or set the daily new card limit.

    Usage:
      /limit      — show current limit
      /limit 20   — set limit to 20 cards/day
      /limit 0    — disable limit (unlimited new cards)
    """
    if not message.from_user:
        return

    # Parse argument (if any)
    parts = (message.text or "").split(maxsplit=1)
    new_limit: int | None = None
    if len(parts) > 1:
        try:
            new_limit = int(parts[1])
            if new_limit < 0:
                await message.answer("Limit must be 0 or positive.")
                return
        except ValueError:
            await message.answer("Usage: /limit [number]\nExample: /limit 20")
            return

    with session_scope() as s:
        user = get_or_create_user(
            s,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            tz=settings.timezone,
        )

        if new_limit is not None:
            user.daily_new_limit = new_limit
            s.flush()
            if new_limit == 0:
                await message.answer("Daily new card limit <b>disabled</b> (unlimited).")
            else:
                await message.answer(
                    f"Daily new card limit set to <b>{new_limit}</b> cards/day."
                )
        else:
            limit = user.daily_new_limit
            if limit == 0:
                await message.answer("Your daily new card limit is <b>disabled</b> (unlimited).")
            else:
                await message.answer(
                    f"Your daily new card limit is <b>{limit}</b> cards/day.\n"
                    f"Use <code>/limit N</code> to change it."
                )
