# tests/test_extraction.py
"""
Wave 2B coverage: ExtractionAgent produces RawTasks from the Instructor-
validated ExtractionResult ({scratchpad, tasks}). Also confirms the existing
LOW_CONFIDENCE auto-flag still fires at threshold after the prompt change.

C-5 Instructor migration: extraction's single LLM call now routes through
``runner.complete_structured(response_model=ExtractionResult)`` instead of
``complete_json``. The mock-point moved to ``agent.runner.complete_structured`` —
returning a validated ``ExtractionResult`` or raising ``InstructorError``. The
provider-side shape juggling (bare array vs wrapper, non-list ``tasks``,
unsupported types) is now Instructor's job: a result that can't be validated
surfaces as InstructorError. Every behavioral assertion is preserved.
"""

from __future__ import annotations

import pathlib
import sys
from unittest.mock import MagicMock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from core.agent_runner import InstructorError
from models.schemas import AcceptanceCriterion, TaskFlag
from pipeline.agents.extraction import ExtractionResult, TaskExtractionAgent


class _DummyAudit:
    def __init__(self):
        self.records = []

    def log(self, **kwargs):
        self.records.append(kwargs)


def _node(node_id: str = "n1", title: str = "Section A") -> dict:
    return {
        "node_id": node_id,
        "title": title,
        "page_start": 1,
        "page_end": 2,
    }


def _section_text() -> str:
    # >= 50 chars so the short-section gate doesn't short-circuit.
    return (
        "The platform must allow users to register, log in, and reset "
        "their passwords via an emailed link. " * 3
    )


def _raw_task(title: str = "Implement login API", confidence: float = 0.9) -> dict:
    return {
        "title": title,
        "short_description": "Build the auth endpoint.",
        "acceptance_criteria": [
            {"condition": "POST /auth/login returns 200 on valid credentials"}
        ],
        "use_case": None,
        "considerations_constraints": None,
        "deliverables": None,
        "mockup_prototype": None,
        "confidence": confidence,
        "flags": [],
        "continues_to_next": False,
        "dependencies": [],
    }


def _stub_structured(agent, *, returns=None, raises=None) -> MagicMock:
    """Stub the C-5 Instructor seam on the agent's runner.

    ``returns`` is the validated ``ExtractionResult`` the runner would hand back;
    ``raises`` simulates a structured-output failure. Returns the mock so tests
    can assert call/no-call.
    """
    mock = MagicMock()
    if raises is not None:
        mock.side_effect = raises
    else:
        mock.return_value = returns
    agent.runner.complete_structured = mock
    return mock


# ─── New wrapper shape ──────────────────────────────────────────────────────

def test_extraction_parses_scratchpad_wrapper():
    """Validated ExtractionResult {scratchpad, tasks} — tasks parse, scratchpad audited."""
    audit = _DummyAudit()
    agent = TaskExtractionAgent(llm_client=MagicMock(), audit_logger=audit, run_id="r1")
    _stub_structured(agent, returns=ExtractionResult(
        scratchpad="Three atomic units: login, registration, password reset.",
        tasks=[
            _raw_task("Implement login API"),
            _raw_task("Implement registration API"),
        ],
    ))

    tasks = agent.extract(_node(), _section_text())
    assert len(tasks) == 2
    assert tasks[0].title == "Implement login API"
    assert tasks[1].title == "Implement registration API"
    # ACs normalized to AcceptanceCriterion objects.
    assert isinstance(tasks[0].acceptance_criteria[0], AcceptanceCriterion)

    # Scratchpad is audit-logged and NEVER propagated onto the RawTask.
    scratchpad_actions = [
        r for r in audit.records if r.get("action") == "EXTRACTION_SCRATCHPAD"
    ]
    assert len(scratchpad_actions) == 1
    assert "Three atomic units" in scratchpad_actions[0]["detail"]
    for t in tasks:
        # RawTask has no scratchpad field; verify nothing leaked.
        assert "scratchpad" not in t.model_dump()


def test_extraction_scratchpad_missing_is_ok():
    """Result with no scratchpad still parses tasks. No EXTRACTION_SCRATCHPAD log."""
    audit = _DummyAudit()
    agent = TaskExtractionAgent(llm_client=MagicMock(), audit_logger=audit, run_id="r1")
    _stub_structured(agent, returns=ExtractionResult(tasks=[_raw_task()]))

    tasks = agent.extract(_node(), _section_text())
    assert len(tasks) == 1
    assert not any(
        r.get("action") == "EXTRACTION_SCRATCHPAD" for r in audit.records
    )


def test_extraction_invalid_structured_output_returns_empty():
    """Instructor could not produce a valid ExtractionResult → empty result + audit."""
    audit = _DummyAudit()
    agent = TaskExtractionAgent(llm_client=MagicMock(), audit_logger=audit, run_id="r1")
    _stub_structured(agent, raises=InstructorError("tasks field was not a list"))

    tasks = agent.extract(_node(), _section_text())
    assert tasks == []
    assert any(r.get("action") == "EXTRACTION_ERROR" for r in audit.records)


# ─── Tasks-only result parses cleanly ───────────────────────────────────────

def test_extraction_tasks_only_result():
    """A tasks-only ExtractionResult (no scratchpad) parses every task, no log."""
    audit = _DummyAudit()
    agent = TaskExtractionAgent(llm_client=MagicMock(), audit_logger=audit, run_id="r1")
    _stub_structured(agent, returns=ExtractionResult(
        tasks=[_raw_task("Legacy task one"), _raw_task("Legacy task two")],
    ))

    tasks = agent.extract(_node(), _section_text())
    assert len(tasks) == 2
    assert tasks[0].title == "Legacy task one"
    # No scratchpad logged (none present).
    assert not any(
        r.get("action") == "EXTRACTION_SCRATCHPAD" for r in audit.records
    )


def test_extraction_structured_failure_returns_empty():
    """Any structured-output failure fails safe to an empty result + audit row."""
    audit = _DummyAudit()
    agent = TaskExtractionAgent(llm_client=MagicMock(), audit_logger=audit, run_id="r1")
    _stub_structured(agent, raises=InstructorError("provider returned a bare string"))

    tasks = agent.extract(_node(), _section_text())
    assert tasks == []
    assert any(r.get("action") == "EXTRACTION_ERROR" for r in audit.records)


# ─── LOW_CONFIDENCE auto-flag preserved ─────────────────────────────────────

def test_extraction_low_confidence_flag_unchanged():
    """confidence below threshold (default 0.6) auto-adds LOW_CONFIDENCE flag."""
    agent = TaskExtractionAgent(
        llm_client=MagicMock(),
        audit_logger=_DummyAudit(),
        run_id="r1",
        confidence_threshold=0.6,
    )
    _stub_structured(agent, returns=ExtractionResult(
        scratchpad="Edge case",
        tasks=[
            _raw_task("Borderline task", confidence=0.5),  # below threshold
            _raw_task("Solid task", confidence=0.9),       # above threshold
        ],
    ))

    tasks = agent.extract(_node(), _section_text())
    assert len(tasks) == 2

    borderline = next(t for t in tasks if t.title == "Borderline task")
    solid = next(t for t in tasks if t.title == "Solid task")
    assert "LOW_CONFIDENCE" in borderline.flags
    assert "LOW_CONFIDENCE" not in solid.flags


# ─── A4: surface section truncation (warn + flag, not silent drop) ───────────


def test_extraction_flags_truncation_on_oversize_section():
    """A section longer than max_section_chars is truncated — the tasks extracted
    from it carry a TRUNCATION flag and the run records a SECTION_TRUNCATED audit
    row, so dense pages don't silently lose tasks."""
    audit = _DummyAudit()
    agent = TaskExtractionAgent(
        llm_client=MagicMock(), audit_logger=audit, run_id="r1", max_section_chars=100
    )
    _stub_structured(agent, returns=ExtractionResult(tasks=[_raw_task("Big task")]))

    long_section = "This section is densely packed with requirements. " * 20  # > 100 chars
    tasks = agent.extract(_node(), long_section)

    assert len(tasks) == 1
    assert TaskFlag.TRUNCATION.value in tasks[0].flags
    assert any(r.get("action") == "SECTION_TRUNCATED" for r in audit.records)


def test_extraction_no_truncation_flag_when_within_limit():
    """A section within max_section_chars is NOT flagged and logs no truncation."""
    audit = _DummyAudit()
    agent = TaskExtractionAgent(
        llm_client=MagicMock(), audit_logger=audit, run_id="r1", max_section_chars=16000
    )
    _stub_structured(agent, returns=ExtractionResult(tasks=[_raw_task("Normal task")]))

    tasks = agent.extract(_node(), _section_text())

    assert TaskFlag.TRUNCATION.value not in tasks[0].flags
    assert not any(r.get("action") == "SECTION_TRUNCATED" for r in audit.records)


def test_extraction_short_section_skipped():
    """Existing < 50 char guard still short-circuits before the LLM call."""
    audit = _DummyAudit()
    agent = TaskExtractionAgent(llm_client=MagicMock(), audit_logger=audit, run_id="r1")
    structured = _stub_structured(agent, returns=ExtractionResult(tasks=[_raw_task()]))

    tasks = agent.extract(_node(), "tiny")
    assert tasks == []
    structured.assert_not_called()
    assert any(r.get("action") == "SKIPPED_SHORT_SECTION" for r in audit.records)
