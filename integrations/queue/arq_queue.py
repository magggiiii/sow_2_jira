"""arq/Redis-backed ``JobQueue`` adapter (WAVE 2 STEP 2.3).

``ArqQueue`` satisfies the ``core.queue_port.JobQueue`` Protocol and enqueues jobs
onto an arq Redis queue. It is the production counterpart to
``integrations.queue.fake.FakeQueue``.

Design notes:
- **arq is imported lazily** inside the methods, so importing this module (and
  therefore ``app.container``) never requires arq/redis to be installed or a
  Redis to be reachable — only actually enqueuing does.
- **No connection at construction.** The pool is created on the first enqueue and
  cached, so building the adapter is cheap and offline-safe.
- ``enqueue_async`` is the native async entrypoint (await it from a FastAPI
  handler that already runs on the event loop). ``enqueue`` is the synchronous
  ``JobQueue`` method — it drives ``enqueue_async`` to completion and is intended
  for sync callers/scripts, not for use inside a running event loop.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

__all__ = ["ArqQueue"]


class ArqQueue:
    """Enqueues jobs onto arq/Redis; satisfies ``core.queue_port.JobQueue``."""

    def __init__(self, redis_url: str):
        self.redis_url = redis_url
        self._pool: Optional[Any] = None

    async def _get_pool(self) -> Any:
        if self._pool is None:
            from arq import create_pool
            from arq.connections import RedisSettings

            self._pool = await create_pool(RedisSettings.from_dsn(self.redis_url))
        return self._pool

    async def enqueue_async(
        self, func_name: str, payload: dict, *, job_id: Optional[str] = None
    ) -> str:
        """Enqueue ``func_name`` with ``payload``; return the arq job id.

        ``job_id`` (usually the run_id) makes the enqueue idempotent — arq drops a
        duplicate enqueue for the same job id, so a retried request never spawns a
        second run.
        """
        pool = await self._get_pool()
        job = await pool.enqueue_job(func_name, payload, _job_id=job_id)
        if job is not None:
            return job.job_id
        # None => a job with this _job_id already exists (idempotent no-op).
        return job_id or ""

    def enqueue(self, func_name: str, payload: dict) -> str:
        """Synchronous ``JobQueue`` enqueue (drives the async path to completion)."""
        return asyncio.run(self.enqueue_async(func_name, payload))
