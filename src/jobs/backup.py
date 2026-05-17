"""Daily on-line DB backup.

03:00 in the configured timezone, every day, we snapshot the SQLite DB
via the online backup API (``sqlite3.Connection.backup``). That API is
the documented way to get a consistent copy without stopping the bot —
WAL writers in progress are safely captured. As a belt-and-suspenders
measure we also copy any ``-wal`` / ``-shm`` siblings of the source file
if they happen to exist at backup time.

Backups land in ``<db_path>.parent / "backups" / "srs-YYYYMMDD.db"``.

Retention: keep the 7 most recent ``srs-*.db`` files; older ones get
removed. Their ``-wal`` / ``-shm`` siblings are removed alongside.

Backup time (03:00) is intentionally not configurable in v1 — flag it if
that turns out to be wrong.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from src.config import Settings

log = logging.getLogger(__name__)

BACKUP_DIR_NAME = "backups"
BACKUP_FILENAME_FMT = "srs-{date:%Y%m%d}.db"
RETENTION = 7


def _backup_dir(db_path: Path) -> Path:
    return db_path.parent / BACKUP_DIR_NAME


def run_backup(db_path: Path, now: datetime | None = None) -> Path:
    """Perform one backup. Returns the path of the created backup file.

    Exposed at module level so tests can call it directly without going
    through APScheduler.
    """
    now = now or datetime.now()
    backup_dir = _backup_dir(db_path)
    backup_dir.mkdir(parents=True, exist_ok=True)

    target = backup_dir / BACKUP_FILENAME_FMT.format(date=now)

    # Use SQLite's online backup API: consistent snapshot, no need to stop
    # writers.
    src = sqlite3.connect(str(db_path))
    try:
        dst = sqlite3.connect(str(target))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()

    # Per the brief, copy any -wal / -shm siblings if present. They are
    # not strictly needed because ``Connection.backup`` produces a
    # consolidated, consistent .db; this is purely defensive.
    for suffix in ("-wal", "-shm"):
        sibling = db_path.with_name(db_path.name + suffix)
        if sibling.exists():
            shutil.copy2(sibling, target.with_name(target.name + suffix))

    log.info("backup written: %s", target)
    prune_backups(db_path, retention=RETENTION)
    return target


def prune_backups(db_path: Path, retention: int = RETENTION) -> list[Path]:
    """Delete all but the ``retention`` most-recent ``srs-*.db`` backups.

    Ordering is by ``Path.stat().st_mtime`` — the actual creation time —
    rather than the YYYYMMDD chunk in the filename. That matters when
    two backups share the same date filename (e.g. a re-run on the same
    UTC day, or a manual backup), since the newer one's mtime correctly
    wins regardless of filename.

    Returns the list of deleted ``.db`` paths. Sibling ``-wal`` /
    ``-shm`` files are removed alongside.
    """
    backup_dir = _backup_dir(db_path)
    if not backup_dir.exists():
        return []

    backups = list(backup_dir.glob("srs-*.db"))
    if len(backups) <= retention:
        return []

    # Newest first by mtime; ties broken by name for determinism.
    backups.sort(key=lambda p: (p.stat().st_mtime, p.name), reverse=True)
    to_delete = backups[retention:]

    deleted: list[Path] = []
    for old in to_delete:
        for suffix in ("", "-wal", "-shm"):
            sibling = old.with_name(old.name + suffix) if suffix else old
            if sibling.exists():
                sibling.unlink()
        deleted.append(old)
        log.info("pruned old backup: %s", old)
    return deleted


def schedule_db_backup(scheduler: AsyncIOScheduler, settings: Settings) -> None:
    scheduler.add_job(
        run_backup,
        trigger=CronTrigger(hour=3, minute=0, timezone=settings.timezone),
        kwargs={"db_path": settings.db_path},
        id="db_backup",
        replace_existing=True,
    )
