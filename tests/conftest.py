"""Shared test fixtures.

A single autouse fixture initializes a fresh SQLite DB in ``tmp_path``
before every test, so individual test files no longer need to repeat
the boilerplate. ``init_db`` already rebuilds the module-level engine
and session factory, so tests that don't touch the DB are unaffected.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.db.engine import init_db


@pytest.fixture(autouse=True)
def fresh_db(tmp_path: Path) -> Path:
    """Per-test SQLite DB, returned as a Path for tests that want it."""
    db_path = tmp_path / "test.db"
    init_db(db_path)
    return db_path
