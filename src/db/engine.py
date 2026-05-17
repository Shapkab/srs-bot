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

from src.db.models import Base


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
    """Create the engine, session factory, and tables if missing.

    Idempotent. Call once at startup.
    For schema changes, add Alembic later — v1 uses create_all only.
    """
    global _engine, _SessionLocal
    _engine = _build_engine(db_path)
    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False, expire_on_commit=False)
    Base.metadata.create_all(_engine)


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
