"""arq worker process definition (WAVE 2 STEP 2.3).

This is the entrypoint for the separate Render worker service that actually runs
pipeline jobs off the web process. It wires arq to the queue-agnostic unit of
work in ``pipeline.jobs``:

  * ``run_pipeline_job_task`` — the async arq entrypoint. It rebuilds the
    ``JobPayload``, then runs the *synchronous* ``pipeline.jobs.run_pipeline_job``
    off the event loop via ``asyncio.to_thread`` so the long CPU/IO-bound run
    never blocks arq's loop. Progress transitions and the orchestrator factory
    are pulled from the arq ``ctx`` so tests inject a ``FakeProgressStore`` + a
    stub factory with no litellm/Redis.
  * ``WorkerSettings`` — arq configuration. ``functions`` registers the task
    under the SAME name the enqueue side uses (``pipeline.jobs.RUN_PIPELINE_JOB``)
    so ``enqueue_run`` and the worker agree. ``on_startup`` calls
    ``configure_litellm_once`` because the worker is a *separate process* from the
    web app and must configure litellm globals itself.

Run it with:  ``arq pipeline.worker.WorkerSettings``
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from arq import func
from arq.connections import RedisSettings

from integrations.progress.fake import FakeProgressStore
from pipeline.jobs import (
    RUN_PIPELINE_JOB,
    JobPayload,
    default_orchestrator_factory,
    run_pipeline_job,
)

# Default local Redis when REDIS_URL is unset (the local dev docker redis:7).
_DEFAULT_REDIS_URL = "redis://localhost:6379/0"


async def run_pipeline_job_task(ctx: dict, payload: dict) -> dict:
    """arq entrypoint: run one pipeline job off the event loop.

    ``ctx`` may carry a ``progress`` store and an ``orchestrator_factory`` (arq's
    ``on_startup`` would populate these in production); tests inject them
    directly. The heavy, synchronous ``run_pipeline_job`` runs in a worker thread
    so arq's loop stays responsive to other jobs and signals.
    """
    job = JobPayload.from_dict(payload)
    progress = ctx.get("progress") or FakeProgressStore()
    factory = ctx.get("orchestrator_factory") or default_orchestrator_factory
    await asyncio.to_thread(
        run_pipeline_job, job, progress=progress, orchestrator_factory=factory
    )
    return {"run_id": job.run_id, "status": "done"}


async def on_startup(ctx: dict) -> None:
    """Configure litellm globals once for this (separate) worker process."""
    from pipeline.llm_client import configure_litellm_once

    configure_litellm_once()


def _redis_settings() -> Any:
    """Build arq RedisSettings from REDIS_URL (no connection is opened here)."""
    return RedisSettings.from_dsn(os.environ.get("REDIS_URL", _DEFAULT_REDIS_URL))


class WorkerSettings:
    """arq worker configuration (``arq pipeline.worker.WorkerSettings``)."""

    # Registered under RUN_PIPELINE_JOB so it matches what enqueue_run enqueues.
    functions = [func(run_pipeline_job_task, name=RUN_PIPELINE_JOB)]
    on_startup = on_startup
    redis_settings = _redis_settings()
    max_jobs = 10
    job_timeout = 3600  # seconds: a full extraction run can be long-running
    keep_result = 3600
    handle_signals = True
