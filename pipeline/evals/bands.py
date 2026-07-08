# pipeline/evals/bands.py
"""
INV-4 quality bands — the pass/fail gate computed over a completed run's
``data/sessions/<run_id>/pipeline_output.json`` checkpoint.

The bands (IMPLEMENTATION-PLAN.md:1291) prevent the Wave-3 intelligence-layer
bugs from shipping behind green CI:

    incomplete_rate <= 0.40   AND   merge_count >= 1   AND   zero_conf_flags == 0
    AND  the run's health is not DEGRADED

This module is pure and dependency-light: it reads the saved checkpoint dict
(tasks are ``model_dump(mode="json")`` shapes — flags as strings, confidence as
float, merged_from as a list) and returns a typed :class:`EvalResult`. It does
NOT run a pipeline, so the band math is unit-testable in isolation and can be
pointed at any real run's output later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# The three bug fingerprints the bands target:
_INCOMPLETE_FLAG = "INCOMPLETE"
_LOW_CONFIDENCE_FLAG = "LOW_CONFIDENCE"


@dataclass(frozen=True)
class EvalBands:
    """The acceptance thresholds — the asserted INV-4 contract.

    Defaults are the plan's bands; overridable so a stricter/looser gate (or a
    future bands.yaml loader) can supply its own."""

    incomplete_rate_max: float = 0.40
    merge_count_min: int = 1
    zero_conf_flags_max: int = 0
    allow_degraded: bool = False


@dataclass
class BandResult:
    name: str
    value: Any
    threshold: Any
    passed: bool


@dataclass
class EvalResult:
    passed: bool
    bands: list[BandResult] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    def summary(self) -> str:
        verdict = "PASS" if self.passed else "FAIL"
        lines = [f"INV-4 bands: {verdict}"]
        for b in self.bands:
            mark = "✓" if b.passed else "✗"
            lines.append(f"  {mark} {b.name}: {b.value} (threshold {b.threshold})")
        return "\n".join(lines)


def compute_metrics(pipeline_output: dict) -> dict:
    """
    Distill the raw INV-4 metrics from a saved ``pipeline_output.json`` dict.

    - ``incomplete_rate`` = fraction of final tasks flagged INCOMPLETE.
    - ``merge_count`` = sum of ``len(merged_from)`` over surviving tasks. (Merged
      tasks are DROPPED from the final list, so a status==MERGED count is always
      0 here — ``merged_from`` on the survivors is the self-contained signal.)
    - ``zero_conf_flags`` = tasks flagged LOW_CONFIDENCE whose confidence is
      exactly 0.0 (the critic conf=0.00 blanket-flag bug — NOT a bare conf==0).
    - ``degraded`` = the run's health verdict is DEGRADED.
    """
    tasks = pipeline_output.get("tasks") or []
    total = len(tasks)

    incomplete = sum(1 for t in tasks if _INCOMPLETE_FLAG in (t.get("flags") or []))
    incomplete_rate = (incomplete / total) if total else 0.0

    merge_count = sum(len(t.get("merged_from") or []) for t in tasks)

    zero_conf_flags = sum(
        1
        for t in tasks
        if _LOW_CONFIDENCE_FLAG in (t.get("flags") or [])
        and float(t.get("confidence", 0.0) or 0.0) == 0.0
    )

    health = pipeline_output.get("health") or {}
    degraded = bool(health.get("is_degraded")) or (
        str(health.get("overall_status", "")).upper() == "DEGRADED"
    )

    return {
        "total_tasks": total,
        "incomplete_count": incomplete,
        "incomplete_rate": incomplete_rate,
        "merge_count": merge_count,
        "zero_conf_flags": zero_conf_flags,
        "degraded": degraded,
    }


def compute_bands(pipeline_output: dict, bands: EvalBands | None = None) -> EvalResult:
    """Evaluate ``pipeline_output`` against ``bands`` (defaults to the INV-4
    contract). ``EvalResult.passed`` is True only when every band passes."""
    bands = bands or EvalBands()
    m = compute_metrics(pipeline_output)

    results = [
        BandResult(
            "incomplete_rate", round(m["incomplete_rate"], 4),
            f"<= {bands.incomplete_rate_max}",
            m["incomplete_rate"] <= bands.incomplete_rate_max,
        ),
        BandResult(
            "merge_count", m["merge_count"], f">= {bands.merge_count_min}",
            m["merge_count"] >= bands.merge_count_min,
        ),
        BandResult(
            "zero_conf_flags", m["zero_conf_flags"], f"<= {bands.zero_conf_flags_max}",
            m["zero_conf_flags"] <= bands.zero_conf_flags_max,
        ),
        BandResult(
            "not_degraded", m["degraded"], f"degraded allowed: {bands.allow_degraded}",
            bands.allow_degraded or not m["degraded"],
        ),
    ]

    return EvalResult(passed=all(b.passed for b in results), bands=results, metrics=m)


__all__ = ["EvalBands", "BandResult", "EvalResult", "compute_metrics", "compute_bands"]
