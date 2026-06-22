# pipeline/stages.py
"""
STEP 3.7: the concrete extraction-pipeline stages and the default registry.

Each stage is a thin wrapper that calls one of ``PipelineOrchestrator``'s
``_stage_*`` phase methods (which hold the actual run() logic, threaded through a
:class:`~core.pipeline.context.PipelineContext`). Keeping the stages as method
delegations — rather than re-implementing the phases here — means run() and the
staged ``run_via_pipeline`` share a single source of truth for each phase; the
runner only owns the *sequencing*.

This module imports only the orchestrator-agnostic ``core.pipeline`` substrate
(never ``pipeline.orchestrator``), so it carries no import cycle; the orchestrator
imports ``build_default_registry`` lazily inside ``run_via_pipeline``.
"""

from __future__ import annotations

from core.pipeline.context import PipelineContext
from core.pipeline.registry import StageRegistry
from core.pipeline.stage import Stage


class MethodStage(Stage):
    """A stage that delegates to a named ``_stage_*`` method on ``ctx.orch``."""

    def __init__(self, name: str, method_name: str):
        self.name = name
        self._method_name = method_name

    def run(self, ctx: PipelineContext) -> None:
        getattr(ctx.orch, self._method_name)(ctx)


# The PEV order — identical to run()'s linear sequence. Coverage is already a
# post-dedup gate (the C-4 restructure), so this order already satisfies PEV;
# the runner halts after `extract` if that stage sets ctx.cancelled.
DEFAULT_STAGES: list[tuple[str, str]] = [
    ("setup", "_stage_setup"),
    ("index", "_stage_index"),
    ("extract", "_stage_extract"),
    ("dedup", "_stage_dedup"),
    ("gap_recovery", "_stage_gap_recovery"),
    ("coverage_gate", "_stage_coverage_gate"),
    ("health", "_stage_health"),
    ("save", "_stage_save"),
]


def build_default_registry() -> StageRegistry:
    """The default extraction pipeline: run()'s phases in run()'s order."""
    reg = StageRegistry()
    for name, method_name in DEFAULT_STAGES:
        reg.register(MethodStage(name, method_name))
    return reg


__all__ = ["MethodStage", "build_default_registry", "DEFAULT_STAGES"]
