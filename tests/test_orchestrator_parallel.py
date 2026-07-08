# tests/test_orchestrator_parallel.py
"""
B1: parallelize the per-node extraction loop.

The per-node classify+extract calls are pure functions of (node, section_text),
so they are safe to run concurrently across nodes. The order-sensitive work —
StateAgent continuation merge, critic/coverage (which read the cross-node
``open_tasks`` accumulator), and ``CoverageTracker.mark_covered`` — stays
sequential and is applied in node order. The hard contract: parallel output ==
sequential output (same tasks / coverage / dedup). ``SOW_NODE_CONCURRENCY=1``
reproduces the legacy sequential path exactly.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from uuid import uuid4

from models.schemas import JiraHierarchy, LLMMode, RunConfig
from pipeline.coverage import CoverageTracker
from pipeline.orchestrator import PipelineOrchestrator

# ─── Fakes ────────────────────────────────────────────────────────────────────


class FakeLLM:
    def complete(self, *a, **k):
        return ""

    def complete_json(self, *a, **k):
        return []


class FakeAudit:
    def __init__(self):
        self.entries: list[dict] = []
        self._lock = threading.Lock()

    def log(self, **kwargs):
        with self._lock:
            self.entries.append(kwargs)


class FakeIndexer:
    def get_node_text(self, node):
        return node.get("text", "")


class FakeExtraction:
    """One raw task per node, tagged with its node_id; raises for fail_on ids.
    Tracks max concurrent in-flight extracts so the test can assert the pool
    bound and that parallelism actually happened."""

    def __init__(self, fail_on=(), hold=0.0):
        self.fail_on = set(fail_on)
        self.hold = hold
        self._lock = threading.Lock()
        self.inflight = 0
        self.max_inflight = 0
        self.seen: list[str] = []

    def extract(self, node, section_text, hierarchy=None, status_callback=None):
        with self._lock:
            self.inflight += 1
            self.max_inflight = max(self.max_inflight, self.inflight)
            self.seen.append(node["node_id"])
        try:
            if self.hold:
                time.sleep(self.hold)
            if node["node_id"] in self.fail_on:
                raise RuntimeError(f"extraction blew up on {node['node_id']}")
            return [{"node": node["node_id"]}]
        finally:
            with self._lock:
                self.inflight -= 1


class FakeState:
    """Promotes each raw task to a managed task tagged with its source node."""

    def process(self, raw_tasks, open_tasks, node):
        closed = [
            SimpleNamespace(id=uuid4(), flags=[], node=r["node"]) for r in raw_tasks
        ]
        return open_tasks, closed

    def close_all_remaining(self, open_tasks):
        return list(open_tasks)


def _make_orchestrator(extraction):
    config = RunConfig(
        run_id="parallel-test",
        sow_pdf_path="/tmp/x.pdf",
        llm_mode=LLMMode.CUSTOM,
        jira_hierarchy=JiraHierarchy.EPIC_TASK,
        jira_project_key="TEST",
    )
    app_config = {"pipeline": {"max_gap_recovery_iterations": 1, "max_section_chars": 16000}}
    orch = PipelineOrchestrator(
        config=config, app_config=app_config, audit=FakeAudit(), llm=FakeLLM()
    )
    orch.classifier = None
    orch.critic = None
    orch.coverage_checker = None
    orch.indexer = FakeIndexer()
    orch.extraction_agent = extraction
    orch.state_agent = FakeState()
    return orch


def _nodes(n):
    return [{"node_id": f"n{i}", "title": f"N{i}", "text": "x" * 200} for i in range(n)]


# ─── equivalence: parallel == sequential ──────────────────────────────────────


def test_parallel_output_equals_sequential(monkeypatch):
    nodes = _nodes(8)

    monkeypatch.setenv("SOW_NODE_CONCURRENCY", "1")
    seq = _make_orchestrator(FakeExtraction())
    seq_cov = CoverageTracker(nodes)
    seq_closed, _, _ = seq._extract_all(nodes, seq_cov)

    monkeypatch.setenv("SOW_NODE_CONCURRENCY", "4")
    par = _make_orchestrator(FakeExtraction())
    par_cov = CoverageTracker(nodes)
    par_closed, _, _ = par._extract_all(nodes, par_cov)

    # Same tasks (compared by their source-node tag, order-independent).
    assert sorted(t.node for t in seq_closed) == sorted(t.node for t in par_closed)
    # Same coverage.
    assert seq_cov.coverage_report() == par_cov.coverage_report()
    assert len(par_closed) == 8


def test_concurrency_one_uses_sequential_order(monkeypatch):
    """=1 reproduces the legacy path: nodes attempted strictly in order."""
    monkeypatch.setenv("SOW_NODE_CONCURRENCY", "1")
    nodes = _nodes(5)
    orch = _make_orchestrator(FakeExtraction())
    orch._extract_all(nodes, CoverageTracker(nodes))
    assert orch.extraction_agent.seen == ["n0", "n1", "n2", "n3", "n4"]


def test_inflight_bounded_by_max_workers(monkeypatch):
    """Concurrency is real (>1) but never exceeds SOW_NODE_CONCURRENCY."""
    monkeypatch.setenv("SOW_NODE_CONCURRENCY", "4")
    nodes = _nodes(12)
    extraction = FakeExtraction(hold=0.02)  # hold the slot so overlap is observable
    orch = _make_orchestrator(extraction)

    orch._extract_all(nodes, CoverageTracker(nodes))

    assert extraction.max_inflight > 1          # parallelism actually happened
    assert extraction.max_inflight <= 4         # and stayed within the pool


def test_parallel_isolates_failing_node(monkeypatch):
    """A node failing during the parallel extract phase is isolated; the rest
    still produce tasks (per-node isolation survives parallelization)."""
    monkeypatch.setenv("SOW_NODE_CONCURRENCY", "4")
    nodes = _nodes(6)
    orch = _make_orchestrator(FakeExtraction(fail_on={"n2"}))

    coverage = CoverageTracker(nodes)
    all_closed, _, cancelled = orch._extract_all(nodes, coverage)

    assert cancelled is False
    assert orch.node_error_count == 1
    assert {t.node for t in all_closed} == {"n0", "n1", "n3", "n4", "n5"}
    assert coverage.coverage_report()["covered_nodes"] == 5
    assert set(orch.extraction_agent.seen) == {f"n{i}" for i in range(6)}
