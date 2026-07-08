# tests/test_coverage_gating.py
"""
Wave 3-F: confidence-gated coverage flagging (audit C-4, the 100% INCOMPLETE bomb).

The orchestrator must NOT flag a section's tasks INCOMPLETE just because
report.missed_items is non-empty. The checker's own confidence
(checker_confidence) has to clear min_confidence first. This pins the pure
decision helper that the orchestrator delegates to:

1. Low checker_confidence + missed_items -> NO flag.
2. High checker_confidence + missed_items -> flag.
3. High checker_confidence + no missed_items -> NO flag.

NOTE: This only gates the per-section flag on confidence. The full post-dedup,
run-wide INCOMPLETE restructure is a separate later step gated by INV-4 (the
eval cassette asserting INCOMPLETE-rate) before the runner flip.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from pipeline.agents.coverage_check import (
    MissedItem,
    SectionCoverageReport,
    should_flag_section_incomplete,
)


def _report(checker_confidence: float, missed: bool) -> SectionCoverageReport:
    items = (
        [MissedItem(description="Dropped export job", confidence=0.9, reason="not covered")]
        if missed
        else []
    )
    return SectionCoverageReport(
        node_id="n1",
        extracted_count=1,
        missed_items=items,
        checker_confidence=checker_confidence,
    )


def test_low_confidence_with_missed_items_does_not_flag():
    """Checker confidence below min_confidence -> no INCOMPLETE flag, even with misses."""
    report = _report(checker_confidence=0.4, missed=True)
    assert should_flag_section_incomplete(report, min_confidence=0.6) is False


def test_high_confidence_with_missed_items_flags():
    """Confidence at/above threshold AND genuine misses -> flag."""
    report = _report(checker_confidence=0.85, missed=True)
    assert should_flag_section_incomplete(report, min_confidence=0.6) is True


def test_high_confidence_no_missed_items_does_not_flag():
    """Confident report but nothing actually missed -> no flag."""
    report = _report(checker_confidence=0.9, missed=False)
    assert should_flag_section_incomplete(report, min_confidence=0.6) is False


def test_confidence_exactly_at_threshold_flags():
    """Boundary: checker_confidence == min_confidence counts as confident enough."""
    report = _report(checker_confidence=0.6, missed=True)
    assert should_flag_section_incomplete(report, min_confidence=0.6) is True


def test_gate_decides_via_confidence_gate(monkeypatch):
    """CONF-2: the confidence comparison routes through core.guardrails.ConfidenceGate."""
    import core.guardrails as guardrails

    calls: list[tuple[float, float]] = []
    real_admit_value = guardrails.ConfidenceGate.admit_value

    def _spy(self, value):
        calls.append((self.floor, float(value)))
        return real_admit_value(self, value)

    monkeypatch.setattr(guardrails.ConfidenceGate, "admit_value", _spy)

    report = _report(checker_confidence=0.6, missed=True)
    assert should_flag_section_incomplete(report, min_confidence=0.6) is True
    # The gate (floor==min_confidence) was consulted with the checker_confidence.
    assert (0.6, 0.6) in calls
