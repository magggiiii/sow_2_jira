# tests/test_pipeline_runner.py
"""
STEP 3.7: the PEV PipelineRunner substrate (core/pipeline/).

These cover the orchestrator-AGNOSTIC abstractions with fakes — no orchestrator,
no LLM, no network:
  * PipelineContext — the mutable run-state carrier threaded through stages.
  * Stage — a named pipeline phase with run(ctx).
  * StageRegistry — an ordered collection of stages.
  * PipelineRunner — runs the registry's stages in order, threading one ctx,
    and stops as soon as a stage sets ctx.cancelled (mirroring run()'s early
    return on a cancelled extraction).

The end-to-end equivalence with orchestrator.run() is proved separately in
tests/test_pipeline_runner_equivalence.py against the synthetic eval cassette.
"""

from __future__ import annotations

from core.pipeline.context import PipelineContext
from core.pipeline.registry import StageRegistry
from core.pipeline.runner import PipelineRunner
from core.pipeline.stage import Stage

# ─── Fakes ──────────────────────────────────────────────────────────────────


class _RecordingStage(Stage):
    def __init__(self, name, log, *, cancel=False):
        self.name = name
        self._log = log
        self._cancel = cancel

    def run(self, ctx):
        self._log.append(self.name)
        if self._cancel:
            ctx.cancelled = True


# ─── PipelineContext ────────────────────────────────────────────────────────


def test_context_defaults():
    ctx = PipelineContext(orch=object())
    assert ctx.nodes == []
    assert ctx.all_closed_tasks == []
    assert ctx.open_tasks == []
    assert ctx.deduplicated == []
    assert ctx.report == {}
    assert ctx.cancelled is False


def test_context_result_prefers_deduplicated_else_closed():
    ctx = PipelineContext(orch=object())
    ctx.all_closed_tasks = ["a", "b"]
    # Before dedup, the result is the closed set (the cancelled-mid-run case).
    assert ctx.result() == ["a", "b"]
    ctx.deduplicated = ["merged"]
    # After dedup, the deduplicated set is the run's output.
    assert ctx.result() == ["merged"]


# ─── StageRegistry ──────────────────────────────────────────────────────────


def test_registry_preserves_registration_order():
    reg = StageRegistry()
    log = []
    reg.register(_RecordingStage("one", log))
    reg.register(_RecordingStage("two", log))
    reg.register(_RecordingStage("three", log))
    assert reg.names() == ["one", "two", "three"]
    assert [s.name for s in reg] == ["one", "two", "three"]


# ─── PipelineRunner ─────────────────────────────────────────────────────────


def test_runner_runs_stages_in_order():
    log = []
    reg = StageRegistry()
    for n in ("a", "b", "c"):
        reg.register(_RecordingStage(n, log))
    ctx = PipelineContext(orch=object())

    out = PipelineRunner(reg).run(ctx)

    assert log == ["a", "b", "c"]
    assert out is ctx  # same context threaded through


def test_runner_stops_after_stage_sets_cancelled():
    log = []
    reg = StageRegistry()
    reg.register(_RecordingStage("extract", log, cancel=True))
    reg.register(_RecordingStage("dedup", log))
    reg.register(_RecordingStage("save", log))
    ctx = PipelineContext(orch=object())

    PipelineRunner(reg).run(ctx)

    # Only the cancelling stage ran; the rest were skipped (run()'s early return).
    assert log == ["extract"]
    assert ctx.cancelled is True
