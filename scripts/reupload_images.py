"""Re-issue Telegram ``file_id``s for every card-attached image.

When the bot's token rotates, every previously-issued ``file_id`` is
invalidated by Telegram. This script walks the DB, finds every image
identified by SHA-256, reads the bytes from ``IMAGE_DIR``, uploads each
back through the *current* bot, and writes the new ``file_id`` onto the
card row.

Usage:
    python -m scripts.reupload_images           # do it
    python -m scripts.reupload_images --dry-run # just list

Each image is reuploaded as a one-time photo to the owner's chat;
Telegram returns a fresh ``file_id`` in the response, which is what we
persist. The owner will see a flood of their own images in the chat
during the run — that's the visible side effect of the rotation; we
don't try to suppress it.

DB writes happen one card at a time, and the SELECT only pulls cards
whose ``*_image_sha256`` is set but matching ``*_image_file_id`` is
NULL — so a crash mid-run skips cards whose file_ids are already fresh
on re-run. To force a full re-upload (e.g., after a second rotation),
clear the file_ids manually first::

    UPDATE card
       SET front_image_file_id = NULL,
           back_image_file_id  = NULL
     WHERE front_image_sha256 IS NOT NULL
        OR back_image_sha256  IS NOT NULL;
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BufferedInputFile
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from src.config import Settings, load_settings
from src.db.engine import init_db, session_scope
from src.db.models import Card
from src.logging_setup import configure_logging

log = logging.getLogger(__name__)


def _targets(s: Session) -> list[tuple[int, str, str]]:
    """Return [(card_id, side, sha256), ...] for every image that still
    needs a fresh ``file_id``.

    Filter: a side is included only when the sha256 is set AND the
    matching file_id is NULL. After a partial-run crash this skips
    sides that were already refreshed, so a re-run only does the
    remaining work.
    """
    cards = s.scalars(
        select(Card)
        .where(
            or_(
                and_(
                    Card.front_image_sha256.is_not(None),
                    Card.front_image_file_id.is_(None),
                ),
                and_(
                    Card.back_image_sha256.is_not(None),
                    Card.back_image_file_id.is_(None),
                ),
            )
        )
        .order_by(Card.id)
    ).all()
    out: list[tuple[int, str, str]] = []
    for c in cards:
        if c.front_image_sha256 and c.front_image_file_id is None:
            out.append((c.id, "front", c.front_image_sha256))
        if c.back_image_sha256 and c.back_image_file_id is None:
            out.append((c.id, "back", c.back_image_sha256))
    return out


async def _reupload_one(
    bot: Bot,
    owner_telegram_id: int,
    image_dir: Path,
    sha256: str,
) -> str | None:
    """Send the on-disk bytes back through the bot. Returns the new
    ``file_id``, or None if the local file is missing.
    """
    path = image_dir / f"{sha256}.jpg"
    if not path.exists():
        log.warning("missing on-disk image sha=%s; skipping", sha256[:12])
        return None
    data = path.read_bytes()
    msg = await bot.send_photo(
        chat_id=owner_telegram_id,
        photo=BufferedInputFile(data, filename=f"{sha256}.jpg"),
        caption=f"reupload {sha256[:12]}…",
    )
    if not msg.photo:
        log.error("Telegram returned no photo sizes for sha=%s", sha256[:12])
        return None
    return msg.photo[-1].file_id


def _persist_new_file_id(card_id: int, side: str, new_file_id: str) -> None:
    """One transaction per (card, side): partial progress survives a crash."""
    with session_scope() as s:
        card = s.get(Card, card_id)
        if card is None:
            log.warning("card_id=%s vanished between scan and write", card_id)
            return
        if side == "front":
            card.front_image_file_id = new_file_id
        else:
            card.back_image_file_id = new_file_id


async def reupload_images(
    settings: Settings,
    bot: Bot,
    *,
    dry_run: bool = False,
) -> tuple[int, int]:
    """Pure orchestration — takes an already-constructed bot so tests
    can stub it. Returns (success_count, total_count).
    """
    with session_scope() as s:
        targets = _targets(s)
    log.info("Found %d image(s) to reupload across the DB.", len(targets))

    if dry_run:
        for card_id, side, sha in targets:
            log.info(
                "would reupload",
                extra={
                    "event": "reupload_dry_run",
                    "card_id": card_id,
                },
            )
            print(f"would reupload card={card_id} side={side} sha={sha[:12]}…")
        return (0, len(targets))

    success = 0
    for card_id, side, sha in targets:
        new_file_id = await _reupload_one(
            bot, settings.owner_telegram_id, settings.image_dir, sha
        )
        if new_file_id is None:
            continue
        _persist_new_file_id(card_id, side, new_file_id)
        success += 1
        log.info(
            "reuploaded",
            extra={
                "event": "reupload_success",
                "card_id": card_id,
            },
        )
    log.info("done: %d/%d image(s) reuploaded", success, len(targets))
    return (success, len(targets))


async def _amain(dry_run: bool) -> int:
    settings = load_settings()
    configure_logging(settings.log_level)
    init_db(settings.db_path)

    if dry_run:
        await reupload_images(settings, bot=None, dry_run=True)  # type: ignore[arg-type]
        return 0

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:
        success, total = await reupload_images(settings, bot, dry_run=False)
    finally:
        await bot.session.close()
    return 0 if success == total else 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="list what would be re-uploaded without calling Telegram",
    )
    args = parser.parse_args(argv[1:])
    return asyncio.run(_amain(args.dry_run))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
