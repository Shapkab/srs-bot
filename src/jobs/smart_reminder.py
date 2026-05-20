"""Smart reminder: hourly backlog check with a 24h spam guard.

Coexists with the fixed-time daily reminder (src/jobs/daily_reminder.py).
Where the daily reminder fires at one configured time regardless of
load, this one runs every hour and pings a user only when their due
backlog has crossed ``User.reminder_threshold`` — and at most once per
``REMINDER_COOLDOWN`` so an hourly tick can't turn into hourly spam.

Off by default (``User.reminder_enabled`` defaults False); toggled via
the /remind command.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from src.config import Settings
from src.db.crud import due_count
from src.db.engine import session_scope
from src.db.models import User
from src.utils.time import ensure_utc

log = logging.getLogger(__name__)

# Minimum gap between two smart reminders for the same user. The job
# ticks hourly; this is what stops every tick from sending.
REMINDER_COOLDOWN = timedelta(hours=24)


def _due_for_reminder(user: User, due: int, now: datetime) -> bool:
    """Pure decision: should this user get a smart reminder right now?

    True iff reminders are on, the backlog is at/over their threshold,
    and the cooldown since the last send has elapsed.
    """
    if not user.reminder_enabled:
        return False
    if due < user.reminder_threshold:
        return False
    last = user.last_reminder_sent_at
    # Within the cooldown window → suppress; otherwise it's due.
    return last is None or now - ensure_utc(last) >= REMINDER_COOLDOWN


async def send_smart_reminders(bot: Bot, settings: Settings) -> None:
    """One hourly tick: find users owed a reminder, send, stamp.

    The stamp (``last_reminder_sent_at``) is written only after a
    successful send, in its own short transaction — so a transient
    Telegram failure simply retries on the next tick rather than
    silently burning the 24h cooldown.
    """
    now = datetime.now(UTC)

    # Collect candidates while the session is open; do not hold it
    # across the network sends.
    candidates: list[tuple[int, int, int]] = []  # (user_id, telegram_id, due)
    with session_scope() as s:
        users = s.scalars(
            select(User).where(User.reminder_enabled.is_(True))
        ).all()
        for user in users:
            due = due_count(s, user, now)
            if _due_for_reminder(user, due, now):
                candidates.append((user.id, user.telegram_id, due))

    for user_id, telegram_id, due in candidates:
        try:
            await bot.send_message(
                telegram_id,
                f"\U0001f4da {due} cards are due. /review",
            )
        except Exception:
            log.exception(
                "smart reminder send failed",
                extra={"event": "smart_reminder_send_failed", "user_id": user_id},
            )
            continue
        # Stamp only on success; a fresh transaction per user.
        with session_scope() as s:
            user = s.get(User, user_id)
            if user is not None:
                user.last_reminder_sent_at = now
        log.info(
            "smart reminder sent",
            extra={"event": "smart_reminder_sent", "user_id": user_id},
        )


def schedule_smart_reminder(
    scheduler: AsyncIOScheduler, bot: Bot, settings: Settings
) -> None:
    scheduler.add_job(
        send_smart_reminders,
        trigger=CronTrigger(minute=0, timezone=settings.timezone),
        kwargs={"bot": bot, "settings": settings},
        id="smart_reminder",
        replace_existing=True,
    )
