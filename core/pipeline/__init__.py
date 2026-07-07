# core/pipeline/ — the staged pipeline substrate (STEP 3.7).
#
# Orchestrator-AGNOSTIC abstractions for running the extraction pipeline as an
# ordered sequence of stages:
#   * PipelineContext (context.py) — the mutable run-state carrier.
#   * Stage (stage.py)             — one named pipeline phase, run(ctx).
#   * StageRegistry (registry.py)  — an ordered collection of stages.
#   * PipelineRunner (runner.py)   — runs the registry in order, honoring cancel.
#
# The concrete stages that wrap PipelineOrchestrator's phase-methods live in
# pipeline/stages.py (they may import the orchestrator); this package must not,
# so it stays a dependency-light, reusable core.

from core.pipeline.context import PipelineContext
from core.pipeline.registry import StageRegistry
from core.pipeline.runner import PipelineRunner
from core.pipeline.stage import Stage

__all__ = ["PipelineContext", "Stage", "StageRegistry", "PipelineRunner"]
