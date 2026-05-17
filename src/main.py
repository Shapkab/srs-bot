"""Entry point.

Polling mode for local dev. Switch to webhooks when deploying — that's
a 20-line change in this file plus a public HTTPS endpoint.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.config import Settings, load_settings
from src.db.engine import init_db
from src.handlers import add_card, cards, export, repair, review, start, stats, undo
from src.handlers.middleware import OwnerOnlyMiddleware
from src.instance_lock import InstanceAlreadyRunning, instance_lock, lock_path_for
from src.jobs.backup import schedule_db_backup
from src.jobs.daily_reminder import run_catchup_if_needed, schedule_daily_reminder
from src.logging_setup import configure_logging


def _build_dispatcher(settings: Settings) -> Dispatcher:
    # MemoryStorage for /addm FSM; single-process single-user is fine.
    dp = Dispatcher(storage=MemoryStorage())

    # Owner-only gate (single-user v1).
    gate = OwnerOnlyMiddleware(owner_id=settings.owner_telegram_id)
    dp.message.middleware(gate)
    dp.callback_query.middleware(gate)

    # Inject settings into every handler via the workflow context.
    dp["settings"] = settings

    dp.include_router(start.router)
    dp.include_router(add_card.router)
    dp.include_router(review.router)
    dp.include_router(export.router)
    dp.include_router(cards.router)
    dp.include_router(undo.router)
    dp.include_router(stats.router)
    dp.include_router(repair.router)
    return dp


async def _run(settings: Settings, log: logging.Logger) -> None:
    init_db(settings.db_path)
    log.info("DB ready at %s", settings.db_path)

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = _build_dispatcher(settings)

    scheduler = AsyncIOScheduler(timezone=settings.timezone)
    schedule_daily_reminder(scheduler, bot, settings)
    schedule_db_backup(scheduler, settings)
    scheduler.start()
    log.info(
        "Scheduler started; daily reminder at %s %s",
        settings.reminder_time.strftime("%H:%M"),
        settings.timezone,
    )
    log.info("DB backup scheduled daily at 03:00 %s", settings.timezone)

    # If we started after today's REMINDER_TIME and never fired today, do it now.
    await run_catchup_if_needed(bot, settings)

    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


async def main() -> int:
    settings = load_settings()
    configure_logging(settings.log_level)
    log = logging.getLogger(__name__)

    # Refuse to start a second instance against the same DB — two pollers
    # would dupe messages and race on writes.
    try:
        with instance_lock(lock_path_for(settings.db_path)):
            await _run(settings, log)
    except InstanceAlreadyRunning as e:
        log.error("%s", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
