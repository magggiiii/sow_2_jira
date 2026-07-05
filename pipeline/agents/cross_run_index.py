# pipeline/agents/cross_run_index.py
#
# Project-scoped embedding store used by DeduplicationAgent to detect
# cross-run duplicates. Persists embeddings per Jira project key, so two SOWs
# pushed to the same project a month apart can flag overlapping tasks even
# though no single pipeline run sees both.
#
# The index is intentionally simple (npz + manifest.json), not FAISS or a
# real vector DB. Project-level task volume is small (hundreds, not millions)
# and we want zero new server dependencies.

from __future__ import annotations

import json
import os
import datetime
from pathlib import Path
from typing import Optional

import numpy as np
from pydantic import BaseModel
from sklearn.neighbors import NearestNeighbors

from models.schemas import ManagedTask


INDEX_VERSION = 1
EMBED_DIM = 384  # all-MiniLM-L6-v2


class CrossRunMatch(BaseModel):
    """A current-run task that semantically matches a task from a prior run."""

    task_id: str                          # Current-run task id (string form of UUID)
    prior_task_id: str                    # Matched prior-run task id
    prior_run_id: str
    prior_jira_key: Optional[str] = None  # Jira key if the prior task was pushed
    similarity: float                     # Cosine similarity in [0, 1]
    prior_title: str                      # Human-readable title of the prior task


class ProjectEmbeddingIndex:
    """
    Project-scoped persistent embedding store keyed by jira_project_key.

    On-disk layout:
        <base_dir>/<project_key>/
            index.npz       — embeddings, task_ids, run_ids, jira_keys, titles
            manifest.json   — version, project_key, last_updated, task_count

    The npz arrays are stored as a single bundle so we can rewrite the whole
    file atomically on each update. For project-level scale (hundreds of
    tasks) the rewrite cost is negligible.
    """

    def __init__(self, project_key: str, base_dir: str = "data/project_indices"):
        if not project_key:
            raise ValueError("project_key is required for ProjectEmbeddingIndex")
        self.project_key = project_key
        self.base_dir = Path(base_dir)
        self.dir = self.base_dir / project_key
        self.index_path = self.dir / "index.npz"
        self.manifest_path = self.dir / "manifest.json"

    # ─── Persistence helpers ────────────────────────────────────────────────

    def _load(self) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """
        Return (embeddings, task_ids, run_ids, jira_keys, titles).
        Returns empty arrays of the right dtype if the index does not exist.
        """
        if not self.index_path.exists():
            return (
                np.zeros((0, EMBED_DIM), dtype=np.float32),
                np.array([], dtype=object),
                np.array([], dtype=object),
                np.array([], dtype=object),
                np.array([], dtype=object),
            )
        with np.load(self.index_path, allow_pickle=True) as data:
            embeddings = data["embeddings"].astype(np.float32)
            task_ids = data["task_ids"].astype(object)
            run_ids = data["run_ids"].astype(object)
            jira_keys = data["jira_keys"].astype(object)
            titles = data["titles"].astype(object)
        return embeddings, task_ids, run_ids, jira_keys, titles

    def _save(
        self,
        embeddings: np.ndarray,
        task_ids: np.ndarray,
        run_ids: np.ndarray,
        jira_keys: np.ndarray,
        titles: np.ndarray,
    ) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        # Write npz atomically by going through a temp file. np.savez_compressed
        # appends ".npz" automatically if missing, so use the directory path
        # plus stem to avoid double extension.
        tmp_stem = self.dir / (self.index_path.stem + ".tmp")
        np.savez_compressed(
            str(tmp_stem),
            embeddings=embeddings,
            task_ids=task_ids,
            run_ids=run_ids,
            jira_keys=jira_keys,
            titles=titles,
        )
        tmp_path = self.dir / (self.index_path.stem + ".tmp.npz")
        os.replace(tmp_path, self.index_path)

        manifest = {
            "version": INDEX_VERSION,
            "project_key": self.project_key,
            # tz-aware UTC (W1 1.3); isoformat now carries +00:00 (no manual "Z").
            "last_updated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "task_count": int(embeddings.shape[0]),
        }
        tmp_manifest = self.manifest_path.with_suffix(".json.tmp")
        tmp_manifest.write_text(json.dumps(manifest, indent=2))
        os.replace(tmp_manifest, self.manifest_path)

    # ─── Public API ─────────────────────────────────────────────────────────

    def add_run(
        self,
        run_id: str,
        tasks: list[ManagedTask],
        embeddings: np.ndarray,
    ) -> None:
        """
        Append a run's tasks and their embeddings to the project index.

        Embeddings must be a (len(tasks), EMBED_DIM) float array, ideally
        L2-normalized so cosine similarity matches the dedup agent's threshold
        without further scaling.

        Jira keys default to empty strings; call update_jira_keys() after the
        push pass to attach them.
        """
        if not tasks:
            return
        if embeddings.shape[0] != len(tasks):
            raise ValueError(
                f"embeddings rows ({embeddings.shape[0]}) must match task count ({len(tasks)})"
            )
        if embeddings.ndim != 2:
            raise ValueError(f"embeddings must be 2D, got shape {embeddings.shape}")

        existing_emb, existing_ids, existing_runs, existing_keys, existing_titles = self._load()

        new_emb = embeddings.astype(np.float32)
        new_ids = np.array([str(t.id) for t in tasks], dtype=object)
        new_runs = np.array([run_id] * len(tasks), dtype=object)
        new_keys = np.array([""] * len(tasks), dtype=object)
        new_titles = np.array([t.title for t in tasks], dtype=object)

        if existing_emb.shape[0] == 0:
            merged_emb = new_emb
            merged_ids = new_ids
            merged_runs = new_runs
            merged_keys = new_keys
            merged_titles = new_titles
        else:
            # Dimension safety: refuse to merge if embedding sizes differ
            if existing_emb.shape[1] != new_emb.shape[1]:
                raise ValueError(
                    f"embedding dim mismatch: index has {existing_emb.shape[1]}, "
                    f"incoming has {new_emb.shape[1]}"
                )
            merged_emb = np.vstack([existing_emb, new_emb])
            merged_ids = np.concatenate([existing_ids, new_ids])
            merged_runs = np.concatenate([existing_runs, new_runs])
            merged_keys = np.concatenate([existing_keys, new_keys])
            merged_titles = np.concatenate([existing_titles, new_titles])

        self._save(merged_emb, merged_ids, merged_runs, merged_keys, merged_titles)

    def search(
        self,
        embeddings: np.ndarray,
        task_ids: list[str],
        threshold: float = 0.85,
        exclude_run_id: Optional[str] = None,
    ) -> list[CrossRunMatch]:
        """
        For each query embedding, find the single best prior-run task above
        threshold (cosine similarity). Returns one CrossRunMatch per query
        that has a match; queries with no match are omitted.

        Set exclude_run_id to the current run_id to avoid self-matches when
        searching after add_run() has already been called for this run.
        """
        if embeddings.shape[0] == 0:
            return []
        if len(task_ids) != embeddings.shape[0]:
            raise ValueError(
                f"task_ids length ({len(task_ids)}) must match embedding rows ({embeddings.shape[0]})"
            )

        existing_emb, existing_ids, existing_runs, existing_keys, existing_titles = self._load()
        if existing_emb.shape[0] == 0:
            return []

        # Optionally drop rows from the excluded run before fitting NN.
        if exclude_run_id is not None:
            mask = existing_runs != exclude_run_id
            if not mask.any():
                return []
            corpus_emb = existing_emb[mask]
            corpus_ids = existing_ids[mask]
            corpus_runs = existing_runs[mask]
            corpus_keys = existing_keys[mask]
            corpus_titles = existing_titles[mask]
        else:
            corpus_emb = existing_emb
            corpus_ids = existing_ids
            corpus_runs = existing_runs
            corpus_keys = existing_keys
            corpus_titles = existing_titles

        if corpus_emb.shape[0] == 0:
            return []

        # sklearn NearestNeighbors with cosine distance. Distance = 1 - cos_sim
        # for L2-normalized vectors, so threshold maps to radius = 1 - threshold.
        nn = NearestNeighbors(metric="cosine", algorithm="brute")
        nn.fit(corpus_emb)
        # Use kneighbors(n=1) to find the single best prior match per query.
        # NearestNeighbors uses cosine distance; we filter by threshold afterward.
        n_query = embeddings.shape[0]
        distances, indices = nn.kneighbors(
            embeddings.astype(np.float32), n_neighbors=1
        )

        matches: list[CrossRunMatch] = []
        for q_idx in range(n_query):
            dist = float(distances[q_idx, 0])
            similarity = 1.0 - dist
            if similarity < threshold:
                continue
            corpus_idx = int(indices[q_idx, 0])
            prior_key = corpus_keys[corpus_idx]
            matches.append(
                CrossRunMatch(
                    task_id=str(task_ids[q_idx]),
                    prior_task_id=str(corpus_ids[corpus_idx]),
                    prior_run_id=str(corpus_runs[corpus_idx]),
                    prior_jira_key=(str(prior_key) if prior_key else None),
                    similarity=round(similarity, 4),
                    prior_title=str(corpus_titles[corpus_idx]),
                )
            )
        return matches

    def update_jira_keys(self, mapping: dict[str, str]) -> None:
        """
        After Jira push, attach jira_issue_key values to indexed tasks.
        `mapping` is {task_id: jira_key}. Unknown task_ids are silently ignored.
        """
        if not mapping:
            return
        embeddings, task_ids, run_ids, jira_keys, titles = self._load()
        if embeddings.shape[0] == 0:
            return

        changed = False
        for i, tid in enumerate(task_ids):
            key = mapping.get(str(tid))
            if key:
                jira_keys[i] = key
                changed = True

        if changed:
            self._save(embeddings, task_ids, run_ids, jira_keys, titles)
