# tests/test_eval_bands.py
"""
INV-4 (band-math): the quality bands the eval gate asserts on a completed run's
pipeline_output.json. The bands (IMPLEMENTATION-PLAN.md:1291) are:

    incomplete_rate <= 0.40   AND   merge_count >= 1   AND   zero_conf_flags == 0
    AND  run health is not DEGRADED

These are pure functions over the saved checkpoint dict — no pipeline, no LLM —
so the band math is provable independent of any run. Notes that drove the design:
- merge_count is sum(len(task.merged_from)) over the SURVIVING tasks, NOT a count
  of status==MERGED: dedup DROPS merged tasks from the final list, so a MERGED
  status never appears in pipeline_output['tasks'].
- zero_conf_flags counts the critic conf=0.00 mass-flag bug specifically: a task
  flagged LOW_CONFIDENCE whose confidence is exactly 0.0 — not a bare conf==0.
"""

from __future__ import annotations

import pytest

from pipeline.evals.bands import EvalBands, compute_bands, compute_metrics


def _task(flags=None, confidence=0.8, merged_from=None, status="CLOSED"):
    return {
        "id": "00000000-0000-0000-0000-000000000000",
        "title": "t",
        "short_description": "d",
        "confidence": confidence,
        "flags": list(flags or []),
        "merged_from": list(merged_from or []),
        "status": status,
    }


def _output(tasks, *, overall_status="OK", is_degraded=False):
    return {
        "run_id": "r",
        "tasks": tasks,
        "coverage_report": {"coverage_pct": 100},
        "health": {"overall_status": overall_status, "is_degraded": is_degraded},
    }


# ─── metrics ──────────────────────────────────────────────────────────────────


def test_compute_metrics_reads_each_dimension():
    out = _output([
        _task(flags=["INCOMPLETE"]),
        _task(merged_from=["a", "b"]),
        _task(flags=["LOW_CONFIDENCE"], confidence=0.0),
        _task(),
    ])
    m = compute_metrics(out)
    assert m["total_tasks"] == 4
    assert m["incomplete_rate"] == pytest.approx(0.25)     # 1 of 4
    assert m["merge_count"] == 2                            # sum of merged_from lengths
    assert m["zero_conf_flags"] == 1                        # the conf=0.0 + LOW_CONFIDENCE task
    assert m["degraded"] is False


def test_merge_count_is_sum_of_merged_from_not_status():
    # A surviving task that absorbed two others. No task carries status==MERGED
    # (those are dropped), so merged_from is the only self-contained signal.
    out = _output([_task(merged_from=["x", "y", "z"])])
    assert compute_metrics(out)["merge_count"] == 3


def test_zero_conf_flag_requires_both_zero_conf_and_low_confidence():
    # conf=0.0 but NOT flagged → not a violation; flagged but conf>0 → not a violation.
    out = _output([
        _task(confidence=0.0, flags=[]),                    # clamped, unflagged: ok
        _task(confidence=0.5, flags=["LOW_CONFIDENCE"]),    # genuinely low: ok
    ])
    assert compute_metrics(out)["zero_conf_flags"] == 0


# ─── band assertions ────────────────────────────────────────────────────────


def _healthy():
    # 1/5 INCOMPLETE (0.20), one real merge, no conf=0.0 flags, health OK.
    return _output([
        _task(flags=["INCOMPLETE"]),
        _task(merged_from=["absorbed-1"]),
        _task(), _task(), _task(),
    ])


def test_healthy_run_passes_all_bands():
    result = compute_bands(_healthy())
    assert result.passed is True
    assert all(b.passed for b in result.bands)


def test_incomplete_rate_at_threshold_passes():
    out = _output([_task(flags=["INCOMPLETE"]), _task(flags=["INCOMPLETE"]),
                   _task(merged_from=["m"]), _task(), _task()])  # 2/5 = 0.40 exactly
    result = compute_bands(out)
    assert result.metrics["incomplete_rate"] == pytest.approx(0.40)
    assert next(b for b in result.bands if b.name == "incomplete_rate").passed is True


def test_incomplete_rate_over_threshold_fails():
    out = _output([_task(flags=["INCOMPLETE"]), _task(flags=["INCOMPLETE"]),
                   _task(flags=["INCOMPLETE"]), _task(merged_from=["m"]), _task()])  # 3/5 = 0.60
    result = compute_bands(out)
    assert result.passed is False
    assert next(b for b in result.bands if b.name == "incomplete_rate").passed is False


def test_zero_merges_fails():
    out = _output([_task(), _task(), _task()])  # no merged_from anywhere
    result = compute_bands(out)
    assert result.passed is False
    assert next(b for b in result.bands if b.name == "merge_count").passed is False


def test_conf_zero_blanket_flag_fails():
    out = _output([_task(merged_from=["m"]),
                   _task(confidence=0.0, flags=["LOW_CONFIDENCE"])])
    result = compute_bands(out)
    assert result.passed is False
    assert next(b for b in result.bands if b.name == "zero_conf_flags").passed is False


def test_degraded_health_fails():
    out = _output([_task(flags=["INCOMPLETE"]), _task(merged_from=["m"]), _task()],
                  overall_status="DEGRADED", is_degraded=True)
    result = compute_bands(out)
    assert result.passed is False
    assert next(b for b in result.bands if b.name == "not_degraded").passed is False


def test_custom_bands_override_thresholds():
    out = _output([_task(flags=["INCOMPLETE"]), _task(merged_from=["m"])])  # 0.50 incomplete
    strict = EvalBands(incomplete_rate_max=0.30)
    assert compute_bands(out, bands=strict).passed is False
    lenient = EvalBands(incomplete_rate_max=0.60)
    assert compute_bands(out, bands=lenient).passed is True
