# tests/test_worker_seam.py
"""WAVE 2 STEP 2.3 (offline half) — arq worker + ArqQueue enqueue adapter.

pipeline/jobs.py already defines the enqueued unit of work; this pins the two
net-new pieces that make it deploy-ready:
  * ArqQueue — a JobQueue adapter that enqueues onto arq/Redis (arq imported
    lazily; construction never connects), tested against a mocked pool.
  * pipeline/worker.py — the arq WorkerSettings (functions/redis/on_startup/
    limits) plus the async job entrypoint that runs the (sync) pipeline off the
    event loop via asyncio.to_thread, delegating to the injected factory.
No real Redis or LLM is touched.
"""

from __future__ import annotations

from unittest import mock

import pytest

import pipeline.worker as worker
from core.queue_port import JobQueue
from integrations.progress.fake import FakeProgressStore
from integrations.queue.arq_queue import ArqQueue
from pipeline.jobs import RUN_PIPELINE_JOB, JobPayload

# ─── ArqQueue ────────────────────────────────────────────────────────────────


def test_arq_queue_satisfies_jobqueue_protocol():
    assert isinstance(ArqQueue("redis://localhost:6379/0"), JobQueue)


def test_arq_queue_does_not_connect_on_construction():
    q = ArqQueue("redis://localhost:6379/0")
    assert q._pool is None  # no pool created until first enqueue


@pytest.mark.asyncio
async def test_arq_queue_enqueue_async_delegates_to_pool():
    fake_pool = mock.AsyncMock()
    fake_pool.enqueue_job = mock.AsyncMock(return_value=mock.Mock(job_id="job-123"))
    with mock.patch("arq.create_pool", mock.AsyncMock(return_value=fake_pool)):
        q = ArqQueue("redis://localhost:6379/0")
        jid = await q.enqueue_async(RUN_PIPELINE_JOB, {"run_id": "r1"})
    assert jid == "job-123"
    fake_pool.enqueue_job.assert_awaited_once()


def test_arq_queue_sync_enqueue_drives_async():
    fake_pool = mock.AsyncMock()
    fake_pool.enqueue_job = mock.AsyncMock(return_value=mock.Mock(job_id="job-xyz"))
    with mock.patch("arq.create_pool", mock.AsyncMock(return_value=fake_pool)):
        jid = ArqQueue("redis://localhost:6379/0").enqueue(RUN_PIPELINE_JOB, {"run_id": "r1"})
    assert jid == "job-xyz"


# ─── WorkerSettings ──────────────────────────────────────────────────────────


def test_worker_settings_registers_pipeline_job_under_matching_name():
    names = {
        getattr(f, "name", getattr(f, "__name__", "")) for f in worker.WorkerSettings.functions
    }
    assert RUN_PIPELINE_JOB in names  # matches the name enqueue_run uses


def test_worker_settings_has_redis_and_lifecycle():
    ws = worker.WorkerSettings
    assert ws.redis_settings is not None
    assert callable(ws.on_startup)
    assert isinstance(ws.job_timeout, int) and ws.job_timeout > 0
    assert isinstance(ws.max_jobs, int) and ws.max_jobs > 0


@pytest.mark.asyncio
async def test_run_pipeline_job_task_delegates_and_records_progress():
    ran = {"called": False}

    class _StubOrch:
        def run(self):
            ran["called"] = True
            return {"tasks": []}

    progress = FakeProgressStore()
    ctx = {"progress": progress, "orchestrator_factory": lambda rc: _StubOrch()}
    payload = JobPayload(run_id="wrk-1", sow_pdf_path="/tmp/x.pdf").to_dict()

    out = await worker.run_pipeline_job_task(ctx, payload)

    assert ran["called"] is True
    assert out["run_id"] == "wrk-1"
    snap = progress.get("wrk-1")
    assert snap is not None and snap["status"] == "done"


@pytest.mark.asyncio
async def test_on_startup_configures_litellm_once(monkeypatch):
    import pipeline.llm_client as llm_client

    monkeypatch.setattr(llm_client, "_CONFIGURED", False)
    calls = {"n": 0}
    monkeypatch.setattr(
        llm_client, "_configure_litellm_logging", lambda: calls.__setitem__("n", calls["n"] + 1)
    )
    await worker.on_startup({})
    assert calls["n"] == 1
