# tests/test_cost_meter.py
"""WAVE-7 CostMeter kill-switch.

A run-scoped (prompt_tokens, completion_tokens) accumulator threaded FROM the
LLMClient computes cumulative USD via ``litellm.cost_per_token`` (NEVER
``litellm.completion_cost``) and trips an ``.exceeded()`` kill-switch once the
per-run budget ``max_run_cost`` is passed. The orchestrator gates it inside the
EXISTING ``_cancelled()`` checkpoints, so an over-budget run aborts via the
existing cancellation return path (a RunCostExceeded condition).
"""

from __future__ import annotations

import pathlib
import sys
from types import SimpleNamespace
from uuid import uuid4

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from core.cost_meter import CostMeter
from models.schemas import JiraHierarchy, LLMMode, RunConfig
from pipeline.coverage import CoverageTracker
from pipeline.orchestrator import PipelineOrchestrator

# ─── CostMeter unit behavior ──────────────────────────────────────────────────


def test_cost_meter_accumulates_and_trips_when_over_budget():
    # Very small budget so a couple of recorded calls exceed it.
    meter = CostMeter(max_run_cost=0.000001)
    assert meter.exceeded() is False

    # A known chat model litellm can price. Record enough tokens to exceed
    # a sub-microdollar budget.
    meter.record("gpt-3.5-turbo", prompt_tokens=1000, completion_tokens=1000)
    assert meter.total_cost() > 0.0
    assert meter.exceeded() is True


def test_cost_meter_stays_under_generous_budget():
    meter = CostMeter(max_run_cost=1000.0)
    meter.record("gpt-3.5-turbo", prompt_tokens=10, completion_tokens=10)
    assert meter.exceeded() is False


def test_cost_meter_uses_cost_per_token_not_completion_cost(monkeypatch):
    """The meter must price via litellm.cost_per_token; completion_cost is banned."""
    import litellm

    calls = {"cost_per_token": 0}

    real = litellm.cost_per_token

    def spy(*args, **kwargs):
        calls["cost_per_token"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(litellm, "cost_per_token", spy)
    # If the impl ever reaches for completion_cost, blow up loudly.
    monkeypatch.setattr(
        litellm,
        "completion_cost",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("completion_cost used")),
    )

    meter = CostMeter(max_run_cost=0.01)
    meter.record("gpt-3.5-turbo", prompt_tokens=100, completion_tokens=100)
    assert calls["cost_per_token"] >= 1


# ─── Orchestrator aborts via _cancelled() when over budget ────────────────────


class _FakeAudit:
    def log(self, **kwargs):
        return None


class _FakeIndexer:
    def get_node_text(self, node):
        return node.get("text", "")


class _FakeExtraction:
    def __init__(self):
        self.seen = []

    def extract(self, node, section_text, hierarchy=None, status_callback=None):
        self.seen.append(node["node_id"])
        return [{"node": node["node_id"]}]


class _FakeState:
    def process(self, raw_tasks, open_tasks, node):
        return open_tasks, [SimpleNamespace(id=uuid4(), flags=[]) for _ in raw_tasks]

    def close_all_remaining(self, open_tasks):
        return list(open_tasks)


class _FakeLLM:
    def complete(self, *a, **k):
        return ""

    def complete_json(self, *a, **k):
        return []


def _make_orchestrator(tmp_path, max_run_cost=None):
    kwargs = dict(
        run_id="cost-test",
        sow_pdf_path="/tmp/x.pdf",
        llm_mode=LLMMode.CUSTOM,
        jira_hierarchy=JiraHierarchy.EPIC_TASK,
        jira_project_key="TEST",
        enable_resumption=False,
    )
    config = RunConfig(**kwargs)
    # max_run_cost FIELD is deferred to the next round — set it as a dynamic
    # attribute so the orchestrator's getattr(config, 'max_run_cost', None) sees it.
    if max_run_cost is not None:
        object.__setattr__(config, "max_run_cost", max_run_cost)
    app_config = {"pipeline": {"max_gap_recovery_iterations": 1, "max_section_chars": 16000}}
    orch = PipelineOrchestrator(
        config=config, app_config=app_config, audit=_FakeAudit(), llm=_FakeLLM(),
    )
    orch.classifier = None
    orch.critic = None
    orch.coverage_checker = None
    orch.indexer = _FakeIndexer()
    orch.extraction_agent = _FakeExtraction()
    orch.state_agent = _FakeState()
    orch._status_file = tmp_path / "status.json"
    orch._status_path = lambda: orch._status_file
    return orch


def _nodes(n):
    return [{"node_id": f"n{i}", "title": f"N{i}", "text": "x" * 200} for i in range(n)]


def test_over_budget_run_aborts_via_cancelled(tmp_path, monkeypatch):
    monkeypatch.setenv("SOW_NODE_CONCURRENCY", "1")
    orch = _make_orchestrator(tmp_path, max_run_cost=0.000001)
    # A cost meter must have been built from the truthy max_run_cost.
    assert orch.cost_meter is not None

    # Simulate spend: push the meter over budget as if the LLM had recorded usage.
    orch.cost_meter.record("gpt-3.5-turbo", prompt_tokens=1000, completion_tokens=1000)
    assert orch.cost_meter.exceeded() is True

    # The EXISTING cancellation seam now reports cancelled because the budget blew.
    assert orch._cancelled() is True

    # A run therefore aborts at the next _cancelled() checkpoint, returning
    # whatever was extracted so far.
    all_closed, _, cancelled = orch._extract_all(_nodes(5), CoverageTracker(_nodes(5)))
    assert cancelled is True
    assert orch.extraction_agent.seen == []  # aborted before the first node


def test_under_budget_run_not_cancelled(tmp_path, monkeypatch):
    monkeypatch.setenv("SOW_NODE_CONCURRENCY", "1")
    orch = _make_orchestrator(tmp_path, max_run_cost=1000.0)
    assert orch.cost_meter is not None
    orch.cost_meter.record("gpt-3.5-turbo", prompt_tokens=10, completion_tokens=10)
    assert orch._cancelled() is False
    all_closed, _, cancelled = orch._extract_all(_nodes(3), CoverageTracker(_nodes(3)))
    assert cancelled is False
    assert len(all_closed) == 3


def test_no_budget_means_no_cost_meter(tmp_path):
    orch = _make_orchestrator(tmp_path, max_run_cost=None)
    assert orch.cost_meter is None
    # Cancellation still tracks only the stop_event, unchanged.
    assert orch._cancelled() is False
