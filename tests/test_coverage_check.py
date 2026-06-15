# tests/test_coverage_check.py
"""
Wave 2A coverage: semantic per-section coverage check.

C-5 Instructor migration: the coverage call now routes through
``runner.complete_structured(response_model=CoverageAudit)`` (Instructor-
validated) instead of ``complete_json``. The mock-point moved to
``checker.runner.complete_structured`` — returning a validated ``CoverageAudit``
or raising ``InstructorError``. Every behavioral assertion is preserved.

Six things under test:
1. Short section -> empty report, no LLM call.
2. No extracted tasks -> empty report, no LLM call (gap_recovery owns that path).
3. LLM returns two missed items -> both surface as MissedItem.
4. Low-confidence items are filtered when below the checker's min_confidence.
5. Structured output raises -> empty report, COVERAGE_CHECK_ERROR audit row, no crash.
6. Structured output unparseable -> empty report, error audit row.
"""

from __future__ import annotations

import pathlib
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from core.agent_runner import InstructorError
from models.schemas import AcceptanceCriterion, ManagedTask
from pipeline.agents.coverage_check import (
    CoverageAudit,
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


def _stub_structured(checker, *, returns=None, raises=None) -> MagicMock:
    """Stub the C-5 Instructor seam on the checker's runner.

    ``returns`` is the validated ``CoverageAudit`` the runner would hand back;
    ``raises`` simulates a structured-output failure. Returns the mock so tests
    can assert call/no-call.
    """
    mock = MagicMock()
    if raises is not None:
        mock.side_effect = raises
    else:
        mock.return_value = returns
    checker.runner.complete_structured = mock
    return mock


# ─── Tests ────────────────────────────────────────────────────────────────────

def test_short_section_returns_empty():
    """Section under 100 chars -> empty report, LLM is never called."""
    audit = MagicMock()
    checker = _make_checker(audit=audit)
    structured = _stub_structured(checker, returns=CoverageAudit())

    short_text = "Too short to audit."
    report = checker.check_section(_node(), short_text, [_managed_task()])

    assert isinstance(report, SectionCoverageReport)
    assert report.node_id == "n1"
    assert report.missed_items == []
    assert report.extracted_count == 1
    structured.assert_not_called()
    # And we logged the skip.
    actions = [call.kwargs.get("action") for call in audit.log.call_args_list]
    assert "COVERAGE_CHECK_SKIPPED" in actions


def test_no_extracted_tasks_returns_empty():
    """Zero extracted tasks -> empty report, LLM not called (gap_recovery owns this)."""
    audit = MagicMock()
    checker = _make_checker(audit=audit)
    structured = _stub_structured(checker, returns=CoverageAudit())

    long_text = "x" * 500  # >100 chars
    report = checker.check_section(_node(), long_text, [])

    assert report.missed_items == []
    assert report.extracted_count == 0
    structured.assert_not_called()
    actions = [call.kwargs.get("action") for call in audit.log.call_args_list]
    assert "COVERAGE_CHECK_SKIPPED" in actions


def test_missed_items_parsed_from_llm():
    """Structured output returns two valid misses -> both surface as MissedItem."""
    audit = MagicMock()
    checker = _make_checker(audit=audit)
    structured = _stub_structured(checker, returns=CoverageAudit(missed_items=[
        MissedItem(
            description="Daily CSV export of approved orders",
            confidence=0.85,
            reason="Section calls out export but no task covers it",
        ),
        MissedItem(
            description="Weekly PDF summary email to managers",
            confidence=0.75,
            reason="Email distribution mentioned, not represented in tasks",
        ),
    ]))

    long_text = "Reporting requirements section. " * 50
    report = checker.check_section(_node(), long_text, [_managed_task()])

    assert structured.called
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
    audit = MagicMock()
    checker = _make_checker(audit=audit, min_confidence=0.7)
    _stub_structured(checker, returns=CoverageAudit(missed_items=[
        MissedItem(description="low conf miss", confidence=0.5, reason="weak signal"),
        MissedItem(description="strong miss", confidence=0.9, reason="clearly dropped"),
    ]))

    long_text = "Section body. " * 100
    report = checker.check_section(_node(), long_text, [_managed_task()])

    assert len(report.missed_items) == 1
    assert report.missed_items[0].description == "strong miss"
    assert report.missed_items[0].confidence == 0.9


def test_llm_error_returns_empty_report_not_crash():
    """Structured output raises -> empty report + error audit row, no exception bubbles up."""
    audit = MagicMock()
    checker = _make_checker(audit=audit)
    _stub_structured(checker, raises=InstructorError("provider blew up"))

    long_text = "Real content. " * 100
    report = checker.check_section(_node(), long_text, [_managed_task()])

    assert report.missed_items == []
    assert report.extracted_count == 1
    actions = [call.kwargs.get("action") for call in audit.log.call_args_list]
    assert "COVERAGE_CHECK_ERROR" in actions
    # And we did NOT emit a successful summary row.
    assert "COVERAGE_CHECK" not in actions


def test_invalid_json_response_returns_empty_report():
    """Instructor could not produce a valid CoverageAudit -> empty report + error audit row."""
    audit = MagicMock()
    checker = _make_checker(audit=audit)
    _stub_structured(checker, raises=InstructorError("unparseable structured output"))

    long_text = "Body. " * 100
    report = checker.check_section(_node(), long_text, [_managed_task()])

    assert report.missed_items == []
    actions = [call.kwargs.get("action") for call in audit.log.call_args_list]
    assert "COVERAGE_CHECK_ERROR" in actions
    assert "COVERAGE_CHECK" not in actions
