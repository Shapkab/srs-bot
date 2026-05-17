"""Daily reminder: 'you have N cards due'.

APScheduler runs in-process with the bot in v1. When you scale out to
multiple users, move this to a separate worker process and use a
job store backed by the DB.
"""

from __future__ import annotations

import logging

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from src.config import Settings
from src.db.crud import due_count
from src.db.engine import session_scope
from src.db.models import User

log = logging.getLogger(__name__)


async def send_daily_reminder(bot: Bot, settings: Settings) -> None:
    with session_scope() as s:
        user = s.scalar(select(User).where(User.telegram_id == settings.owner_telegram_id))
        if user is None:
            log.info("Owner user not yet in DB; skipping reminder.")
            return
        n = due_count(s, user)

    if n == 0:
        return
    try:
        await bot.send_message(settings.owner_telegram_id, f"You have {n} cards due today. /review")
    except Exception:
        log.exception("Failed to send daily reminder")


def schedule_daily_reminder(scheduler: AsyncIOScheduler, bot: Bot, settings: Settings) -> None:
    hour_str, minute_str = settings.reminder_time.split(":")
    scheduler.add_job(
        send_daily_reminder,
        trigger=CronTrigger(
            hour=int(hour_str), minute=int(minute_str), timezone=settings.timezone
        ),
        kwargs={"bot": bot, "settings": settings},
        id="daily_reminder",
        replace_existing=True,
    )
