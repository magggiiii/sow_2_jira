"""Garbage-collect expired extraction runs and their stored objects.

STREAM GC. Long-lived deployments accumulate ``runs`` rows and the blob objects
they reference (the uploaded PDF, the cached PageIndex tree, the flattened node
index). Once a run is older than a configured TTL there is no reason to keep it
around: this collector deletes the expired ``Run`` rows AND removes their
associated objects from the :class:`~integrations.object_store.ObjectStore`.

Design notes
------------
* **Deterministic.** ``now`` is injected as a parameter; this module never calls
  ``datetime.now`` at import time. Tests pin ``now`` so results are stable.
* **Idempotent.** A run only matches once — after its row is deleted, a second
  pass with the same ``now`` collects nothing and never errors.
* **Direct DB access.** We query ``pipeline.db`` ORM models directly and do NOT
  depend on ``integrations/repositories.py`` (owned by a concurrent agent).
* **Import-safe.** No engine, connection, or network I/O at import time. The
  CLI entrypoint builds the engine and reads env vars only under ``__main__``.

A run is considered *expired* when the most recent of its timestamps is strictly
older than ``now - ttl``. We prefer ``completed_at`` when present (a finished run
should age from when it finished), falling back to ``created_at`` for runs that
never completed.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from integrations.object_store import LocalObjectStore
from pipeline.db import Run

__all__ = ["gc_expired", "main"]


# The Run columns that reference stored blob objects. Deleting a Run means
# deleting every non-null object it points at.
_OBJECT_KEY_ATTRS = (
    "pdf_object_key",
    "tree_object_key",
    "node_index_object_key",
)


def _as_aware(dt: Optional[datetime]) -> Optional[datetime]:
    """Coerce a possibly-naive datetime to UTC-aware for safe comparison.

    SQLite round-trips ``TIMESTAMPTZ`` as naive datetimes, so a value read back
    from the DB may lack tzinfo even though it was written aware. We assume UTC
    for naive values (the schema stores everything in UTC).
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _effective_age_ts(run: Run) -> Optional[datetime]:
    """Return the timestamp a run should age from: completed_at, else created_at."""
    return _as_aware(run.completed_at) or _as_aware(run.created_at)


async def gc_expired(
    sessionmaker: async_sessionmaker,
    object_store,
    *,
    ttl_seconds: int,
    now: datetime,
) -> int:
    """Delete runs older than ``ttl_seconds`` and their stored objects.

    Parameters
    ----------
    sessionmaker:
        An ``async_sessionmaker`` bound to the target database.
    object_store:
        An object store satisfying the ``ObjectStore`` port (e.g.
        :class:`~integrations.object_store.LocalObjectStore`). Its ``delete``
        surface is used per object key; we tolerate a missing object.
    ttl_seconds:
        Age threshold in seconds. A run whose effective timestamp is strictly
        before ``now - ttl_seconds`` is expired.
    now:
        The reference "current" time (injected for determinism).

    Returns
    -------
    int
        The number of expired ``Run`` rows removed. ``0`` when nothing expired
        (which makes a repeat call a verifiable no-op).
    """
    now = _as_aware(now)
    cutoff = now - timedelta(seconds=ttl_seconds)

    # 1) Identify expired runs and collect their object keys BEFORE deleting.
    expired_ids: list[str] = []
    object_keys: list[str] = []

    async with sessionmaker() as session:
        rows = (await session.execute(select(Run))).scalars().all()
        for run in rows:
            ts = _effective_age_ts(run)
            if ts is None:
                # No usable timestamp → treat as not-yet-expired (never GC blind).
                continue
            if ts < cutoff:
                expired_ids.append(run.id)
                for attr in _OBJECT_KEY_ATTRS:
                    key = getattr(run, attr, None)
                    if key:
                        object_keys.append(key)

    if not expired_ids:
        return 0

    # 2) Remove associated objects from the store. Best-effort per key: a
    #    missing object (already gone) must not abort the collection.
    for key in object_keys:
        _delete_object(object_store, key)

    # 3) Delete the expired Run rows (cascades to tasks/coverage/audit via FK).
    async with sessionmaker() as session:
        await session.execute(delete(Run).where(Run.id.in_(expired_ids)))
        await session.commit()

    return len(expired_ids)


def _delete_object(object_store, key: str) -> None:
    """Delete a single object key from the store, tolerating absence.

    ``LocalObjectStore`` exposes ``delete_run_prefix`` (which unlinks a single
    file when the key resolves to one). We prefer that; if a store only offers
    ``exists`` we skip silently. Any KeyError/FileNotFoundError is swallowed so
    the collector stays idempotent.
    """
    try:
        deleter = getattr(object_store, "delete_run_prefix", None)
        if callable(deleter):
            deleter(key)
            return
        # Fallback: nothing to do if the store has no delete surface.
    except (KeyError, FileNotFoundError):
        return
    except Exception:
        # Never let a single object failure abort GC of the DB rows.
        return


# ── CLI entrypoint (no DB/network work at import time) ────────────────────────


def main(argv: Optional[list[str]] = None) -> int:
    """Read ``DATABASE_URL`` + TTL env and run one GC pass. Returns rows removed.

    Env vars:
      * ``DATABASE_URL``      — SQLAlchemy URL (normalized to asyncpg internally).
      * ``GC_TTL_SECONDS``    — TTL in seconds (default 30 days).
      * ``OBJECT_STORE_DIR``  — base dir for the LocalObjectStore
                                (default ``data/objectstore``).
    """
    import asyncio

    from pipeline.db import get_sessionmaker, make_engine

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")

    ttl_seconds = int(os.environ.get("GC_TTL_SECONDS", str(30 * 24 * 3600)))
    store_dir = os.environ.get("OBJECT_STORE_DIR", "data/objectstore")

    store = LocalObjectStore(base_dir=store_dir)

    async def _run() -> int:
        engine = make_engine(database_url)
        try:
            sm = get_sessionmaker(engine)
            return await gc_expired(
                sm,
                store,
                ttl_seconds=ttl_seconds,
                now=datetime.now(timezone.utc),
            )
        finally:
            await engine.dispose()

    removed = asyncio.run(_run())
    print(f"gc_sessions: removed {removed} expired run(s)")
    return removed


if __name__ == "__main__":  # pragma: no cover - thin CLI shell
    main()
