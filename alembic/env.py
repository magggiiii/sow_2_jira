"""Alembic environment.

Reads ``DATABASE_URL`` from the environment and runs migrations against a
*synchronous* psycopg2 driver (Alembic does not run under asyncio here). The URL
is normalized via ``pipeline.db.normalize_sync_db_url`` so the same
``postgresql://`` / ``postgres://`` / ``postgresql+asyncpg://`` value used by the
async app also works for migrations.

``target_metadata`` is ``pipeline.db.Base.metadata`` so ``--autogenerate`` sees
the full ORM schema.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# Make the ORM metadata importable when Alembic runs from the repo root.
from pipeline.db import Base, normalize_sync_db_url

# Alembic Config object (values from alembic.ini).
config = context.config

# Configure logging from the .ini if present.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    """Resolve the sync migration URL from DATABASE_URL (or the .ini fallback)."""
    url = os.getenv("DATABASE_URL") or config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set; Alembic needs a Postgres URL to run "
            "migrations (offline authoring does not require one)."
        )
    return normalize_sync_db_url(url)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL to a script, no DB connection)."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (connect and apply against the DB)."""
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _database_url()

    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
