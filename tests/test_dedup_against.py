# tests/test_dedup_against.py
"""orch-3: DeduplicationAgent.dedup_against — scoped recovered-vs-existing dedup.

Gap recovery used to trigger a FULL re-dedup of ``existing + recovered`` (every
already-surviving task re-embedded and re-compared against every other survivor).
That is wasteful and can spuriously re-merge survivors that a prior dedup pass
deliberately kept apart. ``dedup_against(recovered, existing)`` scopes the work:
ONLY the newly-recovered tasks are considered for merge/drop against the frozen
survivor set. Survivors are returned untouched — never re-compared to each other.
"""

from __future__ import annotations

import pathlib
import sys
from unittest.mock import MagicMock
from uuid import uuid4

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from models.schemas import (
    DedupDecision,
    ManagedTask,
    SourceRef,
    TaskStatus,
)
from pipeline.agents.deduplication import DedupDecisionList, DeduplicationAgent


class _DummyAudit:
    def __init__(self):
        self.records: list[dict] = []

    def log(self, **kwargs):
        self.records.append(kwargs)


def _src(node_id: str = "n1") -> SourceRef:
    return SourceRef(node_id=node_id, section_title="Section", page_start=1, page_end=1)


def _task(title: str, desc: str = "") -> ManagedTask:
    return ManagedTask(
        id=uuid4(),
        title=title,
        short_description=desc,
        confidence=0.9,
        status=TaskStatus.CLOSED,
        source_refs=[_src()],
    )


def _make_agent(
    tmp_path: pathlib.Path, threshold: float = 0.85
) -> tuple[DeduplicationAgent, _DummyAudit]:
    audit = _DummyAudit()
    agent = DeduplicationAgent(
        llm_client=MagicMock(),
        audit_logger=audit,
        run_id="run-dedup-against",
        similarity_threshold=threshold,
        sessions_dir=str(tmp_path / "sessions"),
        project_indices_dir=str(tmp_path / "project_indices"),
    )
    return agent, audit


class _FakeEmbedder:
    """Deterministic embedder: maps each text to a fixed unit vector by lookup,
    so cosine similarities are fully controlled by the test."""

    def __init__(self, vectors: dict[str, np.ndarray]):
        self.vectors = vectors

    def encode(self, texts, normalize_embeddings=True):
        out = []
        for t in texts:
            v = self.vectors[t].astype(np.float32)
            if normalize_embeddings:
                v = v / (np.linalg.norm(v) or 1.0)
            out.append(v)
        return np.array(out, dtype=np.float32)


def _install_embedder(agent: DeduplicationAgent, tasks_to_vec: dict[str, np.ndarray]):
    """Map each task's embedding text -> vector and install a fake embedder."""
    text_to_vec = {}
    for t_text, vec in tasks_to_vec.items():
        text_to_vec[t_text] = vec
    agent._embedder = _FakeEmbedder(text_to_vec)


def _emb_text(t: ManagedTask) -> str:
    desc_snippet = (t.short_description or "")[:200]
    return f"{t.title} {desc_snippet}".strip()


def test_dedup_against_drops_recovered_duplicate_of_existing(tmp_path):
    """A recovered task that duplicates a survivor is dropped; survivors are
    returned unchanged and untouched."""
    agent, audit = _make_agent(tmp_path)

    e1 = _task("Build login API")
    e2 = _task("Design dashboard")
    r1 = _task("Implement login API")  # near-duplicate of e1

    existing = [e1, e2]
    recovered = [r1]

    # e1 and r1 collide (same vector); e2 is orthogonal.
    vecs = {
        _emb_text(e1): np.array([1.0, 0.0, 0.0]),
        _emb_text(e2): np.array([0.0, 1.0, 0.0]),
        _emb_text(r1): np.array([1.0, 0.0, 0.0]),
    }
    _install_embedder(agent, vecs)

    # LLM confirms the recovered r1 merges into the existing e1.
    stub = MagicMock(
        return_value=DedupDecisionList(
            decisions=[
                DedupDecision(
                    task_id_a=str(e1.id),
                    task_id_b=str(r1.id),
                    decision="merge",
                    reason="same task",
                )
            ]
        )
    )
    agent.runner.complete_structured = stub

    result = agent.dedup_against(recovered, existing)

    result_ids = {str(t.id) for t in result}
    # Both survivors kept; recovered duplicate dropped.
    assert str(e1.id) in result_ids
    assert str(e2.id) in result_ids
    assert str(r1.id) not in result_ids
    assert len(result) == 2


def test_dedup_against_does_not_recompare_survivors(tmp_path):
    """Two survivors that are near-duplicates of each other must NOT be merged:
    dedup_against only judges recovered-vs-existing, never existing-vs-existing."""
    agent, audit = _make_agent(tmp_path)

    # e1 and e2 are near-identical survivors a prior pass deliberately kept.
    e1 = _task("Configure CI pipeline")
    e2 = _task("Configure CI pipeline")  # collides with e1
    r1 = _task("Write onboarding docs")  # orthogonal recovered task, no dup

    existing = [e1, e2]
    recovered = [r1]

    vecs = {
        _emb_text(e1): np.array([1.0, 0.0, 0.0]),
        _emb_text(e2): np.array([1.0, 0.0, 0.0]),  # identical to e1
        _emb_text(r1): np.array([0.0, 1.0, 0.0]),
    }
    _install_embedder(agent, vecs)

    stub = MagicMock(return_value=DedupDecisionList(decisions=[]))
    agent.runner.complete_structured = stub

    result = agent.dedup_against(recovered, existing)

    # No survivor-vs-survivor pair may ever be offered to the LLM.
    for call in stub.call_args_list:
        # pairs_json is passed in the render context; inspect all string args.
        payload = "".join(str(a) for a in call.args) + "".join(
            str(v) for v in call.kwargs.values()
        )
        assert not (str(e1.id) in payload and str(e2.id) in payload), (
            "survivor-vs-survivor pair was compared — full re-dedup leaked in"
        )

    # All three tasks survive (survivors untouched, recovered had no dup).
    result_ids = {str(t.id) for t in result}
    assert result_ids == {str(e1.id), str(e2.id), str(r1.id)}
