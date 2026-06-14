# tests/test_confidence_bounds.py
"""
CONF-1 — confidence/score fields are CLAMPED into the unit interval [0, 1].

Audit data_model finding "bound confidence": LLM output can emit a confidence
of 1.5 or -0.3. Rather than reject (which would drop an otherwise-usable
record) or store an out-of-range value (which poisons every downstream
`>= floor` comparison and `:.2f` render), every confidence/score field coerces
into [0, 1]: values > 1 -> 1.0, values < 0 -> 0.0, in-range values unchanged.

This mirrors the enum philosophy (`_NormalizedStrEnum`) of absorbing
dirty-but-numeric LLM output instead of failing loudly. Genuinely non-numeric
junk is still left to Pydantic's normal coercion/rejection.
"""

from __future__ import annotations

import pytest

from models.schemas import ManagedTask, RawTask, clamp_unit_interval


# ─── The shared helper ────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "raw,expected",
    [
        (1.5, 1.0),       # above range -> top
        (2.0, 1.0),
        (1.0, 1.0),       # boundary unchanged
        (0.42, 0.42),     # in range unchanged
        (0.0, 0.0),       # boundary unchanged
        (-0.3, 0.0),      # below range -> bottom
        (-5.0, 0.0),
        (1, 1.0),         # int coerces
        (0, 0.0),
    ],
)
def test_clamp_unit_interval(raw, expected):
    assert clamp_unit_interval(raw) == pytest.approx(expected)


def test_clamp_passes_through_none():
    # Optional confidence fields stay None; only numeric values are clamped.
    assert clamp_unit_interval(None) is None


# ─── models/schemas.py fields ─────────────────────────────────────────────────

def test_managed_task_confidence_clamps_high():
    assert ManagedTask(title="t", short_description="d", confidence=1.5).confidence == 1.0


def test_managed_task_confidence_clamps_low():
    assert ManagedTask(title="t", short_description="d", confidence=-0.3).confidence == 0.0


def test_managed_task_confidence_in_range_unchanged():
    assert ManagedTask(title="t", short_description="d", confidence=0.42).confidence == pytest.approx(0.42)


def test_managed_task_confidence_zero_still_valid():
    assert ManagedTask(title="t", short_description="d", confidence=0.0).confidence == 0.0


def test_raw_task_confidence_clamps_high_instead_of_rejecting():
    # Previously RawTask.confidence carried Field(ge=0, le=1) and REJECTED 1.5.
    # Now it coerces to 1.0 (absorb dirty-but-numeric LLM output).
    assert RawTask(title="t", short_description="d", confidence=1.5).confidence == 1.0


def test_raw_task_confidence_clamps_low():
    assert RawTask(title="t", short_description="d", confidence=-0.3).confidence == 0.0


def test_raw_task_confidence_in_range_unchanged():
    assert RawTask(title="t", short_description="d", confidence=0.42).confidence == pytest.approx(0.42)


# ─── agent response models ────────────────────────────────────────────────────

def test_task_critique_confidence_clamps():
    from uuid import uuid4
    from pipeline.agents.critic import TaskCritique

    tid = uuid4()
    assert TaskCritique(task_id=tid, confidence=1.5).confidence == 1.0
    assert TaskCritique(task_id=tid, confidence=-0.3).confidence == 0.0
    assert TaskCritique(task_id=tid, confidence=0.42).confidence == pytest.approx(0.42)


def test_missed_item_confidence_clamps_instead_of_rejecting():
    from pipeline.agents.coverage_check import MissedItem

    assert MissedItem(description="x", confidence=1.5, reason="r").confidence == 1.0
    assert MissedItem(description="x", confidence=-0.3, reason="r").confidence == 0.0
    assert MissedItem(description="x", confidence=0.42, reason="r").confidence == pytest.approx(0.42)


def test_section_coverage_report_checker_confidence_clamps():
    from pipeline.agents.coverage_check import SectionCoverageReport

    rep = SectionCoverageReport(node_id="n1", extracted_count=0, checker_confidence=1.5)
    assert rep.checker_confidence == 1.0
    rep2 = SectionCoverageReport(node_id="n1", extracted_count=0, checker_confidence=-0.3)
    assert rep2.checker_confidence == 0.0


def test_classification_result_confidence_clamps_instead_of_rejecting():
    from pipeline.agents.classifier import ClassificationResult, SectionType

    res = ClassificationResult(node_id="n1", type=SectionType.ACTIONABLE, confidence=1.5, reason="r")
    assert res.confidence == 1.0
    res2 = ClassificationResult(node_id="n1", type=SectionType.CONTEXT, confidence=-0.3, reason="r")
    assert res2.confidence == 0.0
