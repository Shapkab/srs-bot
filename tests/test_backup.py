"""Online DB backup and retention pruning.

Two scenarios:
  1. run_backup creates a file in backups/, the backup is a valid SQLite
     DB, and the seeded card can be SELECTed from it.
  2. prune_backups keeps the 7 most recent srs-*.db files and removes
     the older ones (including any -wal/-shm siblings).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.db import engine as engine_mod
from src.db.crud import add_card, get_or_create_user
from src.db.engine import init_db, session_scope
from src.jobs.backup import RETENTION, prune_backups, run_backup


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    engine_mod._engine = None
    engine_mod._SessionLocal = None
    p = tmp_path / "live.db"
    init_db(p)
    return p


def test_run_backup_writes_a_valid_sqlite_snapshot(db_path: Path) -> None:
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


def _touch_backup_dated(db_path: Path, yyyymmdd: str) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    p = backup_dir / f"srs-{yyyymmdd}.db"
    p.write_bytes(b"fake")
    # Also drop sidecar files to verify they get cleaned up too.
    p.with_name(p.name + "-wal").write_bytes(b"")
    p.with_name(p.name + "-shm").write_bytes(b"")
    return p


def test_prune_backups_keeps_last_seven(db_path: Path) -> None:
    # Create 10 dated backups, ascending.
    dates = [f"2026010{i}" if i < 10 else f"202601{i}" for i in range(1, 11)]
    for d in dates:
        _touch_backup_dated(db_path, d)

    backup_dir = db_path.parent / "backups"
    assert len(list(backup_dir.glob("srs-*.db"))) == 10

    deleted = prune_backups(db_path)

    remaining = sorted(p.name for p in backup_dir.glob("srs-*.db"))
    assert len(remaining) == RETENTION  # 7
    assert len(deleted) == 3
    # The three oldest should be gone.
    for d in dates[:3]:
        assert not (backup_dir / f"srs-{d}.db").exists()
        assert not (backup_dir / f"srs-{d}.db-wal").exists()
        assert not (backup_dir / f"srs-{d}.db-shm").exists()
    # The seven newest survive.
    for d in dates[3:]:
        assert (backup_dir / f"srs-{d}.db").exists()


def test_prune_backups_noop_when_under_retention(db_path: Path) -> None:
    for d in ("20260101", "20260102", "20260103"):
        _touch_backup_dated(db_path, d)
    deleted = prune_backups(db_path)
    assert deleted == []
    backup_dir = db_path.parent / "backups"
    assert len(list(backup_dir.glob("srs-*.db"))) == 3
