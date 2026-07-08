# tests/test_eval_harness.py
"""
INV-4 gate (test-12) + negative controls.

Drives the real PipelineOrchestrator.run() fully offline via the EvalHarness and
asserts the bands. The gate's whole value is that it can FAIL: each negative
control reintroduces one of the three Wave-3 bug fingerprints and asserts the
corresponding band goes RED. A gate that can't fail is worthless.
"""

from __future__ import annotations

from pipeline.evals.harness import EvalHarness


def _band(result, name):
    return next(b for b in result.bands if b.name == name)


# ─── the gate: a healthy run clears every band ────────────────────────────────


def test_healthy_run_passes_all_bands():
    result = EvalHarness(run_id="inv4-healthy").run()
    assert result.passed is True, result.summary()
    assert result.metrics["merge_count"] >= 1
    assert result.metrics["incomplete_rate"] <= 0.40
    assert result.metrics["zero_conf_flags"] == 0
    assert result.metrics["degraded"] is False


# ─── negative controls: the gate catches each bug class ───────────────────────


def test_zero_merge_regression_fails_merge_band():
    result = EvalHarness(run_id="inv4-nomerge", dedup_merges=False).run()
    assert result.passed is False
    assert _band(result, "merge_count").passed is False
    assert result.metrics["merge_count"] == 0


def test_incomplete_flagbomb_fails_incomplete_band():
    # Coverage reports a high-confidence miss on every node → every task flagged
    # INCOMPLETE → rate ~100% → the C-4 100%-INCOMPLETE bomb.
    result = EvalHarness(run_id="inv4-flagbomb", coverage_missed=True).run()
    assert result.passed is False
    assert _band(result, "incomplete_rate").passed is False
    assert result.metrics["incomplete_rate"] > 0.40


def test_conf_zero_blanket_flag_fails_zero_conf_band():
    # Extraction emits conf=0.0 tasks → the agent auto-adds LOW_CONFIDENCE →
    # the critic conf=0.00 mass-flag fingerprint.
    result = EvalHarness(run_id="inv4-confzero", extraction_conf=0.0).run()
    assert result.passed is False
    assert _band(result, "zero_conf_flags").passed is False
    assert result.metrics["zero_conf_flags"] >= 1
