# core/pipeline/runner.py
"""
``PipelineRunner`` — runs a :class:`~core.pipeline.registry.StageRegistry`'s
stages in order against one :class:`~core.pipeline.context.PipelineContext`.

This is the staged equivalent of ``PipelineOrchestrator.run()``'s linear body:
each stage advances the shared context; if a stage sets ``ctx.cancelled`` (today
only the extraction stage does, when the run is cancelled mid-loop) the runner
stops before the next stage and returns the context as-is — mirroring run()'s
early ``return all_closed_tasks``. The runner itself is orchestrator-agnostic;
all pipeline-specific work lives in the stages.
"""

from __future__ import annotations

from core.pipeline.context import PipelineContext
from core.pipeline.registry import StageRegistry


class PipelineRunner:
    """Execute a registry's stages in order, honoring ``ctx.cancelled``."""

    def __init__(self, registry: StageRegistry) -> None:
        self.registry = registry

    def run(self, ctx: PipelineContext) -> PipelineContext:
        for stage in self.registry:
            if ctx.cancelled:
                break
            stage.run(ctx)
        return ctx


__all__ = ["PipelineRunner"]
