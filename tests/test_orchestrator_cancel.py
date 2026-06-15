# tests/test_orchestrator_cancel.py
"""
C2: cancel_check seam + filesystem status fallback.

The orchestrator accepts an injectable ``cancel_check: Callable[[], bool]`` that
defaults to ``stop_event.is_set`` (no behavior change). It is consulted between
nodes so a run can be cancelled cleanly mid-loop — this is the clean seam the
Wave-2 durable worker binds to (e.g. a Redis ``cancel:{run_id}`` probe). Run
status is also mirrored to a filesystem JSON so partial progress survives a
process restart even before the durable worker lands.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

from pipeline.orchestrator import PipelineOrchestrator
from pipeline.coverage import CoverageTracker
from models.schemas import RunConfig, LLMMode, JiraHierarchy


class FakeLLM:
    def complete(self, *a, **k):
        return ""

    def complete_json(self, *a, **k):
        return []


class FakeAudit:
    def log(self, **kwargs):
        return None


class FakeIndexer:
    def get_node_text(self, node):
        return node.get("text", "")


class FakeExtraction:
    def __init__(self):
        self.seen = []

    def extract(self, node, section_text, hierarchy=None, status_callback=None):
        self.seen.append(node["node_id"])
        return [{"node": node["node_id"]}]


class FakeState:
    def process(self, raw_tasks, open_tasks, node):
        return open_tasks, [SimpleNamespace(id=uuid4(), flags=[]) for _ in raw_tasks]

    def close_all_remaining(self, open_tasks):
        return list(open_tasks)


def _make_orchestrator(tmp_path, cancel_check=None):
    config = RunConfig(
        run_id="cancel-test",
        sow_pdf_path="/tmp/x.pdf",
        llm_mode=LLMMode.CUSTOM,
        jira_hierarchy=JiraHierarchy.EPIC_TASK,
        jira_project_key="TEST",
        enable_resumption=False,  # isolate cancellation from resume
    )
    app_config = {"pipeline": {"max_gap_recovery_iterations": 1, "max_section_chars": 16000}}
    orch = PipelineOrchestrator(
        config=config, app_config=app_config, audit=FakeAudit(), llm=FakeLLM(),
        cancel_check=cancel_check,
    )
    orch.classifier = None
    orch.critic = None
    orch.coverage_checker = None
    orch.indexer = FakeIndexer()
    orch.extraction_agent = FakeExtraction()
    orch.state_agent = FakeState()
    orch._status_file = tmp_path / "status.json"
    orch._status_path = lambda: orch._status_file
    return orch


def _nodes(n):
    return [{"node_id": f"n{i}", "title": f"N{i}", "text": "x" * 200} for i in range(n)]


# ─── cancel_check default + injection ─────────────────────────────────────────


def test_default_cancel_check_tracks_stop_event(tmp_path, monkeypatch):
    monkeypatch.setenv("SOW_NODE_CONCURRENCY", "1")
    orch = _make_orchestrator(tmp_path)  # no cancel_check → defaults to stop_event
    assert orch._cancelled() is False
    orch.stop_event.set()
    assert orch._cancelled() is True


def test_injected_cancel_check_stops_run_between_nodes(tmp_path, monkeypatch):
    """An injected cancel_check that flips True mid-run cancels cleanly, returning
    the partial results extracted so far."""
    monkeypatch.setenv("SOW_NODE_CONCURRENCY", "1")
    state = {"checks": 0}

    def cancel_after_two():
        state["checks"] += 1
        return state["checks"] > 2  # False, False, then True

    orch = _make_orchestrator(tmp_path, cancel_check=cancel_after_two)
    nodes = _nodes(5)

    all_closed, _, cancelled = orch._extract_all(nodes, CoverageTracker(nodes))

    assert cancelled is True
    assert len(all_closed) == 2                       # only n0, n1 processed
    assert orch.extraction_agent.seen == ["n0", "n1"]


def test_cancel_check_not_triggered_runs_to_completion(tmp_path, monkeypatch):
    monkeypatch.setenv("SOW_NODE_CONCURRENCY", "1")
    orch = _make_orchestrator(tmp_path, cancel_check=lambda: False)
    nodes = _nodes(4)

    all_closed, _, cancelled = orch._extract_all(nodes, CoverageTracker(nodes))

    assert cancelled is False
    assert len(all_closed) == 4


# ─── filesystem status mirror ─────────────────────────────────────────────────


def test_status_mirrored_to_filesystem(tmp_path):
    orch = _make_orchestrator(tmp_path)
    orch._update_status(3, "Processing node 7", 0.55)

    assert orch._status_path().exists()
    data = json.loads(orch._status_path().read_text())
    assert data["step"] == 3
    assert data["message"] == "Processing node 7"
    assert data["progress"] == 0.55
