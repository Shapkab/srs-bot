"""Online DB backup and retention pruning.

Scenarios:
  1. run_backup creates a file in backups/, the backup is a valid SQLite
     DB, and the seeded card can be SELECTed from it.
  2. prune_backups keeps the ``RETENTION`` most-recent srs-*.db files
     (by mtime) and removes the older ones, including any -wal/-shm
     siblings.
  3. When two backups share the same YYYYMMDD filename root but
     different mtimes, the newer-mtime one survives — sorting by
     filename alone would tie-break by lexicographic order, which is
     not what we want.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from src.db.crud import add_card, get_or_create_user
from src.db.engine import session_scope
from src.jobs.backup import RETENTION, prune_backups, run_backup


def test_run_backup_writes_a_valid_sqlite_snapshot(fresh_db: Path) -> None:
    db_path = fresh_db
    with session_scope() as s:
        user = get_or_create_user(s, telegram_id=1, username="t", tz="UTC")
        add_card(s, user, front="reach out to", back="contact someone")

    backup_path = run_backup(db_path, now=datetime(2026, 5, 17, 3, 0, tzinfo=UTC))

    assert backup_path.exists()
    assert backup_path.name == "srs-20260517.db"

    # The backup must be a fully consistent SQLite DB readable on its own.
    conn = sqlite3.connect(str(backup_path))
    try:
        cur = conn.execute("SELECT front, back FROM card")
        rows = cur.fetchall()
    finally:
        conn.close()
    assert rows == [("reach out to", "contact someone")]


def _touch_backup_dated(db_path: Path, yyyymmdd: str, mtime: float | None = None) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    p = backup_dir / f"srs-{yyyymmdd}.db"
    p.write_bytes(b"fake")
    # Also drop sidecar files to verify they get cleaned up too.
    p.with_name(p.name + "-wal").write_bytes(b"")
    p.with_name(p.name + "-shm").write_bytes(b"")
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


def test_prune_backups_keeps_last_seven_by_mtime(fresh_db: Path) -> None:
    db_path = fresh_db
    # Create 10 dated backups with strictly-increasing explicit mtimes
    # so the test is deterministic regardless of filesystem timer
    # resolution.
    dates = [f"2026010{i}" if i < 10 else f"202601{i}" for i in range(1, 11)]
    for idx, d in enumerate(dates):
        _touch_backup_dated(db_path, d, mtime=1_700_000_000 + idx)

    backup_dir = db_path.parent / "backups"
    assert len(list(backup_dir.glob("srs-*.db"))) == 10

    deleted = prune_backups(db_path)

    remaining = sorted(p.name for p in backup_dir.glob("srs-*.db"))
    assert len(remaining) == RETENTION  # 7
    assert len(deleted) == 3
    # The three with the OLDEST mtime (which are also the three oldest dates
    # in this fixture) should be gone.
    for d in dates[:3]:
        assert not (backup_dir / f"srs-{d}.db").exists()
        assert not (backup_dir / f"srs-{d}.db-wal").exists()
        assert not (backup_dir / f"srs-{d}.db-shm").exists()
    # The seven newest survive.
    for d in dates[3:]:
        assert (backup_dir / f"srs-{d}.db").exists()


def test_prune_backups_uses_mtime_not_filename(fresh_db: Path) -> None:
    """If two backups have the same YYYYMMDD root but different mtimes,
    the newer-mtime one must survive even when its filename sorts
    lexicographically earlier — i.e., sorting by name is wrong."""
    db_path = fresh_db

    # Eight backups: seven named with old dates but assigned new mtimes,
    # plus one named with a new date but assigned an old mtime. Only the
    # one with the oldest mtime should get pruned.
    old_named_new_mtime = []
    for idx in range(7):
        p = _touch_backup_dated(
            db_path,
            yyyymmdd=f"2020010{idx + 1}",  # old name
            mtime=1_700_000_000 + idx,     # newer mtime
        )
        old_named_new_mtime.append(p)
    odd_one_out = _touch_backup_dated(
        db_path,
        yyyymmdd="20300101",       # newest-looking name
        mtime=1_600_000_000,       # but the oldest mtime
    )

    deleted = prune_backups(db_path)

    # The newest-looking-NAME backup is the one that gets pruned because
    # its mtime is the oldest.
    assert deleted == [odd_one_out]
    assert not odd_one_out.exists()
    for p in old_named_new_mtime:
        assert p.exists()


def test_prune_backups_noop_when_under_retention(fresh_db: Path) -> None:
    db_path = fresh_db
    for d in ("20260101", "20260102", "20260103"):
        _touch_backup_dated(db_path, d)
    deleted = prune_backups(db_path)
    assert deleted == []
    backup_dir = db_path.parent / "backups"
    assert len(list(backup_dir.glob("srs-*.db"))) == 3
