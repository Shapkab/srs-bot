"""Smoke tests for the one-shot migration scripts.

These migrations only run on existing pre-Phase-3 production DBs; fresh
installs get the columns directly via create_all. Still, the scripts are
production-critical (a misfire here loses user history), so they get
explicit coverage.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from scripts.migrate_001_card_json_before import migrate as migrate_001
from scripts.migrate_002_card_deleted_at import migrate as migrate_002


def _make_legacy_db(path: Path) -> None:
    """A minimal stand-in for a pre-Phase-3 DB: review_log without
    ``card_json_before``, card without ``deleted_at``."""
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(
            """
            CREATE TABLE card (
                id INTEGER PRIMARY KEY,
                owner_id INTEGER,
                front TEXT,
                back TEXT,
                tags TEXT,
                source TEXT,
                created_at DATETIME
            );
            CREATE TABLE review_log (
                id INTEGER PRIMARY KEY,
                card_id INTEGER,
                user_id INTEGER,
                rating INTEGER,
                reviewed_at DATETIME,
                elapsed_days REAL,
                scheduled_days REAL,
                state_before INTEGER
            );
            INSERT INTO card (id, owner_id, front, back, source) VALUES (1, 1, 'a', 'b', 'manual');
            INSERT INTO review_log (id, card_id, user_id, rating, state_before)
                VALUES (1, 1, 1, 3, 1);
            """
        )
        conn.commit()
    finally:
        conn.close()


def _columns(path: Path, table: str) -> list[str]:
    conn = sqlite3.connect(str(path))
    try:
        return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
    finally:
        conn.close()


def test_migrate_001_adds_card_json_before_to_review_log(tmp_path: Path) -> None:
    db = tmp_path / "legacy.db"
    _make_legacy_db(db)

    assert "card_json_before" not in _columns(db, "review_log")

    added = migrate_001(db)
    assert added is True

    cols = _columns(db, "review_log")
    assert "card_json_before" in cols

    # Existing row backfilled with empty string.
    conn = sqlite3.connect(str(db))
    try:
        (value,) = conn.execute("SELECT card_json_before FROM review_log WHERE id=1").fetchone()
    finally:
        conn.close()
    assert value == ""

    # Re-run is a no-op.
    assert migrate_001(db) is False


def test_migrate_002_adds_deleted_at_to_card(tmp_path: Path) -> None:
    db = tmp_path / "legacy.db"
    _make_legacy_db(db)

    assert "deleted_at" not in _columns(db, "card")

    added = migrate_002(db)
    assert added is True
    assert "deleted_at" in _columns(db, "card")

    # Existing row keeps NULL (live card).
    conn = sqlite3.connect(str(db))
    try:
        (value,) = conn.execute("SELECT deleted_at FROM card WHERE id=1").fetchone()
    finally:
        conn.close()
    assert value is None

    # Re-run is a no-op.
    assert migrate_002(db) is False
