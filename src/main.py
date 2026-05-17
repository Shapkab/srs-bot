"""Entry point.

Polling mode for local dev. Switch to webhooks when deploying — that's
a 20-line change in this file plus a public HTTPS endpoint.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.config import Settings, load_settings
from src.db.engine import init_db
from src.handlers import add_card, review, start
from src.handlers.middleware import OwnerOnlyMiddleware
from src.jobs.daily_reminder import schedule_daily_reminder


def _build_dispatcher(settings: Settings) -> Dispatcher:
    dp = Dispatcher()

    # Owner-only gate (single-user v1).
    gate = OwnerOnlyMiddleware(owner_id=settings.owner_telegram_id)
    dp.message.middleware(gate)
    dp.callback_query.middleware(gate)

    # Inject settings into every handler via the workflow context.
    dp["settings"] = settings

    dp.include_router(start.router)
    dp.include_router(add_card.router)
    dp.include_router(review.router)
    return dp


async def main() -> None:
    settings = load_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    log = logging.getLogger(__name__)

    init_db(settings.db_path)
    log.info("DB ready at %s", settings.db_path)

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = _build_dispatcher(settings)

    scheduler = AsyncIOScheduler(timezone=settings.timezone)
    schedule_daily_reminder(scheduler, bot, settings)
    scheduler.start()
    log.info("Scheduler started; daily reminder at %s %s", settings.reminder_time, settings.timezone)

    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
