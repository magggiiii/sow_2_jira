# tests/test_cross_run_index.py
"""
Wave 2D coverage for the project-scoped ProjectEmbeddingIndex.

Four things under test:
1. add_run() on a fresh project creates the npz + manifest files with the
   right shapes and task_count.
2. search() finds prior-run matches (and reports the prior run_id).
3. search() with exclude_run_id skips rows from the current run, so a run
   that already added itself to the index does not see self-matches.
4. update_jira_keys() attaches Jira keys to indexed tasks, and subsequent
   searches surface those keys in CrossRunMatch.prior_jira_key.

All tests use the pytest tmp_path fixture for the index base_dir — no
writes touch the real data/ tree.
"""

from __future__ import annotations

import json
import pathlib
import sys
from uuid import uuid4

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from models.schemas import ManagedTask, SourceRef, TaskStatus
from pipeline.agents.cross_run_index import (
    CrossRunMatch,
    EMBED_DIM,
    ProjectEmbeddingIndex,
)


# ─── Helpers ────────────────────────────────────────────────────────────────


def _src() -> SourceRef:
    return SourceRef(
        node_id="n1",
        section_title="Section",
        page_start=1,
        page_end=1,
    )


def _task(title: str) -> ManagedTask:
    return ManagedTask(
        id=uuid4(),
        title=title,
        short_description="",
        confidence=0.9,
        status=TaskStatus.CLOSED,
        source_refs=[_src()],
    )


def _unit_vec(seed: int) -> np.ndarray:
    """Stable, L2-normalized 384-dim vector for a given seed."""
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(EMBED_DIM).astype(np.float32)
    return v / np.linalg.norm(v)


def _embeddings_for(*seeds: int) -> np.ndarray:
    return np.vstack([_unit_vec(s) for s in seeds])


# ─── Tests ──────────────────────────────────────────────────────────────────


def test_index_creates_files_on_first_add(tmp_path):
    """
    add_run() on an empty project writes both index.npz and manifest.json
    with correct shapes and task_count, and the npz arrays match what we
    passed in.
    """
    idx = ProjectEmbeddingIndex(project_key="PROJ", base_dir=str(tmp_path))
    t1, t2 = _task("Login"), _task("Signup")
    emb = _embeddings_for(1, 2)

    idx.add_run(run_id="run-001", tasks=[t1, t2], embeddings=emb)

    project_dir = tmp_path / "PROJ"
    npz_path = project_dir / "index.npz"
    manifest_path = project_dir / "manifest.json"
    assert npz_path.exists()
    assert manifest_path.exists()

    manifest = json.loads(manifest_path.read_text())
    assert manifest["project_key"] == "PROJ"
    assert manifest["task_count"] == 2
    assert manifest["version"] == 1
    assert "last_updated" in manifest
    # W1 1.3: tz-aware isoformat carries +00:00 and no longer appends a manual
    # "Z" (which would have produced an invalid "+00:00Z" double-suffix).
    assert manifest["last_updated"].endswith("+00:00")
    assert not manifest["last_updated"].endswith("Z")

    with np.load(npz_path, allow_pickle=True) as data:
        assert data["embeddings"].shape == (2, EMBED_DIM)
        assert data["embeddings"].dtype == np.float32
        assert list(data["task_ids"]) == [str(t1.id), str(t2.id)]
        assert list(data["run_ids"]) == ["run-001", "run-001"]
        assert list(data["jira_keys"]) == ["", ""]
        assert list(data["titles"]) == ["Login", "Signup"]


def test_search_finds_prior_run_match(tmp_path):
    """
    Add run-A with two tasks, then search run-B's embeddings (one is the
    same vector as a run-A task). The duplicate should produce a
    CrossRunMatch carrying run-A's run_id and task_id; the unrelated task
    should not match.
    """
    idx = ProjectEmbeddingIndex(project_key="PROJ", base_dir=str(tmp_path))

    a1 = _task("Login screen")
    a2 = _task("Audit logs page")
    idx.add_run("run-A", [a1, a2], _embeddings_for(1, 2))

    # Run B: one duplicate (same vector as a1), one unique (new seed)
    b1 = _task("Sign-in page")  # same embedding as a1
    b2 = _task("Brand new feature")  # different embedding
    b_emb = np.vstack([_unit_vec(1), _unit_vec(99)])

    matches = idx.search(
        embeddings=b_emb,
        task_ids=[str(b1.id), str(b2.id)],
        threshold=0.9,
        exclude_run_id="run-B",
    )

    assert len(matches) == 1
    m = matches[0]
    assert m.task_id == str(b1.id)
    assert m.prior_task_id == str(a1.id)
    assert m.prior_run_id == "run-A"
    assert m.prior_title == "Login screen"
    assert m.prior_jira_key is None
    assert m.similarity > 0.99  # identical vector


def test_search_excludes_current_run(tmp_path):
    """
    If a run has already added itself to the index, searching with
    exclude_run_id=<that run_id> must not surface self-matches. This guards
    against double-counting when the deduplication agent calls search()
    after add_run() (or in a resumed/replayed pipeline).
    """
    idx = ProjectEmbeddingIndex(project_key="PROJ", base_dir=str(tmp_path))

    t1 = _task("Onboarding checklist")
    idx.add_run("run-A", [t1], _embeddings_for(7))

    # Same task, same embedding, same run — must NOT match itself
    matches = idx.search(
        embeddings=_embeddings_for(7),
        task_ids=[str(t1.id)],
        threshold=0.9,
        exclude_run_id="run-A",
    )
    assert matches == []

    # Without exclusion it should match (sanity check that the data is there)
    matches_no_exclusion = idx.search(
        embeddings=_embeddings_for(7),
        task_ids=[str(t1.id)],
        threshold=0.9,
        exclude_run_id=None,
    )
    assert len(matches_no_exclusion) == 1


def test_update_jira_keys(tmp_path):
    """
    After update_jira_keys({task_id: 'PROJ-42'}) the index persists the
    Jira key on the matching row, and subsequent searches return it via
    CrossRunMatch.prior_jira_key.
    """
    idx = ProjectEmbeddingIndex(project_key="PROJ", base_dir=str(tmp_path))
    t1 = _task("Login screen")
    t2 = _task("Logout button")
    idx.add_run("run-A", [t1, t2], _embeddings_for(1, 2))

    idx.update_jira_keys({str(t1.id): "PROJ-42"})

    # Reload and verify the row was rewritten
    npz_path = tmp_path / "PROJ" / "index.npz"
    with np.load(npz_path, allow_pickle=True) as data:
        ids = list(data["task_ids"])
        keys = list(data["jira_keys"])
    idx_of_t1 = ids.index(str(t1.id))
    idx_of_t2 = ids.index(str(t2.id))
    assert keys[idx_of_t1] == "PROJ-42"
    assert keys[idx_of_t2] == ""

    # Now search from a different run and confirm prior_jira_key propagates
    b1 = _task("Sign-in")
    matches = idx.search(
        embeddings=_embeddings_for(1),
        task_ids=[str(b1.id)],
        threshold=0.9,
        exclude_run_id="run-B",
    )
    assert len(matches) == 1
    assert matches[0].prior_jira_key == "PROJ-42"


def test_add_run_appends_across_runs(tmp_path):
    """
    Two separate add_run() calls should accumulate rows, and the manifest's
    task_count should reflect the running total. Ensures we don't overwrite
    the prior run on each save.
    """
    idx = ProjectEmbeddingIndex(project_key="PROJ", base_dir=str(tmp_path))
    a1 = _task("Task A1")
    a2 = _task("Task A2")
    idx.add_run("run-A", [a1, a2], _embeddings_for(1, 2))

    b1 = _task("Task B1")
    idx.add_run("run-B", [b1], _embeddings_for(3))

    npz_path = tmp_path / "PROJ" / "index.npz"
    with np.load(npz_path, allow_pickle=True) as data:
        assert data["embeddings"].shape == (3, EMBED_DIM)
        assert set(data["run_ids"]) == {"run-A", "run-B"}
        assert list(data["task_ids"]) == [str(a1.id), str(a2.id), str(b1.id)]

    manifest = json.loads((tmp_path / "PROJ" / "manifest.json").read_text())
    assert manifest["task_count"] == 3


def test_search_against_empty_index_returns_empty(tmp_path):
    """search() on a project that has never been written should return []."""
    idx = ProjectEmbeddingIndex(project_key="EMPTY", base_dir=str(tmp_path))
    matches = idx.search(
        embeddings=_embeddings_for(1),
        task_ids=["does-not-matter"],
        threshold=0.5,
        exclude_run_id=None,
    )
    assert matches == []


def test_project_key_required():
    """Empty/blank project_key must raise — caller wiring guard."""
    with pytest.raises(ValueError):
        ProjectEmbeddingIndex(project_key="", base_dir="/tmp")
