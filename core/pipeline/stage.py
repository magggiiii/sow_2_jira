# core/pipeline/stage.py
"""
``Stage`` — one named phase of the pipeline.

A stage does a single unit of run() work against the shared
:class:`~core.pipeline.context.PipelineContext`: it reads the state it needs,
calls back into ``ctx.orch`` (the orchestrator) for the heavy lifting, and writes
its results onto ``ctx``. Stages return nothing; their effect is the mutation of
``ctx``. A stage that determines the run should stop early sets
``ctx.cancelled = True`` and the runner halts before the next stage.

``name`` is a stable identifier used by the :class:`~core.pipeline.registry.StageRegistry`
for ordering/lookup and by progress/telemetry.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from core.pipeline.context import PipelineContext


class Stage(ABC):
    """Abstract base for a single pipeline phase."""

    #: Stable stage identifier (subclasses set this).
    name: str = "stage"

    @abstractmethod
    def run(self, ctx: PipelineContext) -> None:
        """Advance the run by one phase, mutating ``ctx`` in place."""
        raise NotImplementedError


__all__ = ["Stage"]
