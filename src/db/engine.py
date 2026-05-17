"""Engine + session factory.

SQLite for v1. Switch the URL to Postgres when scaling out — no other
code change is required because models use plain SQLAlchemy types
(no SQLite-specific column types).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

# Project root containing alembic.ini. We resolve it at import time so
# init_db doesn't have to rediscover it on every call.
_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


def _build_engine(db_path: Path) -> Engine:
    # check_same_thread=False is safe with SQLAlchemy's connection pool;
    # we never share a raw connection across threads. APScheduler workers
    # take their own session.
    url = f"sqlite:///{db_path}"
    eng = create_engine(url, echo=False, connect_args={"check_same_thread": False})

    @event.listens_for(eng, "connect")
    def _enable_sqlite_pragmas(dbapi_conn, _):
        # WAL = better write concurrency; foreign_keys = enforce FKs (off by default in SQLite).
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return eng


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def init_db(db_path: Path) -> None:
    """Run Alembic migrations to head."""
    global _engine, _SessionLocal
    _engine = _build_engine(db_path)
    _SessionLocal = sessionmaker(
        bind=_engine, autoflush=False, autocommit=False, expire_on_commit=False
    )

    # Self-healing migration: fresh DBs get baselined, legacy DBs are
    # picked up via the PRAGMA user_version handoff in migrations/env.py,
    # partially-migrated DBs are brought to head. Imported lazily so the
    # alembic dependency isn't loaded just for import-side-effects.
    from alembic.command import upgrade as _alembic_upgrade
    from alembic.config import Config as _AlembicConfig

    cfg = _AlembicConfig(str(_ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    _alembic_upgrade(cfg, "head")


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional session. Commits on success, rolls back on exception."""
    if _SessionLocal is None:
        raise RuntimeError("init_db() must be called before session_scope()")
    s = _SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
