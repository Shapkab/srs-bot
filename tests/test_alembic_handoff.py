"""Alembic handoff for pre-Phase-7 DBs.

The retired ``scripts/migrate_001-003`` one-shots wrote columns directly
via ALTER TABLE; the convention used to track that pre-Phase-7 state is
``PRAGMA user_version = 1``. Our ``migrations/env.py`` looks at that
pragma at the start of each online run and stamps the DB at revision
0001 when the legacy schema is already in place, so the baseline
revision's ``CREATE TABLE`` block does not fire against existing tables.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic.command import upgrade as alembic_upgrade
from alembic.config import Config

REPO_ROOT = Path(__file__).resolve().parent.parent


def _alembic_config(db_path: Path) -> Config:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def _apply_baseline_schema(db_path: Path) -> None:
    """Mimic the schema produced by 0001_baseline.upgrade(), using only
    raw sqlite3 so we do NOT touch alembic_version. This is the shape a
    DB ends up in after a real run of the retired migrate_001-003
    scripts."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            """
            CREATE TABLE "user" (
                id INTEGER PRIMARY KEY,
                telegram_id BIGINT NOT NULL UNIQUE,
                username VARCHAR(64),
                timezone VARCHAR(64) NOT NULL DEFAULT 'UTC',
                daily_new_limit INTEGER NOT NULL DEFAULT 10,
                daily_review_limit INTEGER NOT NULL DEFAULT 200,
                created_at DATETIME NOT NULL
            );
            CREATE INDEX ix_user_telegram_id ON "user" (telegram_id);
            CREATE TABLE card (
                id INTEGER PRIMARY KEY,
                owner_id INTEGER NOT NULL REFERENCES "user"(id),
                front TEXT NOT NULL,
                back TEXT NOT NULL,
                tags VARCHAR(255),
                source VARCHAR(64) NOT NULL DEFAULT 'manual',
                created_at DATETIME NOT NULL
            );
            CREATE INDEX ix_card_owner_id ON card (owner_id);
            CREATE TABLE review_state (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES "user"(id),
                card_id INTEGER NOT NULL REFERENCES card(id),
                card_json TEXT NOT NULL,
                due DATETIME NOT NULL,
                last_review DATETIME,
                state VARCHAR(10) NOT NULL,
                reps INTEGER NOT NULL DEFAULT 0,
                lapses INTEGER NOT NULL DEFAULT 0,
                UNIQUE (user_id, card_id)
            );
            CREATE INDEX ix_review_state_user_id ON review_state (user_id);
            CREATE INDEX ix_review_state_card_id ON review_state (card_id);
            CREATE INDEX ix_review_state_due ON review_state (due);
            CREATE TABLE review_log (
                id INTEGER PRIMARY KEY,
                card_id INTEGER NOT NULL REFERENCES card(id),
                user_id INTEGER NOT NULL REFERENCES "user"(id),
                rating VARCHAR(5) NOT NULL,
                reviewed_at DATETIME NOT NULL,
                elapsed_days FLOAT NOT NULL DEFAULT 0,
                scheduled_days FLOAT NOT NULL DEFAULT 0,
                state_before INTEGER NOT NULL
            );
            CREATE INDEX ix_review_log_card_id ON review_log (card_id);
            CREATE INDEX ix_review_log_user_id ON review_log (user_id);
            CREATE INDEX ix_review_log_reviewed_at ON review_log (reviewed_at);
            PRAGMA user_version = 1;
            """
        )
        conn.commit()
    finally:
        conn.close()


def _table_columns(db_path: Path, table: str) -> list[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    finally:
        conn.close()


def _alembic_version(db_path: Path) -> str | None:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def test_handoff_from_legacy_pragma_user_version_one(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    _apply_baseline_schema(db_path)
    # Sanity: legacy schema is in place but alembic_version is NOT yet.
    conn = sqlite3.connect(str(db_path))
    try:
        assert (
            conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='alembic_version'"
            ).fetchone()
            is None
        )
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    finally:
        conn.close()

    # alembic upgrade head must NOT explode on CREATE TABLE user.
    alembic_upgrade(_alembic_config(db_path), "head")

    # All four revisions applied; alembic_version at head.
    assert _alembic_version(db_path) == "0007"

    # The columns added by 0002 / 0003 / 0004 are now present.
    assert "card_json_before" in _table_columns(db_path, "review_log")
    assert "deleted_at" in _table_columns(db_path, "card")
    assert "suspended_at" in _table_columns(db_path, "review_state")
    # And the new kv table from 0004 exists.
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='kv'"
        ).fetchall()
        assert rows, "kv table not created by revision 0004"
    finally:
        conn.close()


def test_fresh_db_user_version_zero_runs_full_chain(tmp_path: Path) -> None:
    db_path = tmp_path / "fresh.db"
    # Don't create anything; alembic upgrade head must build from scratch.
    alembic_upgrade(_alembic_config(db_path), "head")

    assert _alembic_version(db_path) == "0007"
    assert "card_json_before" in _table_columns(db_path, "review_log")
    assert "deleted_at" in _table_columns(db_path, "card")
    assert "suspended_at" in _table_columns(db_path, "review_state")
    assert _table_columns(db_path, "kv") == ["key", "value"]


def test_handoff_is_idempotent(tmp_path: Path) -> None:
    """Running ``alembic upgrade head`` twice in a row on a legacy DB
    must not error or wedge state."""
    db_path = tmp_path / "legacy.db"
    _apply_baseline_schema(db_path)

    alembic_upgrade(_alembic_config(db_path), "head")
    alembic_upgrade(_alembic_config(db_path), "head")

    assert _alembic_version(db_path) == "0007"


def test_init_db_runs_alembic_to_head(tmp_path: Path) -> None:
    """Phase 7.2: src/db/engine.py:init_db() must run Alembic migrations
    instead of Base.metadata.create_all, so startup is self-healing on
    legacy and fresh DBs alike.
    """
    from src.db.engine import init_db

    db_path = tmp_path / "startup.db"
    init_db(db_path)

    # After init_db, the DB must be at head and have the full schema.
    assert _alembic_version(db_path) == "0007"
    assert "card_json_before" in _table_columns(db_path, "review_log")
    assert "deleted_at" in _table_columns(db_path, "card")
    assert "suspended_at" in _table_columns(db_path, "review_state")


def test_init_db_handles_legacy_user_version_one(tmp_path: Path) -> None:
    """Phase 7.2 + 7.1: init_db() on a legacy DB stamps then upgrades."""
    from src.db.engine import init_db

    db_path = tmp_path / "legacy_startup.db"
    _apply_baseline_schema(db_path)

    init_db(db_path)

    assert _alembic_version(db_path) == "0007"
    assert "card_json_before" in _table_columns(db_path, "review_log")


def test_migration_0005_adds_image_columns(tmp_path: Path) -> None:
    """Phase 9.5: revision 0005 must add the four image columns to the
    card table. Verified via PRAGMA table_info, no engine state reuse.
    """
    db_path = tmp_path / "image_schema.db"
    alembic_upgrade(_alembic_config(db_path), "head")

    cols = _table_columns(db_path, "card")
    assert {
        "front_image_file_id",
        "front_image_sha256",
        "back_image_file_id",
        "back_image_sha256",
    }.issubset(cols)


def test_migration_0006_adds_reminder_columns(tmp_path: Path) -> None:
    """Revision 0006 must add the three smart-reminder columns to the
    user table. Verified via PRAGMA table_info, no engine state reuse.
    """
    db_path = tmp_path / "reminder_schema.db"
    alembic_upgrade(_alembic_config(db_path), "head")

    cols = _table_columns(db_path, "user")
    assert {
        "reminder_enabled",
        "reminder_threshold",
        "last_reminder_sent_at",
    }.issubset(cols)


def test_migration_0007_adds_pronunciation_column(tmp_path: Path) -> None:
    """Revision 0007 must add front_pronunciation to the card table."""
    db_path = tmp_path / "pronunciation_schema.db"
    alembic_upgrade(_alembic_config(db_path), "head")

    assert "front_pronunciation" in _table_columns(db_path, "card")
