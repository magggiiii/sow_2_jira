# tests/test_coverage_gate.py
"""
C-4 / STEP 3.3: the run-wide, post-dedup, report-level coverage gate.

``CoverageGate`` replaces the per-section, pre-dedup blanket INCOMPLETE flagging
that produced the audit's ~100% INCOMPLETE bomb. It runs ONCE over the final
(deduped + gap-recovered) task set and reuses the SAME confidence-gate logic the
per-section ``should_flag_section_incomplete`` applied (delegated to the shared
``core.guardrails.ConfidenceGate``):

  * a coverage report flags its section only when it has genuine missed_items
    AND ``checker_confidence >= floor``;
  * a surviving task is flagged INCOMPLETE iff one of its ``source_refs``
    node_ids belongs to a flagged section — report-level, not blanket.

These tests pin that contract in isolation: no orchestrator, no LLM, no network.
"""

from __future__ import annotations

from core.guardrails import CoverageGate
from models.schemas import ManagedTask, SourceRef, TaskFlag


# ─── helpers ──────────────────────────────────────────────────────────────────


def _task(*node_ids: str, flags=None) -> ManagedTask:
    """A minimal ManagedTask sourced from one or more nodes."""
    return ManagedTask(
        title="t",
        short_description="d",
        confidence=0.9,
        flags=list(flags or []),
        source_refs=[
            SourceRef(node_id=nid, section_title=nid, page_start=1, page_end=1)
            for nid in node_ids
        ],
    )


def _report(node_id: str, *, checker_confidence: float, missed: int) -> dict:
    """A serialized SectionCoverageReport (model_dump(mode='json') shape) — the
    form the orchestrator persists in ``section_coverage_reports``."""
    return {
        "node_id": node_id,
        "extracted_count": 1,
        "checker_confidence": checker_confidence,
        "missed_items": [
            {"description": f"missed {i}", "confidence": checker_confidence,
             "reason": "dropped"}
            for i in range(missed)
        ],
    }


# ─── report-level predicate (the reused should_flag logic) ────────────────────


def test_report_flags_requires_misses_and_confidence():
    # genuine miss + confidence at/above floor -> flags
    assert CoverageGate.report_flags(missed_count=1, checker_confidence=0.6, floor=0.5) is True
    # below floor -> does not flag (mirrors the plan acceptance: conf=0.3, floor=0.5)
    assert CoverageGate.report_flags(missed_count=1, checker_confidence=0.3, floor=0.5) is False
    # no misses -> never flags, however confident
    assert CoverageGate.report_flags(missed_count=0, checker_confidence=0.99, floor=0.5) is False
    # boundary: confidence == floor is admitted (inclusive >=)
    assert CoverageGate.report_flags(missed_count=1, checker_confidence=0.5, floor=0.5) is True


# ─── apply: report-level flagging over the final task set ─────────────────────


def test_confident_report_flags_its_section_task():
    task = _task("n1")
    reports = [_report("n1", checker_confidence=0.9, missed=1)]

    result = CoverageGate(floor=0.6).apply([task], reports)

    assert TaskFlag.INCOMPLETE in task.flags
    assert result.flagged_task_count == 1
    assert result.total_task_count == 1
    assert result.incomplete_rate == 1.0
    assert result.flagged_node_ids == {"n1"}


def test_low_confidence_report_does_not_flag():
    task = _task("n1")
    reports = [_report("n1", checker_confidence=0.3, missed=1)]

    result = CoverageGate(floor=0.6).apply([task], reports)

    assert TaskFlag.INCOMPLETE not in task.flags
    assert result.flagged_task_count == 0
    assert result.incomplete_rate == 0.0


def test_report_without_missed_items_does_not_flag():
    task = _task("n1")
    reports = [_report("n1", checker_confidence=0.95, missed=0)]

    result = CoverageGate(floor=0.6).apply([task], reports)

    assert TaskFlag.INCOMPLETE not in task.flags
    assert result.flagged_task_count == 0


def test_only_flagged_sections_tasks_are_flagged_rate_drops():
    """The C-4 fix: a confident miss in ONE section flags only that section's
    tasks — not the whole corpus. A naive blanket would flag both (rate 1.0);
    the report-level gate flags one (rate 0.5)."""
    task_a = _task("n1")          # section with a confident miss
    task_b = _task("n2")          # clean section
    reports = [
        _report("n1", checker_confidence=0.9, missed=1),
        _report("n2", checker_confidence=0.0, missed=0),
    ]

    result = CoverageGate(floor=0.6).apply([task_a, task_b], reports)

    assert TaskFlag.INCOMPLETE in task_a.flags
    assert TaskFlag.INCOMPLETE not in task_b.flags
    assert result.flagged_task_count == 1
    assert result.total_task_count == 2
    assert result.incomplete_rate == 0.5


def test_task_spanning_flagged_and_clean_nodes_is_flagged():
    """A merged survivor carries source_refs from several nodes; if ANY of them
    is a flagged section the survivor is INCOMPLETE."""
    task = _task("n1", "n2")      # n1 clean, n2 confident miss
    reports = [
        _report("n1", checker_confidence=0.0, missed=0),
        _report("n2", checker_confidence=0.9, missed=1),
    ]

    CoverageGate(floor=0.6).apply([task], reports)

    assert TaskFlag.INCOMPLETE in task.flags


def test_apply_is_idempotent_no_double_flag():
    task = _task("n1", flags=[TaskFlag.INCOMPLETE])
    reports = [_report("n1", checker_confidence=0.9, missed=1)]

    CoverageGate(floor=0.6).apply([task], reports)

    assert task.flags.count(TaskFlag.INCOMPLETE) == 1


def test_apply_accepts_reports_mapping():
    """The orchestrator holds reports as a {node_id: report} dict; apply must
    consume the mapping form too."""
    task = _task("n1")
    reports = {"n1": _report("n1", checker_confidence=0.9, missed=1)}

    result = CoverageGate(floor=0.6).apply([task], reports)

    assert TaskFlag.INCOMPLETE in task.flags
    assert result.flagged_task_count == 1


def test_no_reports_is_a_noop():
    task = _task("n1")
    result = CoverageGate(floor=0.6).apply([task], [])
    assert TaskFlag.INCOMPLETE not in task.flags
    assert result.flagged_task_count == 0
    assert result.total_task_count == 1
    assert result.incomplete_rate == 0.0
