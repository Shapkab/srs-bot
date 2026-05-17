"""Smoke test: ``alembic upgrade head`` on an empty DB produces a
schema with the same table-and-column set as ``Base.metadata.create_all``.

This is the regression we'd want if anyone touched the migration chain.
We don't compare SQL DDL byte-for-byte — alembic uses ``server_default``
where the models use Python-side ``default=``, so the columns line up
even though the raw CREATE TABLE strings don't.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


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
    repo_root = Path(__file__).resolve().parent.parent

    # ALEMBIC PATH ---------------------------------------------------------
    alembic_db = tmp_path / "alembic.db"
    env = {
        "DB_PATH": str(alembic_db),
        # Pass the host PATH through so alembic can find python.
        "PATH": __import__("os").environ.get("PATH", ""),
    }
    res = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(repo_root),
        env={**__import__("os").environ, **env},
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        pytest.fail(
            f"alembic upgrade head failed:\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
        )
    alembic_schema = _dump_schema(alembic_db)

    # CREATE_ALL PATH ------------------------------------------------------
    # Use a separate process so the test's own session-scoped engine isn't
    # disturbed. We invoke a tiny snippet via python -c.
    create_all_db = tmp_path / "create_all.db"
    snippet = (
        "from pathlib import Path;"
        "from src.db.engine import init_db;"
        f"init_db(Path('{create_all_db}'))"
    )
    res = subprocess.run(
        [sys.executable, "-c", snippet],
        cwd=str(repo_root),
        env={**__import__("os").environ},
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        pytest.fail(f"create_all failed:\nstderr:\n{res.stderr}")
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
