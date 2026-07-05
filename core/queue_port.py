# core/queue_port.py
"""Async-job queue port for the strangler-fig async/worker seam.

``JobQueue`` is the minimal surface the pipeline depends on to hand a unit of
work off to *something* that will run it later — today an in-memory
``FakeQueue`` (integrations/queue/fake.py), tomorrow a real arq/redis-backed
adapter. It is modeled on arq's ``enqueue_job`` but reduced to the single call
the seam actually needs.

Like the other ports in ``core/ports.py`` this is ``@runtime_checkable`` so a
concrete adapter structurally satisfies it with no inheritance or registration,
and tests can assert ``isinstance(obj, JobQueue)``. ``runtime_checkable`` only
verifies method *presence*, not signatures — the docstring here is the contract
for argument shapes.

This module is additive and dependency-free beyond ``typing`` — importing it
must not pull in arq, redis, litellm, or observability.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

__all__ = ["JobQueue"]


@runtime_checkable
class JobQueue(Protocol):
    """The async-enqueue surface the core depends on.

    Mirrors a minimal ``arq``-style ``enqueue_job``: name the worker function
    and hand it a JSON-serializable payload; get back an opaque job id the
    caller can use to correlate progress/results later.
    """

    def enqueue(self, func_name: str, payload: dict) -> str:
        """Enqueue ``func_name`` with ``payload``; return an opaque job id."""
        ...
