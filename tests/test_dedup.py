# tests/test_dedup.py
"""
Wave 2D coverage for the DeduplicationAgent rewrite.

Three things under test:
1. sklearn NearestNeighbors candidate-pair search returns the same set of
   pairs as the legacy O(n^2) brute-force loop on identical embeddings.
2. When no pair clears threshold, no LLM call happens and tasks are returned
   unchanged — but embeddings are still persisted to data/sessions/<run_id>/.
3. LLM errors during dedup are swallowed (audit-logged) and tasks come back
   unmodified, again with the embeddings file present.
"""

from __future__ import annotations

import pathlib
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from models.schemas import (
    AcceptanceCriterion,
    ManagedTask,
    SourceRef,
    TaskStatus,
)
from pipeline.agents.deduplication import DeduplicationAgent


# ─── Helpers ────────────────────────────────────────────────────────────────


class _DummyAudit:
    def __init__(self):
        self.records: list[dict] = []

    def log(self, **kwargs):
        self.records.append(kwargs)

    def actions(self) -> list[str]:
        return [r.get("action", "") for r in self.records]


def _src(node_id: str = "n1") -> SourceRef:
    return SourceRef(
        node_id=node_id,
        section_title="Section",
        page_start=1,
        page_end=1,
    )


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
    tmp_path: pathlib.Path,
    llm: MagicMock | None = None,
    threshold: float = 0.85,
    project_key: str | None = None,
) -> tuple[DeduplicationAgent, _DummyAudit, MagicMock]:
    llm = llm or MagicMock()
    audit = _DummyAudit()
    agent = DeduplicationAgent(
        llm_client=llm,
        audit_logger=audit,
        run_id="run-test-2d",
        similarity_threshold=threshold,
        project_key=project_key,
        sessions_dir=str(tmp_path / "sessions"),
        project_indices_dir=str(tmp_path / "project_indices"),
    )
    return agent, audit, llm


def _brute_force_pairs(
    tasks: list[ManagedTask], embeddings: np.ndarray, threshold: float
) -> set[frozenset[str]]:
    """Reference implementation matching the pre-2D dedup loop."""
    pairs: set[frozenset[str]] = set()
    for i in range(len(tasks)):
        for j in range(i + 1, len(tasks)):
            sim = float(np.dot(embeddings[i], embeddings[j]))
            if sim >= threshold:
                pairs.add(frozenset((str(tasks[i].id), str(tasks[j].id))))
    return pairs


# ─── Tests ──────────────────────────────────────────────────────────────────


def test_dedup_same_results_as_legacy_brute_force(tmp_path):
    """
    sklearn NearestNeighbors path must yield the same candidate pairs as the
    old O(n^2) np.dot loop on a small hand-crafted fixture. The fixture has
    one obvious duplicate cluster and one unrelated outlier, so the only
    pair >= threshold should be (t1, t2).
    """
    t1 = _task("Build login screen", "User logs in with email and password")
    t2 = _task("Implement login UI", "Allow user to authenticate via email and password")
    t3 = _task("Configure observability stack", "Wire OTel collector to Tempo")
    tasks = [t1, t2, t3]

    agent, audit, llm = _make_agent(tmp_path, threshold=0.6)

    embedder = agent._get_embedder()
    embeddings = embedder.encode(
        agent._get_embedding_texts(tasks),
        normalize_embeddings=True,
    )

    expected = _brute_force_pairs(tasks, np.asarray(embeddings), threshold=0.6)
    nn_pairs = agent._find_candidate_pairs(tasks, np.asarray(embeddings))
    actual = {frozenset((str(a.id), str(b.id))) for a, b, _ in nn_pairs}

    # The fixture should produce at least one pair so the comparison is
    # meaningful (otherwise both sides would be trivially empty).
    assert expected, "fixture should contain at least one duplicate pair"
    assert actual == expected

    # Similarities returned by the NN path should be >= threshold and within
    # 1e-6 of np.dot for normalized vectors.
    for a, b, sim in nn_pairs:
        i = tasks.index(a)
        j = tasks.index(b)
        legacy_sim = float(np.dot(embeddings[i], embeddings[j]))
        assert sim >= 0.6 - 1e-9
        assert abs(sim - legacy_sim) < 1e-6


def test_dedup_below_threshold_no_pairs(tmp_path):
    """
    When no pair clears threshold, the LLM is never called, tasks are
    returned untouched, and the audit log records NO_DUPLICATES_FOUND.
    The embeddings file is still persisted.
    """
    t1 = _task("Build login screen")
    t2 = _task("Migrate billing database")
    t3 = _task("Set up CI pipeline")
    tasks = [t1, t2, t3]

    llm = MagicMock()
    agent, audit, _ = _make_agent(tmp_path, llm=llm, threshold=0.99)
    out = agent.deduplicate(tasks)

    # Behavior contract: identity preserved, no LLM call, audit recorded
    assert len(out) == 3
    assert {str(x.id) for x in out} == {str(x.id) for x in tasks}
    llm.complete_json.assert_not_called()
    assert "NO_DUPLICATES_FOUND" in audit.actions()

    # Embeddings file persisted
    npz_path = tmp_path / "sessions" / "run-test-2d" / "embeddings.npz"
    assert npz_path.exists()


def test_dedup_persists_embeddings_npz(tmp_path):
    """
    After dedup, data/sessions/<run_id>/embeddings.npz exists, holds a
    (N, 384) float32 array, and the task_ids array matches the task list
    in order. Covers the happy-path persist branch.
    """
    t1 = _task("Onboarding checklist", "List of steps a new hire must complete")
    t2 = _task("Configure CI build", "GitHub Actions workflow that runs pytest")
    tasks = [t1, t2]

    llm = MagicMock()
    # Force no candidate pairs so we don't have to fake an LLM response
    agent, _, _ = _make_agent(tmp_path, llm=llm, threshold=0.99)
    agent.deduplicate(tasks)

    npz_path = tmp_path / "sessions" / "run-test-2d" / "embeddings.npz"
    assert npz_path.exists()

    with np.load(npz_path, allow_pickle=True) as data:
        emb = data["embeddings"]
        ids = data["task_ids"]

    assert emb.dtype == np.float32
    assert emb.ndim == 2
    assert emb.shape == (2, 384)
    assert list(ids) == [str(t1.id), str(t2.id)]


def test_dedup_with_llm_error_returns_unmodified(tmp_path):
    """
    When the LLM raises during the confirmation step, dedup audits the error
    and returns tasks unchanged. The embeddings file is still written so
    a retry can reuse them.
    """
    # Two near-duplicate tasks so we definitely hit the LLM path
    t1 = _task("Build login screen", "User logs in with email and password")
    t2 = _task("Build login page", "User logs in with email and password")
    tasks = [t1, t2]

    llm = MagicMock()
    llm.complete_json.side_effect = ValueError("simulated parse error")

    agent, audit, _ = _make_agent(tmp_path, llm=llm, threshold=0.6)
    out = agent.deduplicate(tasks)

    assert llm.complete_json.called
    assert len(out) == 2
    assert {str(x.id) for x in out} == {str(x.id) for x in tasks}
    assert "DEDUP_ERROR" in audit.actions()

    npz_path = tmp_path / "sessions" / "run-test-2d" / "embeddings.npz"
    assert npz_path.exists()


def test_dedup_no_project_key_skips_cross_run_index(tmp_path):
    """
    With project_key=None (default) the project index is never touched even
    if a similar prior run wrote to the same base_dir. This guards the
    backward-compat contract: existing callers pre-Wave-3 must see no
    change in behavior.
    """
    t1 = _task("Sole task A")
    tasks = [t1]

    agent, audit, _ = _make_agent(tmp_path, project_key=None)
    agent.deduplicate(tasks)

    # No project index directory should be created
    assert not (tmp_path / "project_indices").exists()
    assert "CROSS_RUN_MATCH" not in audit.actions()
    assert "PROJECT_INDEX_UPDATED" not in audit.actions()
