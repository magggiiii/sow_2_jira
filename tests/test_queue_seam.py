"""SEAM-A: async-job queue port + in-memory fake (offline).

Verifies the JobQueue Protocol contract and the FakeQueue adapter that satisfies
it structurally. No arq/redis/network — this is the seam a real worker will
later plug into.
"""

from __future__ import annotations

from core.queue_port import JobQueue
from integrations.queue.fake import FakeQueue


def test_fakequeue_satisfies_jobqueue_protocol():
    # Gate 1: structural isinstance against the runtime_checkable Protocol.
    assert isinstance(FakeQueue(), JobQueue) is True


def test_enqueue_returns_id_and_records():
    q = FakeQueue()
    payload = {"run_id": "r-123", "kind": "extraction"}
    job_id = q.enqueue("run_pipeline_job", payload)

    # Returns a non-empty opaque id.
    assert isinstance(job_id, str)
    assert job_id

    # Records the enqueue in the public list.
    assert len(q.enqueued) == 1
    rec = q.enqueued[0]
    assert rec["func_name"] == "run_pipeline_job"
    assert rec["payload"] == payload
    assert rec["job_id"] == job_id


def test_enqueue_ids_are_distinct_across_calls():
    q = FakeQueue()
    id1 = q.enqueue("run_pipeline_job", {"run_id": "a"})
    id2 = q.enqueue("run_pipeline_job", {"run_id": "b"})
    assert id1 != id2
    assert len(q.enqueued) == 2
