"""/remind — per-user smart-reminder settings.

  /remind                 show current settings
  /remind on              enable smart reminders
  /remind off             disable smart reminders
  /remind threshold <N>   set the due-count that triggers a reminder

The hourly job in src/jobs/smart_reminder.py reads these fields off the
User row. Reminders are off until /remind on is run.
"""

from __future__ import annotations

import html

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from src.config import Settings
from src.db.crud import get_or_create_user
from src.db.engine import session_scope

router = Router(name="remind")

_HELP = (
    "Usage:\n"
    "<code>/remind</code> — show settings\n"
    "<code>/remind on</code> — enable smart reminders\n"
    "<code>/remind off</code> — disable\n"
    "<code>/remind threshold N</code> — nudge when N+ cards are due"
)


def _status_line(enabled: bool, threshold: int) -> str:
    state = "on" if enabled else "off"
    return (
        f"Smart reminders: <b>{state}</b>\n"
        f"Threshold: <b>{threshold}</b> due cards\n"
        f"(hourly check, at most one reminder per 24h)"
    )


@router.message(Command("remind"))
async def cmd_remind(message: Message, command: CommandObject, settings: Settings) -> None:
    args = (command.args or "").strip()

    with session_scope() as s:
        user = get_or_create_user(
            s,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            tz=settings.timezone,
        )

        if not args:
            await message.answer(_status_line(user.reminder_enabled, user.reminder_threshold))
            return

        parts = args.split()
        verb = parts[0].lower()

        if verb == "on":
            user.reminder_enabled = True
            await message.answer(
                "Smart reminders enabled.\n"
                + _status_line(True, user.reminder_threshold)
            )
            return

        if verb == "off":
            user.reminder_enabled = False
            await message.answer(
                "Smart reminders disabled.\n"
                + _status_line(False, user.reminder_threshold)
            )
            return

        if verb == "threshold":
            if len(parts) != 2:
                await message.answer(
                    "Use: <code>/remind threshold N</code> (a positive number)."
                )
                return
            try:
                n = int(parts[1])
            except ValueError:
                await message.answer("Threshold must be a whole number.")
                return
            if n < 1:
                await message.answer("Threshold must be at least 1.")
                return
            user.reminder_threshold = n
            await message.answer(
                f"Threshold set to {n}.\n"
                + _status_line(user.reminder_enabled, n)
            )
            return

        safe_verb = html.escape(verb, quote=False)
        await message.answer(f"Unknown option: <code>{safe_verb}</code>\n\n{_HELP}")
