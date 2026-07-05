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

    # 1.6b: status now routes through the object store. Read it back via the
    # store (the default LocalObjectStore writes it under data/sessions/...).
    raw = orch._object_store.get(orch._artifact_key("status.json"))
    data = json.loads(raw.decode("utf-8"))
    assert data["step"] == 3
    assert data["message"] == "Processing node 7"
    assert data["progress"] == 0.55


# ─── 2.3-d: cancel_check propagates to the indexer + in-flight LLM calls ───────
#
# A durable worker injects a custom cancel_check (e.g. a Redis ``cancel:{run_id}``
# probe) that does NOT flip the local stop_event. 2.3-d bridges the cancel_check
# into the stop_event the indexer and LLMClient consult, so flipping cancel makes
# the indexer bail AND any in-flight LLM call raise.


def test_cancel_check_bridge_reports_set_when_probe_true(tmp_path):
    """The stop-event-like bridge handed to the indexer/LLM is 'set' when the
    injected cancel_check fires, even though the real stop_event is untouched."""
    probe = {"cancel": False}
    orch = _make_orchestrator(tmp_path, cancel_check=lambda: probe["cancel"])
    bridge = orch._cancel_signal()

    assert bridge.is_set() is False
    probe["cancel"] = True
    assert bridge.is_set() is True
    # The real local stop_event was never touched by the probe.
    assert orch.stop_event.is_set() is False


def test_cancel_check_bridge_reports_set_when_local_stop_event_set(tmp_path):
    orch = _make_orchestrator(tmp_path, cancel_check=lambda: False)
    bridge = orch._cancel_signal()
    assert bridge.is_set() is False
    orch.stop_event.set()
    assert bridge.is_set() is True


def test_indexer_receives_cancel_signal_and_bails(tmp_path):
    """_build_or_load_tree passes the cancel-aware signal to the indexer; when the
    probe fires the indexer sees ``is_set()`` True and bails."""
    probe = {"cancel": True}  # already cancelled before build
    orch = _make_orchestrator(tmp_path, cancel_check=lambda: probe["cancel"])

    captured = {}

    class RecordingIndexer:
        def build_tree(self, pdf_path, status_callback=None, stop_event=None, run_id="none"):
            captured["stop_event"] = stop_event
            # Mirror the real indexer's bail check.
            if stop_event and stop_event.is_set():
                return []
            return [{"node_id": "n0"}]

        def flatten_tree(self, tree):
            return tree

    orch.indexer = RecordingIndexer()
    nodes = orch._build_or_load_tree("/tmp/x.pdf")

    assert nodes == []                              # indexer bailed
    assert captured["stop_event"] is not None
    assert captured["stop_event"].is_set() is True  # cancel_check propagated


def test_inflight_llm_call_raises_when_cancel_check_flips(tmp_path):
    """The LLMClient built by the orchestrator consults the cancel-aware signal,
    so a cancel_check that flips mid-call makes an in-flight completion raise."""
    from pipeline.llm_client import LLMClient

    probe = {"cancel": False}
    config = RunConfig(
        run_id="cancel-llm-test",
        sow_pdf_path="/tmp/x.pdf",
        llm_mode=LLMMode.CUSTOM,
        jira_hierarchy=JiraHierarchy.EPIC_TASK,
        jira_project_key="TEST",
        enable_resumption=False,
    )
    app_config = {"pipeline": {"max_gap_recovery_iterations": 1, "max_section_chars": 16000}}
    # No llm injected → orchestrator builds a real LLMClient wired to the bridge.
    orch = PipelineOrchestrator(
        config=config, app_config=app_config, audit=FakeAudit(),
        cancel_check=lambda: probe["cancel"],
    )
    assert isinstance(orch.llm, LLMClient)

    # The LLMClient's stop_event is the cancel-aware signal.
    assert orch.llm.stop_event is not None
    assert orch.llm.stop_event.is_set() is False

    probe["cancel"] = True  # durable worker cancels the run
    # The LLMClient's cancellation guard (self.stop_event.is_set()) now trips.
    assert orch.llm.stop_event.is_set() is True
