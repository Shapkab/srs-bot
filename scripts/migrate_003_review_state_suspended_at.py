"""One-shot migration: add ``suspended_at`` to review_state.

Run once on existing production DBs that predate leech suspension.
Fresh installs do not need this — ``Base.metadata.create_all`` creates
the column from the model definition directly.

The ``kv`` table is created on the fly by ``create_all`` for fresh DBs;
existing DBs get it implicitly the next time ``init_db`` runs (its
``create_all`` is idempotent and only creates missing tables). No
separate migration step needed for ``kv``.

This will be re-expressed as an Alembic revision in Phase 6 and the
script deleted then.

Usage:
    python -m scripts.migrate_003_review_state_suspended_at /path/to/srs.db
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

COLUMN_NAME = "suspended_at"
COLUMN_TABLE = "review_state"


def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cur.fetchall())


def migrate(db_path: Path) -> bool:
    conn = sqlite3.connect(str(db_path))
    try:
        if column_exists(conn, COLUMN_TABLE, COLUMN_NAME):
            return False
        conn.execute(
            f"ALTER TABLE {COLUMN_TABLE} ADD COLUMN {COLUMN_NAME} DATETIME"
        )
        conn.commit()
        return True
    finally:
        conn.close()


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <path-to-srs.db>", file=sys.stderr)
        return 2
    db_path = Path(sys.argv[1])
    if not db_path.exists():
        print(f"no such file: {db_path}", file=sys.stderr)
        return 1
    added = migrate(db_path)
    print(f"{COLUMN_NAME}: {'added' if added else 'already present'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
