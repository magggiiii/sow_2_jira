# tests/test_gap_recovery.py
"""
Behavior-pinning tests for GapRecoveryAgent (pipeline/agents/gap_recovery.py).

These lock the agent's observable contract. C-5: the single LLM call now routes
through ``runner.complete_structured(response_model=GapRecoveryResult)`` instead
of ``complete_json``. The mock-point moved to ``agent.runner.complete_structured``
— returning a validated ``GapRecoveryResult`` or raising ``InstructorError``.
They use a FAKE LLM (MagicMock) — no network, no litellm.

Pinned behavior (all preserved across the migration):
- recover() returns a list of (RawTask, source_node) tuples, with
  TaskFlag.GAP_RECOVERED appended to each task's flags.
- The structured call forwards exactly: prompt (truncated to 16000 chars), the
  GAP_SYSTEM_PROMPT, agent_name="GapRecoveryAgent", and node_id.
- Sections with < 100 chars of stripped text are skipped (no LLM call).
- A structured-output failure is swallowed and audited (recover() still returns).
- A per-task validation error is swallowed and audited (other tasks still
  recover) — the permissive RawRecoveredTask item lets a dirty task through to
  the agent's strict RawTask re-validation, which skips it.
- Audit emits RECOVERY_COMPLETE when anything recovered, NO_GAPS_RECOVERED when
  nothing did.
"""

from __future__ import annotations

import pathlib
import sys
from unittest.mock import MagicMock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from core.agent_runner import InstructorError
from models.schemas import RawTask, TaskFlag
from pipeline.agents.gap_recovery import (
    GAP_SYSTEM_PROMPT,
    GapRecoveryAgent,
    GapRecoveryResult,
)


class _DummyAudit:
    def __init__(self):
        self.records = []

    def log(self, **kwargs):
        self.records.append(kwargs)

    def actions(self):
        return [r.get("action") for r in self.records]


def _node(node_id: str = "n1", title: str = "Skipped Section") -> dict:
    return {
        "node_id": node_id,
        "title": title,
        "page_start": 3,
        "page_end": 5,
    }


def _raw_task(title: str = "Implement the missed export job") -> dict:
    return {
        "title": title,
        "short_description": "Build the nightly export job that was skipped.",
        "acceptance_criteria": ["[ ] Export runs nightly and writes a CSV"],
        "use_case": None,
        "considerations_constraints": None,
        "deliverables": None,
        "mockup_prototype": None,
        "confidence": 0.8,
        "flags": [],
        "continues_to_next": False,
    }


class _Indexer:
    """Minimal indexer stub: maps node_id -> section text."""

    def __init__(self, text_by_node: dict[str, str]):
        self._text = text_by_node

    def get_node_text(self, node: dict) -> str:
        return self._text[node["node_id"]]


def _stub_structured(agent, *, returns=None, raises=None) -> MagicMock:
    """Stub the C-5 Instructor seam on the agent's runner.

    ``returns`` is the validated ``GapRecoveryResult`` the runner would hand
    back; ``raises`` simulates a structured-output failure. Returns the mock so
    tests can assert call args / no-call.
    """
    mock = MagicMock()
    if raises is not None:
        mock.side_effect = raises
    else:
        mock.return_value = returns
    agent.runner.complete_structured = mock
    return mock


def _long_text() -> str:
    # >= 100 chars so the short-section gate doesn't short-circuit.
    return "This section describes a nightly data export pipeline. " * 4


# ─── Happy path: recovers tasks, flags them, returns (task, node) tuples ──────


def test_recover_returns_task_node_tuples_with_gap_flag():
    node = _node()
    agent = GapRecoveryAgent(MagicMock(), _DummyAudit(), run_id="r1")
    _stub_structured(agent, returns=GapRecoveryResult(
        tasks=[_raw_task("Implement the missed export job")]
    ))
    indexer = _Indexer({"n1": _long_text()})

    results = agent.recover([node], indexer)

    assert len(results) == 1
    raw_obj, source_node = results[0]
    assert isinstance(raw_obj, RawTask)
    assert source_node is node  # exact source-node attribution preserved
    assert TaskFlag.GAP_RECOVERED in raw_obj.flags


# ─── The structured call forwards exactly the agent's current kwargs ──────────


def test_recover_forwards_expected_llm_kwargs():
    node = _node(node_id="n7", title="Appendix B")
    agent = GapRecoveryAgent(MagicMock(), _DummyAudit(), run_id="r1")
    structured = _stub_structured(agent, returns=GapRecoveryResult(tasks=[]))
    indexer = _Indexer({"n7": _long_text()})

    agent.recover([node], indexer)

    assert structured.call_count == 1
    _, kwargs = structured.call_args
    assert kwargs["system"] == GAP_SYSTEM_PROMPT
    assert kwargs["agent_name"] == "GapRecoveryAgent"
    assert kwargs["node_id"] == "n7"
    # Prompt carries the node attribution and is built from the template.
    assert "Appendix B" in kwargs["prompt"]


def test_recover_truncates_section_text_to_16000_chars():
    node = _node(node_id="big")
    agent = GapRecoveryAgent(MagicMock(), _DummyAudit(), run_id="r1")
    structured = _stub_structured(agent, returns=GapRecoveryResult(tasks=[]))
    # Use a marker char that does NOT appear anywhere in the prompt template so
    # the count isolates the embedded section body.
    indexer = _Indexer({"big": "§" * 50000})

    agent.recover([node], indexer)

    _, kwargs = structured.call_args
    # The template embeds section_text[:16000]; the prompt must not contain the
    # full 50000-char body.
    assert kwargs["prompt"].count("§") == 16000


# ─── Short sections are skipped entirely (no LLM call) ────────────────────────


def test_recover_skips_short_sections():
    node = _node(node_id="short")
    agent = GapRecoveryAgent(MagicMock(), _DummyAudit(), run_id="r1")
    structured = _stub_structured(agent, returns=GapRecoveryResult(tasks=[_raw_task()]))
    indexer = _Indexer({"short": "   tiny   "})  # < 100 chars stripped

    results = agent.recover([node], indexer)

    assert results == []
    structured.assert_not_called()


# ─── Unparseable structured output is ignored for that node ───────────────────


def test_recover_ignores_non_list_response():
    node = _node()
    agent = GapRecoveryAgent(MagicMock(), _DummyAudit(), run_id="r1")
    _stub_structured(agent, raises=InstructorError("not a valid GapRecoveryResult"))
    indexer = _Indexer({"n1": _long_text()})

    results = agent.recover([node], indexer)

    assert results == []


# ─── Per-task validation error is swallowed and audited ───────────────────────


def test_recover_swallows_per_task_parse_error_and_audits():
    node = _node()
    # First item is malformed (no confidence) — the permissive RawRecoveredTask
    # accepts it, but the agent's strict RawTask re-validation rejects it. The
    # second item is valid and must still recover.
    bad = {"title": "Broken", "short_description": "no confidence field"}
    good = _raw_task("Implement the valid recovered task")
    audit = _DummyAudit()
    agent = GapRecoveryAgent(MagicMock(), audit, run_id="r1")
    _stub_structured(agent, returns=GapRecoveryResult(tasks=[bad, good]))
    indexer = _Indexer({"n1": _long_text()})

    results = agent.recover([node], indexer)

    assert len(results) == 1
    assert results[0][0].title == "Implement the valid recovered task"
    assert "RECOVERY_PARSE_ERROR" in audit.actions()


# ─── Whole-call structured-output error is swallowed and audited ──────────────


def test_recover_swallows_llm_error_and_audits():
    node = _node()
    audit = _DummyAudit()
    agent = GapRecoveryAgent(MagicMock(), audit, run_id="r1")
    _stub_structured(agent, raises=InstructorError("LLM exploded"))
    indexer = _Indexer({"n1": _long_text()})

    results = agent.recover([node], indexer)

    assert results == []
    assert "RECOVERY_ERROR" in audit.actions()


# ─── Completion-summary audit signals ─────────────────────────────────────────


def test_recover_audits_recovery_complete_when_tasks_found():
    node = _node()
    audit = _DummyAudit()
    agent = GapRecoveryAgent(MagicMock(), audit, run_id="r1")
    _stub_structured(agent, returns=GapRecoveryResult(tasks=[_raw_task()]))
    indexer = _Indexer({"n1": _long_text()})

    agent.recover([node], indexer)

    assert "RECOVERY_COMPLETE" in audit.actions()
    assert "NO_GAPS_RECOVERED" not in audit.actions()


def test_recover_audits_no_gaps_when_nothing_found():
    node = _node()
    audit = _DummyAudit()
    agent = GapRecoveryAgent(MagicMock(), audit, run_id="r1")
    _stub_structured(agent, returns=GapRecoveryResult(tasks=[]))
    indexer = _Indexer({"n1": _long_text()})

    agent.recover([node], indexer)

    assert "NO_GAPS_RECOVERED" in audit.actions()
    assert "RECOVERY_COMPLETE" not in audit.actions()


# ─── The runner is wired over the same client (seam is in place) ──────────────


def test_agent_uses_agent_runner_over_its_llm():
    from core.agent_runner import AgentRunner

    llm = MagicMock()
    agent = GapRecoveryAgent(llm, _DummyAudit(), run_id="r1")

    assert isinstance(agent.runner, AgentRunner)
    assert agent.runner.llm is llm
