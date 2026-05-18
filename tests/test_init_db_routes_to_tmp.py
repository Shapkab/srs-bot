"""Regression test for Phase 8.10.

``migrations/env.py`` used to read ``DB_PATH`` from the environment and
override the ``sqlalchemy.url`` that the in-process caller had already
set on the Alembic ``Config``. With ``src/config.py:load_dotenv()``
running at import time, any test that imported anything under ``src/``
would silently pull the operator's ``DB_PATH`` from ``.env`` into
``os.environ`` — and the very next ``init_db(tmp_path / "...")`` call
would upgrade the wrong DB (usually the operator's real one), leaving
the tmp DB empty.

This file pins two contracts:

* ``init_db(path)`` populates *that* path with the full schema.
* The DB at the path pointed to by ``DB_PATH`` is NOT touched. The
  env-var coupling is gone for good.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

# These are the tables Base.metadata + the four migrations produce. If a
# future revision adds a table, extend this set rather than asserting
# equality.
_EXPECTED_TABLES = {
    "user",
    "card",
    "review_state",
    "review_log",
    "kv",
    "alembic_version",
}


def _tables(db_path: Path) -> set[str]:
    """Read the table list directly via sqlite3 — do not use SQLAlchemy
    so we never share session/engine state with the code under test."""
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        return {r[0] for r in rows}
    finally:
        conn.close()


def test_init_db_creates_schema_in_target_path(tmp_path: Path) -> None:
    """The DB at the path passed to init_db must end up with the head
    schema. (The autouse ``fresh_db`` fixture has already created
    ``tmp_path / "test.db"``; we deliberately route to a *different*
    path so this assertion is about THIS init_db call, not the fixture's.)
    """
    from src.db.engine import init_db

    target = tmp_path / "fresh.db"
    init_db(target)

    assert target.exists(), "init_db should create the target SQLite file"
    assert _EXPECTED_TABLES.issubset(_tables(target)), (
        f"expected at least {_EXPECTED_TABLES}, got {_tables(target)}"
    )


def test_init_db_does_not_touch_db_path_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Set DB_PATH to a path that does NOT exist, then init_db a
    *different* path. The DB_PATH-pointed file must remain absent, and
    the explicitly-passed path must be the one populated. This proves
    migrations/env.py is ignoring DB_PATH, which is the exact contract
    Phase 8.10 establishes.
    """
    from src.db.engine import init_db

    decoy = tmp_path / "decoy_from_env.db"
    target = tmp_path / "fresh.db"
    assert not decoy.exists()
    assert not target.exists()

    monkeypatch.setenv("DB_PATH", str(decoy))
    init_db(target)

    assert target.exists(), "init_db should populate the path it was passed"
    assert _EXPECTED_TABLES.issubset(_tables(target))
    assert not decoy.exists(), (
        "Phase 8.10 regression: migrations/env.py respected DB_PATH and "
        "upgraded the wrong DB"
    )
