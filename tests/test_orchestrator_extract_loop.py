# tests/test_orchestrator_extract_loop.py
"""
A2: per-node error isolation in the extraction loop.

A single node raising mid-loop must NOT abort the whole run (the legacy loop had
no per-node guard — one bad node lost every task). The loop body is extracted to
``_process_node`` and the loop wraps it: on failure it logs EXTRACTION_FAILED,
increments ``node_error_count``, and continues. These tests drive the extracted
loop directly with fakes — no PageIndex, no LLM, no network.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from pipeline.orchestrator import PipelineOrchestrator
from pipeline.coverage import CoverageTracker
from models.schemas import RunConfig, LLMMode, JiraHierarchy


# ─── Fakes ────────────────────────────────────────────────────────────────────


class FakeLLM:
    def complete(self, *a, **k):
        return ""

    def complete_json(self, *a, **k):
        return []


class FakeAudit:
    def __init__(self):
        self.entries: list[dict] = []

    def log(self, **kwargs):
        self.entries.append(kwargs)


class FakeIndexer:
    def get_node_text(self, node):
        return node.get("text", "")


class FakeExtraction:
    """Returns one raw task per node; raises for node_ids in ``fail_on``."""

    def __init__(self, fail_on=()):
        self.fail_on = set(fail_on)
        self.seen: list[str] = []

    def extract(self, node, section_text, hierarchy=None, status_callback=None):
        self.seen.append(node["node_id"])
        if node["node_id"] in self.fail_on:
            raise RuntimeError(f"extraction blew up on {node['node_id']}")
        return [{"raw": node["node_id"]}]


class FakeState:
    """Closes every extracted task immediately; no continuation."""

    def process(self, raw_tasks, open_tasks, node):
        closed = [SimpleNamespace(id=uuid4(), flags=[]) for _ in raw_tasks]
        return open_tasks, closed

    def close_all_remaining(self, open_tasks):
        return list(open_tasks)


def _make_orchestrator(extraction):
    config = RunConfig(
        run_id="extract-loop-test",
        sow_pdf_path="/tmp/x.pdf",
        llm_mode=LLMMode.CUSTOM,
        jira_hierarchy=JiraHierarchy.EPIC_TASK,
        jira_project_key="TEST",
    )
    app_config = {"pipeline": {"max_gap_recovery_iterations": 1, "max_section_chars": 16000}}
    orch = PipelineOrchestrator(
        config=config, app_config=app_config, audit=FakeAudit(), llm=FakeLLM()
    )
    # Strip the optional intelligence agents to isolate the loop mechanics.
    orch.classifier = None
    orch.critic = None
    orch.coverage_checker = None
    orch.indexer = FakeIndexer()
    orch.extraction_agent = extraction
    orch.state_agent = FakeState()
    return orch


def _nodes(n):
    return [{"node_id": f"n{i}", "title": f"N{i}", "text": "x" * 200} for i in range(n)]


# ─── isolation ────────────────────────────────────────────────────────────────


def test_one_failing_node_does_not_abort_the_run():
    nodes = _nodes(3)
    orch = _make_orchestrator(FakeExtraction(fail_on={"n1"}))
    coverage = CoverageTracker(nodes)

    all_closed, open_tasks, cancelled = orch._extract_all(nodes, coverage)

    assert cancelled is False
    # Nodes n0 and n2 still produced tasks; n1's failure was isolated.
    assert len(all_closed) == 2
    assert orch.node_error_count == 1
    # All three nodes were attempted (the loop continued past the failure).
    assert orch.extraction_agent.seen == ["n0", "n1", "n2"]
    # Coverage reflects the two nodes that succeeded.
    assert coverage.coverage_report()["covered_nodes"] == 2
    # The failure was recorded to the audit trail, not swallowed.
    assert any(e.get("action") == "EXTRACTION_FAILED" for e in orch.audit.entries)


def test_all_nodes_succeed_no_errors():
    nodes = _nodes(3)
    orch = _make_orchestrator(FakeExtraction())
    coverage = CoverageTracker(nodes)

    all_closed, open_tasks, cancelled = orch._extract_all(nodes, coverage)

    assert cancelled is False
    assert len(all_closed) == 3
    assert orch.node_error_count == 0
    assert coverage.coverage_report()["covered_nodes"] == 3


# ─── _process_node: classifier skip path ──────────────────────────────────────


def test_process_node_returns_none_when_classifier_skips():
    nodes = _nodes(1)
    orch = _make_orchestrator(FakeExtraction())

    class FakeClassifier:
        def classify(self, node, text):
            return SimpleNamespace(type=SimpleNamespace(value="non_actionable"), confidence=0.95)

        def should_extract(self, classification):
            return False

    orch.classifier = FakeClassifier()

    result = orch._process_node(nodes[0], [], 0, 1)
    assert result is None
    # Skipped node never reached extraction.
    assert orch.extraction_agent.seen == []
