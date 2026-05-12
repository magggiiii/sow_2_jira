# tests/test_extraction.py
"""
Wave 2B coverage: ExtractionAgent parses the new {scratchpad, tasks} wrapper
and still accepts the legacy bare-array shape. Also confirms the existing
LOW_CONFIDENCE auto-flag still fires at threshold after the prompt change.
"""

from __future__ import annotations

import pathlib
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from models.schemas import AcceptanceCriterion, TaskFlag
from pipeline.agents.extraction import TaskExtractionAgent


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


def _llm_returning(value):
    client = MagicMock()
    client.complete_json.return_value = value
    return client


# ─── New wrapper shape ──────────────────────────────────────────────────────

def test_extraction_parses_scratchpad_wrapper():
    """LLM returns {scratchpad, tasks} — tasks parse, scratchpad is audited."""
    payload = {
        "scratchpad": "Three atomic units: login, registration, password reset.",
        "tasks": [
            _raw_task("Implement login API"),
            _raw_task("Implement registration API"),
        ],
    }
    llm = _llm_returning(payload)
    audit = _DummyAudit()
    agent = TaskExtractionAgent(llm_client=llm, audit_logger=audit, run_id="r1")

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
    """Wrapper with no scratchpad still parses tasks. No EXTRACTION_SCRATCHPAD log."""
    payload = {"tasks": [_raw_task()]}
    llm = _llm_returning(payload)
    audit = _DummyAudit()
    agent = TaskExtractionAgent(llm_client=llm, audit_logger=audit, run_id="r1")

    tasks = agent.extract(_node(), _section_text())
    assert len(tasks) == 1
    assert not any(
        r.get("action") == "EXTRACTION_SCRATCHPAD" for r in audit.records
    )


def test_extraction_wrapper_with_non_list_tasks_returns_empty():
    """Wrapper present but tasks field is not a list → empty result + audit."""
    payload = {"scratchpad": "Confused", "tasks": "oops"}
    llm = _llm_returning(payload)
    audit = _DummyAudit()
    agent = TaskExtractionAgent(llm_client=llm, audit_logger=audit, run_id="r1")

    tasks = agent.extract(_node(), _section_text())
    assert tasks == []
    assert any(r.get("action") == "EXTRACTION_ERROR" for r in audit.records)


# ─── Legacy shape fallback ──────────────────────────────────────────────────

def test_extraction_falls_back_to_bare_array():
    """Old / small models still return a bare list. The agent must accept it."""
    payload = [_raw_task("Legacy task one"), _raw_task("Legacy task two")]
    llm = _llm_returning(payload)
    audit = _DummyAudit()
    agent = TaskExtractionAgent(llm_client=llm, audit_logger=audit, run_id="r1")

    tasks = agent.extract(_node(), _section_text())
    assert len(tasks) == 2
    assert tasks[0].title == "Legacy task one"
    # No scratchpad logged (legacy shape has none).
    assert not any(
        r.get("action") == "EXTRACTION_SCRATCHPAD" for r in audit.records
    )


def test_extraction_unsupported_response_type_returns_empty():
    """If the LLM returns something that is neither dict nor list, fail safe."""
    llm = _llm_returning("just a string")
    audit = _DummyAudit()
    agent = TaskExtractionAgent(llm_client=llm, audit_logger=audit, run_id="r1")

    tasks = agent.extract(_node(), _section_text())
    assert tasks == []
    assert any(r.get("action") == "EXTRACTION_ERROR" for r in audit.records)


# ─── LOW_CONFIDENCE auto-flag preserved ─────────────────────────────────────

def test_extraction_low_confidence_flag_unchanged():
    """confidence below threshold (default 0.6) auto-adds LOW_CONFIDENCE flag."""
    payload = {
        "scratchpad": "Edge case",
        "tasks": [
            _raw_task("Borderline task", confidence=0.5),  # below threshold
            _raw_task("Solid task", confidence=0.9),       # above threshold
        ],
    }
    llm = _llm_returning(payload)
    agent = TaskExtractionAgent(
        llm_client=llm,
        audit_logger=_DummyAudit(),
        run_id="r1",
        confidence_threshold=0.6,
    )

    tasks = agent.extract(_node(), _section_text())
    assert len(tasks) == 2

    borderline = next(t for t in tasks if t.title == "Borderline task")
    solid = next(t for t in tasks if t.title == "Solid task")
    assert "LOW_CONFIDENCE" in borderline.flags
    assert "LOW_CONFIDENCE" not in solid.flags


def test_extraction_short_section_skipped():
    """Existing < 50 char guard still short-circuits before the LLM call."""
    llm = MagicMock()
    audit = _DummyAudit()
    agent = TaskExtractionAgent(llm_client=llm, audit_logger=audit, run_id="r1")

    tasks = agent.extract(_node(), "tiny")
    assert tasks == []
    llm.complete_json.assert_not_called()
    assert any(r.get("action") == "SKIPPED_SHORT_SECTION" for r in audit.records)
