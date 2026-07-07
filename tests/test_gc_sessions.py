"""Tests for scripts/gc_sessions.py — the expired-run garbage collector.

STREAM GC. ``gc_expired`` removes Run rows older than a TTL and their stored
objects (pdf/tree/node_index) from the ObjectStore, while leaving recent runs
and their objects intact. It must be deterministic (``now`` is injected) and
idempotent (a 2nd run with the same ``now`` is a no-op).

These run offline on ``sqlite+aiosqlite:///:memory:`` + a tmp LocalObjectStore.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from integrations.object_store import LocalObjectStore, key_for
from pipeline.db import Base, Run, User
from scripts.gc_sessions import gc_expired

# ── helpers ──────────────────────────────────────────────────────────────────


async def _make_sessionmaker():
    """Fresh in-memory async engine with the schema created.

    A shared-cache ``:memory:`` DB lives only as long as its single connection.
    With the default (queue) pool, aiosqlite may hand out more than one
    connection per engine — and each fresh ``:memory:`` connection is a *distinct*
    empty database. That lets ``gc_expired``'s own ``sessionmaker()`` sessions
    bind to a different DB than the one the test seeded, causing intermittent
    cross-test bleed and a flaky idempotency gate. ``StaticPool`` pins a single
    connection for the engine's whole lifetime, so every session — seed, GC, and
    assertion — reads and writes the same in-memory database deterministically.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    return engine, sm


async def _seed_run(sm, store, *, user_id, created_at, name_prefix):
    """Insert one User+Run with a stored pdf object; return (run_id, obj_key)."""
    run_id = str(uuid.uuid4())
    obj_key = key_for(user_id, run_id, f"{name_prefix}.pdf")
    store.put(obj_key, b"%PDF-1.4 fake bytes")

    async with sm() as sess:
        # A user per run keeps FK setup simple (email/google_sub are unique).
        sess.add(
            User(
                id=user_id,
                email=f"{name_prefix}-{user_id[:8]}@example.com",
                google_sub=f"sub-{name_prefix}-{user_id[:8]}",
            )
        )
        sess.add(
            Run(
                id=run_id,
                user_id=user_id,
                filename=f"{name_prefix}.pdf",
                config={},
                status="completed",
                created_at=created_at,
                completed_at=created_at,
                pdf_object_key=obj_key,
            )
        )
        await sess.commit()
    return run_id, obj_key


async def _run_ids(sm):
    async with sm() as sess:
        rows = (await sess.execute(select(Run.id))).scalars().all()
    return set(rows)


async def _run_count(sm):
    async with sm() as sess:
        return (await sess.execute(select(func.count()).select_from(Run))).scalar_one()


# ── GATE 1: expired run + its object are removed; recent survives ─────────────


@pytest.mark.asyncio
async def test_gc_removes_expired_run_and_object_keeps_recent(tmp_path):
    engine, sm = await _make_sessionmaker()
    store = LocalObjectStore(base_dir=tmp_path / "objectstore")

    now = datetime(2026, 7, 5, 12, 0, 0, tzinfo=timezone.utc)
    ttl = 30 * 24 * 3600  # 30 days

    expired_id, expired_key = await _seed_run(
        sm, store,
        user_id=str(uuid.uuid4()),
        created_at=now - timedelta(days=90),  # far in the past → expired
        name_prefix="old",
    )
    recent_id, recent_key = await _seed_run(
        sm, store,
        user_id=str(uuid.uuid4()),
        created_at=now - timedelta(days=1),  # inside TTL → keep
        name_prefix="new",
    )

    # Pre-conditions: both rows + both objects exist.
    assert await _run_ids(sm) == {expired_id, recent_id}
    assert store.exists(expired_key)
    assert store.exists(recent_key)

    removed = await gc_expired(sm, store, ttl_seconds=ttl, now=now)

    # Expired run row gone; its object gone.
    assert await _run_ids(sm) == {recent_id}
    assert not store.exists(expired_key)
    # Recent run row + object remain.
    assert store.exists(recent_key)
    assert removed >= 1  # at least the expired run was collected

    await engine.dispose()


# ── GATE 2: idempotency — a 2nd call with same ``now`` is a no-op ─────────────


@pytest.mark.asyncio
async def test_gc_is_idempotent_second_call_noop(tmp_path):
    engine, sm = await _make_sessionmaker()
    store = LocalObjectStore(base_dir=tmp_path / "objectstore")

    now = datetime(2026, 7, 5, 12, 0, 0, tzinfo=timezone.utc)
    ttl = 30 * 24 * 3600

    await _seed_run(
        sm, store,
        user_id=str(uuid.uuid4()),
        created_at=now - timedelta(days=90),
        name_prefix="old",
    )
    recent_id, recent_key = await _seed_run(
        sm, store,
        user_id=str(uuid.uuid4()),
        created_at=now - timedelta(days=1),
        name_prefix="new",
    )

    first = await gc_expired(sm, store, ttl_seconds=ttl, now=now)
    assert first >= 1

    count_after_first = await _run_count(sm)

    # Second call, SAME now → nothing further removed, no error.
    second = await gc_expired(sm, store, ttl_seconds=ttl, now=now)
    assert second == 0
    assert await _run_count(sm) == count_after_first
    assert await _run_ids(sm) == {recent_id}
    assert store.exists(recent_key)

    await engine.dispose()


# ── GATE 3: never deletes objects for non-expired runs ───────────────────────


@pytest.mark.asyncio
async def test_gc_never_touches_recent_object(tmp_path):
    engine, sm = await _make_sessionmaker()
    store = LocalObjectStore(base_dir=tmp_path / "objectstore")

    now = datetime(2026, 7, 5, 12, 0, 0, tzinfo=timezone.utc)
    ttl = 7 * 24 * 3600  # 7 days

    _, recent_key = await _seed_run(
        sm, store,
        user_id=str(uuid.uuid4()),
        created_at=now - timedelta(hours=1),  # very recent
        name_prefix="fresh",
    )

    await gc_expired(sm, store, ttl_seconds=ttl, now=now)

    # Object still present AND readable via store.get with original bytes.
    assert store.exists(recent_key)
    assert store.get(recent_key) == b"%PDF-1.4 fake bytes"

    await engine.dispose()
