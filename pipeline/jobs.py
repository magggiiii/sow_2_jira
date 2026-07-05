"""pipeline.jobs — the enqueued unit-of-work seam for the async/worker path.

This module defines *what gets enqueued* and *what the worker runs*, decoupled
from any concrete queue or orchestrator:

- ``JobPayload`` — a small, JSON-serializable description of one pipeline run
  (the future route will build this and hand it to ``enqueue_run``).
- ``enqueue_run(queue, payload)`` — routes the payload through the ``JobQueue``
  port and returns the opaque job id.
- ``run_pipeline_job(payload, *, progress, orchestrator_factory)`` — the job
  entrypoint a future arq/redis worker calls. It writes progress transitions
  through the ``ProgressStore`` port and delegates the actual work to an
  injected ``orchestrator_factory``, so it is fully testable with a STUB
  factory and never touches litellm.

IMPORTANT: ``PipelineOrchestrator`` (and, transitively, litellm) are imported
LAZILY inside the default factory helper, never at module top. ``import
pipeline.jobs`` therefore stays clean and offline. This module also does NOT
import ``pipeline.observability`` (being rewritten concurrently) — it uses no
logging framework.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Optional

from core.progress_port import ProgressStore
from core.queue_port import JobQueue

__all__ = [
    "JobPayload",
    "RUN_PIPELINE_JOB",
    "enqueue_run",
    "run_pipeline_job",
]

# The worker-function name the queue dispatches to. Kept as a constant so the
# enqueue side and the (future) worker registration agree on one string.
RUN_PIPELINE_JOB = "run_pipeline_job"


@dataclass
class JobPayload:
    """JSON-serializable description of one enqueued pipeline run.

    Fields mirror the identity a worker needs to reconstruct a ``RunConfig`` on
    the other side of the queue. ``run_config`` carries any extra RunConfig
    fields verbatim so the payload stays forward-compatible without this seam
    needing to know every knob.
    """

    run_id: str
    kind: str = "extraction"
    sow_pdf_path: str = ""
    run_config: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Return a plain JSON-serializable dict for the queue payload."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "JobPayload":
        """Rebuild a ``JobPayload`` from a queue payload dict (worker side)."""
        return cls(
            run_id=data["run_id"],
            kind=data.get("kind", "extraction"),
            sow_pdf_path=data.get("sow_pdf_path", ""),
            run_config=data.get("run_config", {}) or {},
        )


def enqueue_run(queue: JobQueue, payload: JobPayload) -> str:
    """Route ``payload`` through the ``JobQueue`` port; return the job id.

    The payload is serialized to a plain dict so any queue backend (fake today,
    arq/redis later) receives a JSON-safe value.
    """
    return queue.enqueue(RUN_PIPELINE_JOB, payload.to_dict())


def run_pipeline_job(
    payload: JobPayload,
    *,
    progress: ProgressStore,
    orchestrator_factory: Callable[[Any], Any],
) -> Any:
    """Worker entrypoint: run one pipeline job, emitting progress transitions.

    ``orchestrator_factory`` is injected so this is testable with a STUB (no
    litellm / no real PipelineOrchestrator). It receives a run-config object and
    must return something with a ``.run()`` method. The default production wiring
    uses :func:`default_orchestrator_factory`, which imports the orchestrator
    lazily.

    Progress transitions written through the ``ProgressStore`` port:
    queued -> running -> done (or -> failed on exception, which is re-raised so
    the queue can record the failure / retry).
    """
    run_id = payload.run_id
    progress.set(run_id, "queued", 0.0, 0, "job accepted")
    progress.set(run_id, "running", 0.1, 1, "pipeline started")
    try:
        run_config = _build_run_config(payload)
        orchestrator = orchestrator_factory(run_config)
        result = orchestrator.run()
    except Exception as exc:  # noqa: BLE001 - record + re-raise for the queue
        progress.set(run_id, "failed", 1.0, 0, f"pipeline failed: {exc}")
        raise
    progress.set(run_id, "done", 1.0, 9, "pipeline complete")
    return result


def _build_run_config(payload: JobPayload) -> Any:
    """Build the run-config object handed to the orchestrator factory.

    Lazily imports ``RunConfig`` so importing this module stays dependency-light.
    If schema construction is unavailable for any reason, fall back to the raw
    ``JobPayload`` (a stub factory in tests only reads ``.run_id``).
    """
    try:
        from models.schemas import RunConfig  # local import: keep module light
    except Exception:
        return payload

    fields: dict = dict(payload.run_config)
    fields.setdefault("run_id", payload.run_id)
    if payload.sow_pdf_path:
        fields.setdefault("sow_pdf_path", payload.sow_pdf_path)
    try:
        return RunConfig(**fields)
    except Exception:
        # A partial payload (missing required RunConfig fields) is expected in
        # the seam-only phase; hand the orchestrator factory the raw payload.
        return payload


def default_orchestrator_factory(run_config: Any) -> Any:
    """Production factory: build a real ``PipelineOrchestrator``.

    Imported LAZILY here (not at module top) so ``import pipeline.jobs`` never
    drags in litellm or the orchestrator's heavy dependency graph. This is the
    factory a real worker would pass to :func:`run_pipeline_job`.
    """
    from pipeline.orchestrator import PipelineOrchestrator  # lazy: heavy deps

    return PipelineOrchestrator(run_config)
