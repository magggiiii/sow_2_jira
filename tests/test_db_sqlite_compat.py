"""SQLite compatibility for the 7-table ORM in ``pipeline/db.py``.

W2-S0. The same ORM must compile + round-trip on SQLite (offline tests) WITHOUT
changing the Postgres DDL. These tests prove:

1. SYNC create_all on ``sqlite:///:memory:`` succeeds; all 7 tables exist.
2. ASYNC round-trip on ``sqlite+aiosqlite:///:memory:`` — a JSONB dict column
   deep-equals what was inserted; ids match exactly.
3. Task.id parity — an app-supplied UUID str is read back byte-identical.
4. Postgres DDL is UNCHANGED — compiling against the postgresql dialect still
   emits INET / JSONB / gen_random_uuid().
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.schema import CreateTable

from pipeline.db import Base, Run, Session, Task, User

EXPECTED_TABLES = {
    "users",
    "sessions",
    "user_credentials",
    "runs",
    "tasks",
    "coverage_reports",
    "audit_log",
}


# ── GATE 1: SYNC create_all on sqlite ────────────────────────────────────────


def test_sync_create_all_on_sqlite_creates_all_seven_tables():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    names = set(inspect(engine).get_table_names())
    assert EXPECTED_TABLES.issubset(names), (
        f"missing tables: {EXPECTED_TABLES - names}"
    )
    assert len(EXPECTED_TABLES & names) == 7


# ── GATE 2: ASYNC JSONB round-trip on sqlite+aiosqlite ───────────────────────


@pytest.mark.asyncio
async def test_async_jsonb_roundtrip_and_id_parity_on_sqlite():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    user_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    config = {"a": 1, "nested": {"b": [2, 3]}}

    from sqlalchemy.ext.asyncio import async_sessionmaker

    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as sess:
        sess.add(
            User(
                id=user_id,
                email="w2s0@example.com",
                google_sub="sub-w2s0",
            )
        )
        sess.add(
            Run(
                id=run_id,
                user_id=user_id,
                filename="sow.pdf",
                config=config,
                status="queued",
            )
        )
        await sess.commit()

    async with sm() as sess:
        got = (
            await sess.execute(select(Run).where(Run.id == run_id))
        ).scalar_one()
        assert got.id == run_id
        assert got.user_id == user_id
        # Deep equal — proves JSONB actually deserializes on sqlite reads.
        assert got.config == config
        assert got.config["nested"]["b"] == [2, 3]

    await engine.dispose()


# ── GATE 3: Task.id parity (never regenerated) ───────────────────────────────


@pytest.mark.asyncio
async def test_task_id_is_byte_identical_on_sqlite():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    from sqlalchemy.ext.asyncio import async_sessionmaker

    user_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    task_id = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"  # specific fixed uuid str

    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as sess:
        sess.add(User(id=user_id, email="t@example.com", google_sub="sub-t"))
        sess.add(
            Run(
                id=run_id,
                user_id=user_id,
                filename="s.pdf",
                config={},
                status="queued",
            )
        )
        sess.add(
            Task(
                id=task_id,
                run_id=run_id,
                user_id=user_id,
                title="Do the thing",
                status="pending",
                confidence=0.9,
            )
        )
        await sess.commit()

    async with sm() as sess:
        got = (
            await sess.execute(select(Task).where(Task.id == task_id))
        ).scalar_one()
        assert got.id == task_id  # byte-identical, never regenerated

    await engine.dispose()


# ── GATE 4: Postgres DDL UNCHANGED (shim is sqlite-only) ─────────────────────


def test_postgres_ddl_still_uses_inet_jsonb_and_gen_random_uuid():
    pg = postgresql.dialect()
    sessions_ddl = str(CreateTable(Session.__table__).compile(dialect=pg))
    runs_ddl = str(CreateTable(Run.__table__).compile(dialect=pg))

    assert "INET" in sessions_ddl, sessions_ddl
    assert "JSONB" in runs_ddl, runs_ddl
    assert "gen_random_uuid()" in runs_ddl, runs_ddl
    assert "gen_random_uuid()" in sessions_ddl, sessions_ddl
