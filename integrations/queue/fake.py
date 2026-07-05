"""In-memory ``JobQueue`` fake for the async-job seam (offline).

``FakeQueue`` satisfies the ``JobQueue`` Protocol (core/queue_port.py)
structurally. Instead of dispatching to a worker it records every enqueue in a
public ``self.enqueued`` list, so tests (and the future route rewrite) can
assert what *would* have been dispatched without any arq/redis/network. The
real arq-backed adapter will land later behind the same interface.
"""

from __future__ import annotations

from uuid import uuid4

__all__ = ["FakeQueue"]


class FakeQueue:
    """Records enqueues in memory and returns a deterministic opaque id.

    ``self.enqueued`` is a public list of dicts, one per call, each with keys
    ``func_name``, ``payload``, and ``job_id``.
    """

    def __init__(self) -> None:
        self.enqueued: list[dict] = []

    def enqueue(self, func_name: str, payload: dict) -> str:
        job_id = f"fake-{uuid4().hex}"
        self.enqueued.append(
            {"func_name": func_name, "payload": payload, "job_id": job_id}
        )
        return job_id
