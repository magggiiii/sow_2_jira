# tests/test_pipeline_runner_equivalence.py
"""
STEP 3.7 — OFFLINE equivalence golden test.

Runs the SAME deterministic synthetic cassette (the INV-4 harness machinery)
through both the legacy ``PipelineOrchestrator.run()`` AND the new staged
``run_via_pipeline()`` and asserts the two ``pipeline_output.json`` artifacts are
equivalent: identical task_count, flag multiset, merge_count, coverage_report,
health, and top-level structure. This is the safety net that lets the
PipelineRunner land behind a flag without risking pipeline behavior — if it
can't go green, the runner is wrong.

Fully offline: LLM replaced at ``AgentRunner.complete_structured``, dedup pairs
frozen, ``SOW_NODE_CONCURRENCY=1``, no PageIndex, no network.
"""

from __future__ import annotations

import json
import os
import shutil
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

from core.agent_runner import AgentRunner
from models.schemas import DedupDecision, JiraHierarchy, LLMMode, ProviderConfig, RunConfig
from pipeline.agents.deduplication import DedupDecisionList, DeduplicationAgent
from pipeline.evals.harness import build_cassette, golden_tree
from pipeline.evals.replay import NoOpProvider, make_structured_replay


class _NoOpAudit:
    def log(self, **kwargs):
        return None


def _run_driver(run_id: str, driver) -> dict:
    """Drive one deterministic offline run via ``driver(orch)`` and return its
    parsed pipeline_output.json. Mirrors EvalHarness.run()'s setup exactly so the
    only variable between calls is the driver (run() vs run_via_pipeline())."""
    nodes = golden_tree()
    sess = Path(f"data/sessions/{run_id}")
    if sess.exists():
        shutil.rmtree(sess)
    sess.mkdir(parents=True, exist_ok=True)
    (sess / "document_tree.json").write_text(json.dumps(nodes))

    cassette = build_cassette(nodes)
    cassette_replay = make_structured_replay(cassette)
    captured = {"pair": None}

    def fake_find_pairs(agent_self, tasks, embeddings):
        if len(tasks) >= 2:
            captured["pair"] = (str(tasks[0].id), str(tasks[1].id))
            return [(tasks[0], tasks[1], 0.95)]
        captured["pair"] = None
        return []

    def replay_or_dedup(runner_self, *, prompt, response_model, system=None,
                        max_tokens=None, agent_name=None, node_id=None):
        if agent_name == "DeduplicationAgent":
            if captured["pair"]:
                a, b = captured["pair"]
                return DedupDecisionList(decisions=[DedupDecision(
                    task_id_a=a, task_id_b=b, decision="merge", reason="eval merge")])
            return DedupDecisionList(decisions=[])
        return cassette_replay(
            runner_self, prompt=prompt, response_model=response_model,
            system=system, max_tokens=max_tokens, agent_name=agent_name, node_id=node_id,
        )

    config = RunConfig(
        run_id=run_id, sow_pdf_path="/tmp/eval.pdf",
        llm_mode=LLMMode.CUSTOM, jira_hierarchy=JiraHierarchy.EPIC_TASK,
        jira_project_key="", skip_indexing=True, enable_resumption=False,
        provider_config=ProviderConfig(provider="replay", model="replay/none"),
    )
    app_config = {"pipeline": {"max_gap_recovery_iterations": 1, "max_section_chars": 16000}}

    prev = os.environ.get("SOW_NODE_CONCURRENCY")
    os.environ["SOW_NODE_CONCURRENCY"] = "1"
    try:
        from pipeline.orchestrator import PipelineOrchestrator
        with ExitStack() as stack:
            stack.enter_context(
                mock.patch.object(AgentRunner, "complete_structured", replay_or_dedup)
            )
            stack.enter_context(
                mock.patch.object(
                    DeduplicationAgent, "_find_candidate_pairs", fake_find_pairs
                )
            )
            orch = PipelineOrchestrator(
                config=config, app_config=app_config, audit=_NoOpAudit(), llm=NoOpProvider(),
            )
            driver(orch)
        return json.loads((sess / "pipeline_output.json").read_text())
    finally:
        if prev is None:
            os.environ.pop("SOW_NODE_CONCURRENCY", None)
        else:
            os.environ["SOW_NODE_CONCURRENCY"] = prev
        if sess.exists():
            shutil.rmtree(sess)


def _flag_multiset(output: dict) -> list:
    return sorted(f for t in output["tasks"] for f in t.get("flags", []))


def _merge_count(output: dict) -> int:
    return sum(len(t.get("merged_from", [])) for t in output["tasks"])


def _legacy_run(o):
    """Force the linear run() path regardless of the RunConfig default so the
    equivalence comparison stays meaningful now that use_pipeline_runner
    defaults to True."""
    o.config.use_pipeline_runner = False
    return o.run()


def test_run_via_pipeline_matches_run_on_synthetic_cassette():
    out_run = _run_driver("eq-legacy-run", _legacy_run)
    out_pipe = _run_driver("eq-pipeline-run", lambda o: o.run_via_pipeline())

    # Same number of final tasks.
    assert len(out_pipe["tasks"]) == len(out_run["tasks"]), (
        f"task_count: runner={len(out_pipe['tasks'])} run()={len(out_run['tasks'])}"
    )
    # Same flags across the corpus (multiset).
    assert _flag_multiset(out_pipe) == _flag_multiset(out_run)
    # Same number of merges — and the eval's deterministic merge actually happened.
    assert _merge_count(out_pipe) == _merge_count(out_run)
    assert _merge_count(out_run) >= 1
    # Same coverage report and health summary, and identical top-level structure.
    assert out_pipe["coverage_report"] == out_run["coverage_report"]
    assert out_pipe["health"] == out_run["health"]
    assert set(out_pipe.keys()) == set(out_run.keys())


def test_use_pipeline_runner_default_is_true():
    # Decision 3 (2026-07-08): the staged PipelineRunner path is now the default;
    # the legacy linear run() stays reachable by forcing the flag off (as the
    # equivalence drivers do). run() itself is NOT deleted (gated on STEP 3.8).
    assert RunConfig.model_fields["use_pipeline_runner"].default is True


def test_use_pipeline_runner_flag_routes_run_through_pipeline():
    # config.use_pipeline_runner=True makes run() delegate to run_via_pipeline();
    # the output must match the legacy linear run().
    def flagged_run(o):
        o.config.use_pipeline_runner = True
        return o.run()

    out_legacy = _run_driver("eq-flag-legacy", _legacy_run)
    out_flagged = _run_driver("eq-flag-on", flagged_run)

    assert len(out_flagged["tasks"]) == len(out_legacy["tasks"])
    assert _flag_multiset(out_flagged) == _flag_multiset(out_legacy)
    assert _merge_count(out_flagged) == _merge_count(out_legacy)
    assert out_flagged["coverage_report"] == out_legacy["coverage_report"]
    assert out_flagged["health"] == out_legacy["health"]


def test_run_via_pipeline_returns_same_task_count_as_its_artifact():
    # The return value of run_via_pipeline() agrees with what it persisted.
    captured_return = {}

    def driver(o):
        captured_return["tasks"] = o.run_via_pipeline()

    out = _run_driver("eq-return-check", driver)
    assert len(captured_return["tasks"]) == len(out["tasks"])
