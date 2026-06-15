# pipeline/evals/harness.py
"""
INV-4 offline eval harness.

Drives the REAL ``PipelineOrchestrator.run()`` fully offline against a frozen
golden document tree + a generated cassette, then evaluates the saved
``pipeline_output.json`` against the INV-4 bands. No network, no PageIndex, no
real LLM — the LLM is replaced at the ``AgentRunner.complete_structured`` seam.

Determinism (the parts the scout flagged as fragile, solved here):
- ``SOW_NODE_CONCURRENCY=1`` so node order and cassette consumption are fixed.
- The cassette is generated PER NODE from the golden tree (this module owns the
  node_ids), so there's no need to trace the orchestrator's call sequence.
- Dedup's candidate-pair formation is frozen (``_find_candidate_pairs`` patched
  to return the first two tasks), and the dedup reply is built from those exact
  runtime task ids via closure-shared state — so a real merge happens
  deterministically without depending on the MiniLM embedder or parsing prompts.

This is the "scaffolding-now" gate (hand-authored cassette on the current
pipeline). The faithful recorded 103-node cassette is deferred until the C-4
post-dedup coverage restructure (STEP 3.3) lands.
"""

from __future__ import annotations

import json
import os
import shutil
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from unittest import mock

from core.agent_runner import AgentRunner
from models.schemas import (
    DedupDecision, JiraHierarchy, LLMMode, ProviderConfig, RunConfig,
)
from pipeline.agents.deduplication import DeduplicationAgent, DedupDecisionList
from pipeline.evals.bands import EvalBands, EvalResult, compute_bands
from pipeline.evals.cassette import Cassette
from pipeline.evals.replay import NoOpProvider, make_structured_replay


class _NoOpAudit:
    """No-op audit sink — band metrics come from pipeline_output.json, not audit."""

    def log(self, **kwargs):
        return None


def golden_tree(n: int = 4) -> list[dict]:
    """A frozen hierarchical document_tree.json with exactly the keys
    ``DocumentIndexer.flatten_tree`` requires."""
    return [
        {
            "node_id": f"node-{i}",
            "title": f"Section {i}",
            "start_index": i,
            "end_index": i,
            "text": f"Section {i} requires the team to build and deliver component {i}. " * 4,
            "summary": f"Build component {i}.",
        }
        for i in range(n)
    ]


def build_cassette(nodes, *, extraction_conf: float = 0.8, coverage_missed: bool = False) -> Cassette:
    """Generate a per-node cassette covering every (agent, node_id) call the run
    makes. ``extraction_conf=0.0`` reproduces the critic conf=0.00 flag-bomb;
    ``coverage_missed=True`` reproduces the 100%-INCOMPLETE bomb."""
    entries: list[dict] = []
    for node in nodes:
        nid = node["node_id"]
        entries.append({"agent": "SectionClassifier", "node_id": nid,
                        "response": {"node_id": nid, "type": "actionable",
                                     "confidence": 0.9, "reason": "lists deliverables"}})
        entries.append({"agent": "ExtractionAgent", "node_id": nid,
                        "response": {"scratchpad": "", "tasks": [{
                            "title": f"Build {nid}",
                            "short_description": f"Implement deliverable for {nid}",
                            "confidence": extraction_conf,
                            "flags": [], "continues_to_next": False, "dependencies": [],
                        }]}})
        entries.append({"agent": "TaskCritic", "node_id": nid,
                        "response": {"critiques": []}})
        missed = ([{"description": f"missed deliverable in {nid}",
                    "confidence": 0.9, "reason": "an actionable item was dropped"}]
                  if coverage_missed else [])
        entries.append({"agent": "CoverageChecker", "node_id": nid,
                        "response": {"missed_items": missed}})
    return Cassette.from_dict({"entries": entries})


@dataclass
class EvalHarness:
    """Drive a deterministic offline run and evaluate it against the bands."""

    run_id: str = "inv4-eval-gate"
    nodes: list = None
    extraction_conf: float = 0.8
    coverage_missed: bool = False
    dedup_merges: bool = True
    bands: EvalBands = field(default_factory=EvalBands)

    def __post_init__(self):
        if self.nodes is None:
            self.nodes = golden_tree()

    def _session_dir(self) -> Path:
        return Path(f"data/sessions/{self.run_id}")

    def run(self) -> EvalResult:
        from pipeline.orchestrator import PipelineOrchestrator

        sess = self._session_dir()
        if sess.exists():
            shutil.rmtree(sess)
        sess.mkdir(parents=True, exist_ok=True)
        (sess / "document_tree.json").write_text(json.dumps(self.nodes))

        cassette = build_cassette(
            self.nodes, extraction_conf=self.extraction_conf, coverage_missed=self.coverage_missed
        )
        cassette_replay = make_structured_replay(cassette)
        captured = {"pair": None}
        merges = self.dedup_merges

        def fake_find_pairs(agent_self, tasks, embeddings):
            # Freeze the candidate pair so a merge is deterministic without MiniLM.
            if len(tasks) >= 2:
                captured["pair"] = (str(tasks[0].id), str(tasks[1].id))
                return [(tasks[0], tasks[1], 0.95)]
            captured["pair"] = None
            return []

        def replay_or_dedup(runner_self, *, prompt, response_model, system=None,
                            max_tokens=None, agent_name=None, node_id=None):
            if agent_name == "DeduplicationAgent":
                if merges and captured["pair"]:
                    a, b = captured["pair"]
                    return DedupDecisionList(decisions=[DedupDecision(
                        task_id_a=a, task_id_b=b, decision="merge", reason="eval merge")])
                return DedupDecisionList(decisions=[])  # keep_both → zero merges
            return cassette_replay(
                runner_self, prompt=prompt, response_model=response_model,
                system=system, max_tokens=max_tokens, agent_name=agent_name, node_id=node_id,
            )

        config = RunConfig(
            run_id=self.run_id, sow_pdf_path="/tmp/eval.pdf",
            llm_mode=LLMMode.CUSTOM, jira_hierarchy=JiraHierarchy.EPIC_TASK,
            jira_project_key="",  # "" → no cross-run dedup index
            skip_indexing=True, enable_resumption=False,
            provider_config=ProviderConfig(provider="replay", model="replay/none"),
        )
        app_config = {"pipeline": {"max_gap_recovery_iterations": 1, "max_section_chars": 16000}}

        prev = os.environ.get("SOW_NODE_CONCURRENCY")
        os.environ["SOW_NODE_CONCURRENCY"] = "1"
        try:
            with ExitStack() as stack:
                stack.enter_context(mock.patch.object(AgentRunner, "complete_structured", replay_or_dedup))
                stack.enter_context(mock.patch.object(DeduplicationAgent, "_find_candidate_pairs", fake_find_pairs))
                orch = PipelineOrchestrator(
                    config=config, app_config=app_config, audit=_NoOpAudit(), llm=NoOpProvider(),
                )
                orch.run()
            output = json.loads((sess / "pipeline_output.json").read_text())
            return compute_bands(output, bands=self.bands)
        finally:
            if prev is None:
                os.environ.pop("SOW_NODE_CONCURRENCY", None)
            else:
                os.environ["SOW_NODE_CONCURRENCY"] = prev
            if sess.exists():
                shutil.rmtree(sess)


__all__ = ["EvalHarness", "golden_tree", "build_cassette"]
