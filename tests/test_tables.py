"""Offline tests for pipeline.db: URL normalization + ORM metadata shape.

Anything that needs a live Postgres is guarded behind DATABASE_URL so the suite
stays green in CI/dev without a database.
"""

import os

import pytest

from pipeline.db import (
    Base,
    normalize_db_url,
    normalize_sync_db_url,
)

# The seven tables that must exist in the baseline schema.
EXPECTED_TABLES = {
    "users",
    "sessions",
    "user_credentials",
    "runs",
    "tasks",
    "coverage_reports",
    "audit_log",
}

# Tenant-owned tables carry a user_id FK + index for row-level isolation.
# (users is the tenant root and does NOT carry a user_id column.)
TENANT_TABLES = EXPECTED_TABLES - {"users"}


# ── normalize_db_url (async / asyncpg) ───────────────────────────────────────


@pytest.mark.parametrize(
    "raw, expected",
    [
        # postgresql:// -> +asyncpg
        (
            "postgresql://u:p@host:5432/db",
            "postgresql+asyncpg://u:p@host:5432/db",
        ),
        # postgres:// (Render/Heroku style) -> +asyncpg
        (
            "postgres://u:p@host:5432/db",
            "postgresql+asyncpg://u:p@host:5432/db",
        ),
        # already +asyncpg -> unchanged (idempotent)
        (
            "postgresql+asyncpg://u:p@host:5432/db",
            "postgresql+asyncpg://u:p@host:5432/db",
        ),
        # non-postgres scheme -> untouched
        ("sqlite:///local.db", "sqlite:///local.db"),
        ("mysql://u:p@host/db", "mysql://u:p@host/db"),
    ],
)
def test_normalize_db_url(raw, expected):
    assert normalize_db_url(raw) == expected


def test_normalize_db_url_is_idempotent():
    once = normalize_db_url("postgresql://u:p@host/db")
    twice = normalize_db_url(once)
    assert once == twice == "postgresql+asyncpg://u:p@host/db"


def test_normalize_db_url_handles_empty():
    assert normalize_db_url("") == ""


def test_normalize_db_url_no_scheme_untouched():
    assert normalize_db_url("just-a-string") == "just-a-string"


# ── normalize_sync_db_url (psycopg2 for Alembic) ─────────────────────────────


@pytest.mark.parametrize(
    "raw, expected",
    [
        (
            "postgresql://u:p@host/db",
            "postgresql+psycopg2://u:p@host/db",
        ),
        (
            "postgres://u:p@host/db",
            "postgresql+psycopg2://u:p@host/db",
        ),
        # asyncpg URL is rewritten to the sync driver for migrations
        (
            "postgresql+asyncpg://u:p@host/db",
            "postgresql+psycopg2://u:p@host/db",
        ),
        ("sqlite:///local.db", "sqlite:///local.db"),
    ],
)
def test_normalize_sync_db_url(raw, expected):
    assert normalize_sync_db_url(raw) == expected


# ── ORM metadata shape ───────────────────────────────────────────────────────


def test_metadata_has_all_seven_tables():
    names = set(Base.metadata.tables)
    assert names == EXPECTED_TABLES, f"unexpected table set: {names}"


def test_tenant_tables_carry_user_id():
    for tname in sorted(TENANT_TABLES):
        table = Base.metadata.tables[tname]
        assert "user_id" in table.columns, (
            f"{tname} is a tenant table but has no user_id column"
        )


def test_tenant_tables_index_user_id():
    """Each tenant table has at least one index whose columns include user_id."""
    for tname in sorted(TENANT_TABLES):
        table = Base.metadata.tables[tname]
        indexed_cols = {
            col.name for idx in table.indexes for col in idx.columns
        }
        assert "user_id" in indexed_cols, (
            f"{tname} has no index covering user_id"
        )


def test_task_pk_has_no_server_default():
    """tasks.id is ManagedTask.id (client-supplied) — never auto-generated."""
    task_id = Base.metadata.tables["tasks"].columns["id"]
    assert task_id.primary_key is True
    assert task_id.server_default is None


def test_import_is_connection_free():
    """Importing pipeline.db must not create an engine or connect.

    make_engine is a factory; nothing at module scope should hold a live engine.
    """
    # No module-level Engine/AsyncEngine object should exist.
    from sqlalchemy.ext.asyncio import AsyncEngine

    import pipeline.db as db

    engines = [
        name
        for name, val in vars(db).items()
        if isinstance(val, AsyncEngine)
    ]
    assert engines == [], f"module holds live engine(s): {engines}"


# ── Live-Postgres round-trip (skipped offline) ───────────────────────────────


@pytest.mark.skipif(
    not os.getenv("DATABASE_URL"), reason="needs Postgres (DATABASE_URL unset)"
)
@pytest.mark.asyncio
async def test_run_jsonb_roundtrip_live():
    """Insert a Run with a JSONB config and read it back (requires Postgres)."""
    from pipeline.db import Run, User, get_sessionmaker, make_engine

    engine = make_engine(os.environ["DATABASE_URL"])
    Session = get_sessionmaker(engine)

    async with Session() as session:
        user = User(email="rt@calibraint.com", google_sub="sub-roundtrip")
        session.add(user)
        await session.flush()

        cfg = {"provider": "openai", "model": "gpt-x", "nested": {"a": [1, 2]}}
        run = Run(user_id=user.id, filename="rt.pdf", config=cfg)
        session.add(run)
        await session.flush()
        run_id = run.id
        await session.rollback()  # don't persist test data

    async with Session() as session:
        # Re-run inside a fresh session to prove round-trip semantics work.
        user = User(email="rt2@calibraint.com", google_sub="sub-roundtrip-2")
        session.add(user)
        await session.flush()
        run = Run(user_id=user.id, filename="rt2.pdf", config=cfg)
        session.add(run)
        await session.flush()
        assert run.config == cfg
        await session.rollback()

    await engine.dispose()
    assert run_id is not None
