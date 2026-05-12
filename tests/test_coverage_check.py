# tests/test_coverage_check.py
"""
Wave 2A coverage: semantic per-section coverage check.

Six things under test:
1. Short section -> empty report, no LLM call.
2. No extracted tasks -> empty report, no LLM call (gap_recovery owns that path).
3. LLM returns two missed items -> both parse into MissedItem and surface.
4. Low-confidence items are filtered when below the checker's min_confidence.
5. LLM raises -> empty report, COVERAGE_CHECK_ERROR audit row, no crash.
6. LLM returns non-list JSON -> empty report, error audit row.
"""

from __future__ import annotations

import pathlib
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from models.schemas import AcceptanceCriterion, ManagedTask
from pipeline.agents.coverage_check import (
    CoverageChecker,
    MissedItem,
    SectionCoverageReport,
)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _node(node_id: str = "n1", title: str = "Reporting Module") -> dict:
    return {
        "node_id": node_id,
        "title": title,
        "page_start": 4,
        "page_end": 6,
        "parent_id": None,
        "parent_chain": [],
        "depth": 0,
    }


def _managed_task(title: str = "Build report exporter", conditions: list[str] | None = None) -> ManagedTask:
    acs = [AcceptanceCriterion(condition=c) for c in (conditions or [])]
    return ManagedTask(
        title=title,
        short_description="d",
        confidence=0.9,
        acceptance_criteria=acs or None,
    )


def _make_checker(llm: MagicMock | None = None, audit: MagicMock | None = None, min_confidence: float = 0.6):
    return CoverageChecker(
        llm_client=llm or MagicMock(),
        audit_logger=audit or MagicMock(),
        run_id="r1",
        min_confidence=min_confidence,
    )


# ─── Tests ────────────────────────────────────────────────────────────────────

def test_short_section_returns_empty():
    """Section under 100 chars -> empty report, LLM is never called."""
    llm = MagicMock()
    audit = MagicMock()
    checker = _make_checker(llm=llm, audit=audit)

    short_text = "Too short to audit."
    report = checker.check_section(_node(), short_text, [_managed_task()])

    assert isinstance(report, SectionCoverageReport)
    assert report.node_id == "n1"
    assert report.missed_items == []
    assert report.extracted_count == 1
    llm.complete_json.assert_not_called()
    # And we logged the skip.
    actions = [call.kwargs.get("action") for call in audit.log.call_args_list]
    assert "COVERAGE_CHECK_SKIPPED" in actions


def test_no_extracted_tasks_returns_empty():
    """Zero extracted tasks -> empty report, LLM not called (gap_recovery owns this)."""
    llm = MagicMock()
    audit = MagicMock()
    checker = _make_checker(llm=llm, audit=audit)

    long_text = "x" * 500  # >100 chars
    report = checker.check_section(_node(), long_text, [])

    assert report.missed_items == []
    assert report.extracted_count == 0
    llm.complete_json.assert_not_called()
    actions = [call.kwargs.get("action") for call in audit.log.call_args_list]
    assert "COVERAGE_CHECK_SKIPPED" in actions


def test_missed_items_parsed_from_llm():
    """LLM returns two valid misses -> both surface in the report as MissedItem."""
    llm = MagicMock()
    llm.complete_json.return_value = [
        {
            "description": "Daily CSV export of approved orders",
            "confidence": 0.85,
            "reason": "Section calls out export but no task covers it",
        },
        {
            "description": "Weekly PDF summary email to managers",
            "confidence": 0.75,
            "reason": "Email distribution mentioned, not represented in tasks",
        },
    ]
    audit = MagicMock()
    checker = _make_checker(llm=llm, audit=audit)

    long_text = "Reporting requirements section. " * 50
    report = checker.check_section(_node(), long_text, [_managed_task()])

    assert llm.complete_json.called
    assert report.extracted_count == 1
    assert len(report.missed_items) == 2
    assert all(isinstance(m, MissedItem) for m in report.missed_items)
    assert report.missed_items[0].description.startswith("Daily CSV")
    assert report.checker_confidence == pytest.approx((0.85 + 0.75) / 2, rel=1e-3)
    # Audit trail: one summary row + one per miss.
    actions = [call.kwargs.get("action") for call in audit.log.call_args_list]
    assert "COVERAGE_CHECK" in actions
    assert actions.count("COVERAGE_MISS") == 2


def test_low_confidence_items_filtered_when_below_threshold():
    """min_confidence=0.7 with items at 0.5 and 0.9 -> only the 0.9 survives."""
    llm = MagicMock()
    llm.complete_json.return_value = [
        {"description": "low conf miss", "confidence": 0.5, "reason": "weak signal"},
        {"description": "strong miss", "confidence": 0.9, "reason": "clearly dropped"},
    ]
    audit = MagicMock()
    checker = _make_checker(llm=llm, audit=audit, min_confidence=0.7)

    long_text = "Section body. " * 100
    report = checker.check_section(_node(), long_text, [_managed_task()])

    assert len(report.missed_items) == 1
    assert report.missed_items[0].description == "strong miss"
    assert report.missed_items[0].confidence == 0.9


def test_llm_error_returns_empty_report_not_crash():
    """LLM raises -> empty report and an error audit row, no exception bubbles up."""
    llm = MagicMock()
    llm.complete_json.side_effect = RuntimeError("provider blew up")
    audit = MagicMock()
    checker = _make_checker(llm=llm, audit=audit)

    long_text = "Real content. " * 100
    report = checker.check_section(_node(), long_text, [_managed_task()])

    assert report.missed_items == []
    assert report.extracted_count == 1
    actions = [call.kwargs.get("action") for call in audit.log.call_args_list]
    assert "COVERAGE_CHECK_ERROR" in actions
    # And we did NOT emit a successful summary row.
    assert "COVERAGE_CHECK" not in actions


def test_invalid_json_response_returns_empty_report():
    """LLM returns a non-list (dict) -> empty report and an error audit row."""
    llm = MagicMock()
    llm.complete_json.return_value = {"oops": "not a list"}
    audit = MagicMock()
    checker = _make_checker(llm=llm, audit=audit)

    long_text = "Body. " * 100
    report = checker.check_section(_node(), long_text, [_managed_task()])

    assert report.missed_items == []
    actions = [call.kwargs.get("action") for call in audit.log.call_args_list]
    assert "COVERAGE_CHECK_ERROR" in actions
    assert "COVERAGE_CHECK" not in actions
