import logging
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

log = logging.getLogger("alembic.env")

# Revision that produced the legacy retired-scripts schema (Phase 6.1's
# baseline). A pre-Phase-7 DB with PRAGMA user_version == 1 should be
# stamped here, then continue with 0002+. See run_migrations_online.
_LEGACY_BASELINE_REV = "0001"

# Make the project importable so we can pull in models.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db.models import Base  # noqa: E402

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Allow `DB_PATH=/some/where.db alembic upgrade head` to override the URL
# without editing alembic.ini.
_db_path = os.getenv("DB_PATH")
if _db_path:
    config.set_main_option("sqlalchemy.url", f"sqlite:///{_db_path}")

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Models' metadata, used by --autogenerate.
target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def _stamp_alembic_at(connection, revision: str) -> None:
    """Write ``revision`` into the ``alembic_version`` table without
    running any migration. Used to hand off a legacy DB (built by the
    retired one-shot scripts) to the Alembic chain mid-stream.
    """
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS alembic_version "
        "(version_num VARCHAR(32) NOT NULL, "
        "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
    )
    connection.exec_driver_sql("DELETE FROM alembic_version")
    connection.exec_driver_sql(
        "INSERT INTO alembic_version (version_num) VALUES (:r)",
        {"r": revision},
    )


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    Before delegating to ``context.run_migrations()`` we inspect
    ``PRAGMA user_version``:

      * 0 — fresh DB (no prior migration). Standard alembic upgrade
        from baseline.
      * 1 — pre-Phase-7 DB built by the retired ``scripts/migrate_001-003``
        one-shots. The baseline schema is already in place; stamp at
        revision 0001 so the baseline's upgrade() does not re-run
        against existing tables. Subsequent revisions (0002+) still
        apply normally.
      * anything else — log a warning and proceed unchanged.

    PRAGMA user_version is not re-written; ``alembic_version`` is the
    source of truth from now on.
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        # Pre-handoff inspection runs in its own short-lived transaction
        # so it does NOT leave an open transaction on the connection that
        # would prevent Alembic from committing its own alembic_version
        # update at the end of run_migrations().
        with connection.begin():
            already_tracked = (
                connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name='alembic_version'"
                ).first()
                is not None
            )
            if not already_tracked:
                row = connection.exec_driver_sql("PRAGMA user_version").first()
                user_version = row[0] if row is not None else 0
                if user_version == 1:
                    log.info(
                        "Legacy DB detected (PRAGMA user_version=1); "
                        "stamping at %s",
                        _LEGACY_BASELINE_REV,
                    )
                    _stamp_alembic_at(connection, _LEGACY_BASELINE_REV)
                elif user_version > 1:
                    log.warning(
                        "Unexpected PRAGMA user_version=%s; proceeding "
                        "without handoff",
                        user_version,
                    )

        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
