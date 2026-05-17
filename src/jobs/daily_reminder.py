"""Daily reminder: 'you have N cards due'.

APScheduler runs in-process with the bot in v1. When you scale out to
multiple users, move this to a separate worker process and use a
job store backed by the DB.

A KV row (``daily_reminder.last_fired``) records the local-date of the
most recent fire. On startup we use it to decide whether today's
reminder was missed and a catch-up fire is needed.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from src.config import Settings
from src.db.crud import due_count
from src.db.engine import session_scope
from src.db.models import KV, User

log = logging.getLogger(__name__)

LAST_FIRED_KEY = "daily_reminder.last_fired"


def _mark_fired(today_local: date) -> None:
    with session_scope() as s:
        kv = s.get(KV, LAST_FIRED_KEY)
        if kv is None:
            s.add(KV(key=LAST_FIRED_KEY, value=today_local.isoformat()))
        else:
            kv.value = today_local.isoformat()


def _last_fired() -> str | None:
    with session_scope() as s:
        kv = s.get(KV, LAST_FIRED_KEY)
        return kv.value if kv is not None else None


def should_run_catchup(
    now_local: datetime, reminder_time_hhmm: str, last_fired_iso: str | None
) -> bool:
    """Pure decision function: should we fire send_daily_reminder right
    now on startup?

    True iff:
      * today's reminder hasn't fired yet (last_fired < today's date), AND
      * the current local time is at or past today's REMINDER_TIME.
    """
    today_local = now_local.date()
    if last_fired_iso:
        try:
            last_fired_date = date.fromisoformat(last_fired_iso)
        except ValueError:
            last_fired_date = date.min
        if last_fired_date >= today_local:
            return False

    hh, mm = reminder_time_hhmm.split(":")
    reminder_today = now_local.replace(
        hour=int(hh), minute=int(mm), second=0, microsecond=0
    )
    return now_local >= reminder_today


async def send_daily_reminder(bot: Bot, settings: Settings) -> None:
    with session_scope() as s:
        user = s.scalar(select(User).where(User.telegram_id == settings.owner_telegram_id))
        if user is None:
            log.info("Owner user not yet in DB; skipping reminder.")
            return
        n = due_count(s, user)

    today_local = datetime.now(tz=ZoneInfo(settings.timezone)).date()
    # Mark "fired today" regardless of whether a message went out, so a
    # zero-due day doesn't trigger a catch-up fire on every restart.
    _mark_fired(today_local)

    if n == 0:
        return
    try:
        await bot.send_message(settings.owner_telegram_id, f"You have {n} cards due today. /review")
    except Exception:
        log.exception("Failed to send daily reminder")


async def run_catchup_if_needed(bot: Bot, settings: Settings) -> bool:
    """Called once at startup. Fires send_daily_reminder immediately if
    the scheduler missed today's slot. Returns True iff a fire happened.
    """
    now_local = datetime.now(tz=ZoneInfo(settings.timezone))
    if not should_run_catchup(now_local, settings.reminder_time, _last_fired()):
        return False
    log.info("daily reminder catch-up: firing now")
    await send_daily_reminder(bot, settings)
    return True


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
