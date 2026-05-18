"""Smoke test: ``alembic upgrade head`` on an empty DB produces a
schema with the same table-and-column set as ``Base.metadata.create_all``.

This is the regression we'd want if anyone touched the migration chain.
We don't compare SQL DDL byte-for-byte — alembic uses ``server_default``
where the models use Python-side ``default=``, so the columns line up
even though the raw CREATE TABLE strings don't.

Both paths run in-process via their respective Python APIs — no
subprocess + ``DB_PATH`` env-var coupling (removed in Phase 8.10) — so
each side of the comparison is unambiguous about which DB it targets.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic.command import upgrade as alembic_upgrade
from alembic.config import Config
from sqlalchemy import create_engine

from src.db.models import Base

REPO_ROOT = Path(__file__).resolve().parent.parent


def _alembic_config(db_path: Path) -> Config:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def _dump_schema(db_path: Path) -> dict[str, list[tuple[str, str, bool]]]:
    """Returns {table: [(col_name, col_type, not_null), ...]}, alembic
    bookkeeping tables filtered out."""
    conn = sqlite3.connect(str(db_path))
    try:
        tables = sorted(
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                "AND name NOT LIKE 'alembic_%'"
            )
        )
        out: dict[str, list[tuple[str, str, bool]]] = {}
        for t in tables:
            out[t] = [
                (row[1], row[2], bool(row[3]))
                for row in conn.execute(f"PRAGMA table_info({t})")
            ]
        return out
    finally:
        conn.close()


def test_alembic_upgrade_head_matches_create_all(tmp_path: Path) -> None:
    # ALEMBIC PATH ---------------------------------------------------------
    alembic_db = tmp_path / "alembic.db"
    alembic_upgrade(_alembic_config(alembic_db), "head")
    alembic_schema = _dump_schema(alembic_db)

    # CREATE_ALL PATH ------------------------------------------------------
    # Use a freshly-constructed engine so we don't disturb the
    # module-level engine that the autouse ``fresh_db`` fixture wired up.
    create_all_db = tmp_path / "create_all.db"
    engine = create_engine(f"sqlite:///{create_all_db}")
    try:
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()
    create_all_schema = _dump_schema(create_all_db)

    # COMPARE --------------------------------------------------------------
    assert sorted(alembic_schema) == sorted(create_all_schema), (
        f"table set differs: alembic={sorted(alembic_schema)} "
        f"create_all={sorted(create_all_schema)}"
    )
    for table in alembic_schema:
        assert alembic_schema[table] == create_all_schema[table], (
            f"column shape differs for {table!r}:\n"
            f"  alembic:   {alembic_schema[table]}\n"
            f"  create_all:{create_all_schema[table]}"
        )
