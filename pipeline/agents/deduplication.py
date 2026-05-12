# pipeline/agents/deduplication.py

import json
from pathlib import Path
from typing import Optional

import numpy as np
from sentence_transformers import SentenceTransformer
from sklearn.neighbors import NearestNeighbors

from models.schemas import ManagedTask, TaskStatus, TaskFlag, DedupDecision
from pipeline.agents.state import _merge_acceptance_criteria, _merge_dependencies
from pipeline.agents.cross_run_index import ProjectEmbeddingIndex
from pipeline.llm_client import LLMClient
from audit.logger import AuditLogger

DEDUP_SYSTEM_PROMPT = "You are a precise task deduplication agent. Return ONLY valid JSON."

DEDUP_PROMPT_TEMPLATE = """You are reviewing pairs of extracted tasks from a Statement of Work for duplication.

Two tasks are duplicates if they describe the SAME piece of work, even if worded differently.
Two tasks are NOT duplicates if they describe different aspects of similar work.

For each pair, decide:
- "merge": they are the same work item — the first should absorb the second
- "keep_both": they are distinct work items
- "keep_first": the second is a subset of the first — drop the second
- "keep_second": the first is a subset of the second — drop the first

Return ONLY a valid JSON array:
[
  {{
    "task_id_a": "uuid-string",
    "task_id_b": "uuid-string",
    "decision": "merge" | "keep_both" | "keep_first" | "keep_second",
    "reason": "one sentence explanation"
  }}
]

Pairs to review:
{pairs_json}
"""


class DeduplicationAgent:

    EMBED_MODEL = "all-MiniLM-L6-v2"  # Small, fast, runs on CPU

    def __init__(
        self,
        llm_client: LLMClient,
        audit_logger: AuditLogger,
        run_id: str,
        similarity_threshold: float = 0.85,
        project_key: Optional[str] = None,
        cross_run_threshold: Optional[float] = None,
        sessions_dir: str = "data/sessions",
        project_indices_dir: str = "data/project_indices",
    ):
        """
        Args:
            project_key: If provided, enables the project-level cross-run
                index. When None (default), the agent behaves exactly like
                the legacy intra-run-only dedup — no cross-run search, no
                index writes. The orchestrator passes
                `RunConfig.jira_project_key` here once Wave 3 wires it.
            cross_run_threshold: Cosine threshold for cross-run matches.
                Defaults to similarity_threshold; can be set higher to make
                cross-run flagging more conservative than intra-run merge.
            sessions_dir / project_indices_dir: On-disk locations. Overridable
                so tests don't write into the real data/ tree.
        """
        self.llm = llm_client
        self.audit = audit_logger
        self.run_id = run_id
        self.threshold = similarity_threshold
        self.project_key = project_key
        self.cross_run_threshold = (
            cross_run_threshold if cross_run_threshold is not None else similarity_threshold
        )
        self.sessions_dir = Path(sessions_dir)
        self.project_indices_dir = Path(project_indices_dir)
        self._embedder = None  # Lazy load

    def _get_embedder(self) -> SentenceTransformer:
        if self._embedder is None:
            self._embedder = SentenceTransformer(self.EMBED_MODEL)
        return self._embedder

    def _get_embedding_texts(self, tasks: list[ManagedTask]) -> list[str]:
        """
        Combine title and description for better duplicate detection.
        - Title alone misses duplicates where titles differ slightly but descriptions match
        - Cap description at 200 chars to keep vector focused on task identity
        """
        texts = []
        for t in tasks:
            desc_snippet = (t.short_description or "")[:200]
            text = f"{t.title} {desc_snippet}".strip()
            texts.append(text)
        return texts

    # ─── Embedding persistence ─────────────────────────────────────────────

    def _persist_embeddings(
        self, tasks: list[ManagedTask], embeddings: np.ndarray
    ) -> Optional[Path]:
        """
        Save current-run embeddings to data/sessions/<run_id>/embeddings.npz
        so downstream tooling (cross-run index, evals, debugging) can replay
        them without recomputing. Best-effort; IO failure is logged, not raised.
        """
        try:
            run_dir = self.sessions_dir / self.run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            out_path = run_dir / "embeddings.npz"
            task_ids = np.array([str(t.id) for t in tasks], dtype=object)
            np.savez_compressed(
                str(out_path),
                embeddings=embeddings.astype(np.float32),
                task_ids=task_ids,
            )
            return out_path
        except Exception as e:  # pragma: no cover - defensive
            self.audit.log(
                run_id=self.run_id,
                agent="DeduplicationAgent",
                action="EMBEDDING_PERSIST_FAILED",
                detail=str(e),
            )
            return None

    # ─── Candidate-pair search (sklearn NN, replaces the O(n^2) loop) ──────

    def _find_candidate_pairs(
        self,
        tasks: list[ManagedTask],
        embeddings: np.ndarray,
    ) -> list[tuple[ManagedTask, ManagedTask, float]]:
        """
        Use sklearn NearestNeighbors with cosine distance to find pairs whose
        similarity is at or above threshold. For L2-normalized embeddings
        (which SentenceTransformer.encode(normalize_embeddings=True) returns),
        cosine_distance = 1 - cosine_similarity, so the radius for
        radius_neighbors is (1 - threshold).

        Returns pairs as (task_i, task_j, similarity) with i < j, identical
        in shape to the previous brute-force loop's output.
        """
        n = len(tasks)
        if n < 2:
            return []

        # Tiny floating-point cushion so a similarity of exactly `threshold`
        # is included (radius_neighbors uses strict less-than on the upper
        # bound on some sklearn versions). Equivalent to the legacy `>=`.
        radius = max(0.0, (1.0 - self.threshold) + 1e-9)
        nn = NearestNeighbors(metric="cosine", algorithm="brute", radius=radius)
        nn.fit(embeddings)
        distances, indices = nn.radius_neighbors(embeddings, return_distance=True)

        seen: set[tuple[int, int]] = set()
        pairs: list[tuple[ManagedTask, ManagedTask, float]] = []
        for i in range(n):
            for dist, j in zip(distances[i], indices[i]):
                if i == j:
                    continue
                lo, hi = (i, j) if i < j else (j, i)
                if (lo, hi) in seen:
                    continue
                seen.add((lo, hi))
                similarity = 1.0 - float(dist)
                # Defensive: ensure threshold semantic matches legacy `>=`
                if similarity + 1e-9 < self.threshold:
                    continue
                pairs.append((tasks[lo], tasks[hi], similarity))
        return pairs

    # ─── Cross-run match handling ──────────────────────────────────────────

    def _apply_cross_run_matches(
        self,
        remaining_tasks: list[ManagedTask],
        embeddings_after_dedup: np.ndarray,
    ) -> list[ManagedTask]:
        """
        For each remaining (post intra-run dedup) task, search the project
        index for prior-run matches and FLAG them as POTENTIAL_DUPLICATE.
        Cross-run merge is intentionally NOT performed automatically — that
        decision lives with a human reviewer who can compare against the
        prior Jira issue.

        After flagging, append this run's embeddings to the index so future
        runs can match against it. We pass exclude_run_id=self.run_id so the
        search is safe even if a previous interrupted run already wrote our
        run_id into the index.
        """
        if not self.project_key or not remaining_tasks:
            return remaining_tasks

        try:
            index = ProjectEmbeddingIndex(
                project_key=self.project_key,
                base_dir=str(self.project_indices_dir),
            )
        except Exception as e:  # pragma: no cover - defensive
            self.audit.log(
                run_id=self.run_id,
                agent="DeduplicationAgent",
                action="CROSS_RUN_INDEX_ERROR",
                detail=f"Could not open index for project={self.project_key}: {e}",
            )
            return remaining_tasks

        task_ids = [str(t.id) for t in remaining_tasks]
        try:
            matches = index.search(
                embeddings=embeddings_after_dedup,
                task_ids=task_ids,
                threshold=self.cross_run_threshold,
                exclude_run_id=self.run_id,
            )
        except Exception as e:  # pragma: no cover - defensive
            self.audit.log(
                run_id=self.run_id,
                agent="DeduplicationAgent",
                action="CROSS_RUN_SEARCH_ERROR",
                detail=str(e),
            )
            matches = []

        if matches:
            task_map = {str(t.id): t for t in remaining_tasks}
            for m in matches:
                t = task_map.get(m.task_id)
                if t is None:
                    continue
                if TaskFlag.POTENTIAL_DUPLICATE not in t.flags:
                    t.flags.append(TaskFlag.POTENTIAL_DUPLICATE)
                detail = (
                    f"sim={m.similarity:.3f} prior_run={m.prior_run_id} "
                    f"prior_task={m.prior_task_id} prior_jira={m.prior_jira_key or 'none'} "
                    f"prior_title={m.prior_title!r}"
                )
                self.audit.log(
                    run_id=self.run_id,
                    agent="DeduplicationAgent",
                    action="CROSS_RUN_MATCH",
                    task_id=m.task_id,
                    detail=detail,
                )

        # Append this run's embeddings to the project index for future runs.
        try:
            index.add_run(self.run_id, remaining_tasks, embeddings_after_dedup)
            self.audit.log(
                run_id=self.run_id,
                agent="DeduplicationAgent",
                action="PROJECT_INDEX_UPDATED",
                detail=f"Added {len(remaining_tasks)} tasks to project={self.project_key}",
            )
        except Exception as e:  # pragma: no cover - defensive
            self.audit.log(
                run_id=self.run_id,
                agent="DeduplicationAgent",
                action="PROJECT_INDEX_WRITE_FAILED",
                detail=str(e),
            )

        return remaining_tasks

    # ─── Public entrypoint ─────────────────────────────────────────────────

    def deduplicate(self, tasks: list[ManagedTask]) -> list[ManagedTask]:
        """
        1. Embed all task titles + descriptions
        2. Find pairs with cosine similarity >= threshold (sklearn NearestNeighbors)
        3. Send candidate pairs to LLM for final decision
        4. Apply decisions (merge / drop)
        5. Persist embeddings to data/sessions/<run_id>/embeddings.npz
        6. If project_key set: cross-run search + flag + add_run to project index
        Returns cleaned task list.
        """
        if len(tasks) < 2:
            # Still persist embeddings if we have at least one task — useful
            # for evals and resumption. Skipped if zero tasks.
            if tasks:
                embedder = self._get_embedder()
                texts = self._get_embedding_texts(tasks)
                embeddings = embedder.encode(texts, normalize_embeddings=True)
                self._persist_embeddings(tasks, embeddings)
                if self.project_key:
                    self._apply_cross_run_matches(tasks, embeddings)
            return tasks

        # Step 1: Embed texts
        embedder = self._get_embedder()
        texts = self._get_embedding_texts(tasks)
        embeddings = embedder.encode(texts, normalize_embeddings=True)

        # Step 2: Find candidate pairs (sklearn NearestNeighbors, cosine metric)
        candidate_pairs = self._find_candidate_pairs(tasks, embeddings)

        if not candidate_pairs:
            self.audit.log(
                run_id=self.run_id,
                agent="DeduplicationAgent",
                action="NO_DUPLICATES_FOUND",
                detail=f"No pairs above threshold {self.threshold}",
            )
            self._persist_embeddings(tasks, embeddings)
            if self.project_key:
                self._apply_cross_run_matches(tasks, embeddings)
            return tasks

        # Step 3: LLM confirmation for candidate pairs
        pairs_json = json.dumps([
            {
                "task_id_a": str(a.id),
                "title_a": a.title,
                "description_a": a.short_description,
                "task_id_b": str(b.id),
                "title_b": b.title,
                "description_b": b.short_description,
                "similarity_score": round(sim, 3),
            }
            for a, b, sim in candidate_pairs
        ], indent=2)

        try:
            raw_decisions = self.llm.complete_json(
                prompt=DEDUP_PROMPT_TEMPLATE.format(pairs_json=pairs_json),
                system=DEDUP_SYSTEM_PROMPT,
                agent_name="DeduplicationAgent",
            )
        except (ValueError, RuntimeError) as e:
            self.audit.log(
                run_id=self.run_id,
                agent="DeduplicationAgent",
                action="DEDUP_ERROR",
                detail=str(e),
            )
            # Fail safe: persist embeddings, then return all tasks unmodified
            self._persist_embeddings(tasks, embeddings)
            if self.project_key:
                self._apply_cross_run_matches(tasks, embeddings)
            return tasks

        # Step 4: Apply decisions
        decisions = [DedupDecision(**d) for d in raw_decisions if isinstance(d, dict)]
        drop_ids: set[str] = set()
        task_map = {str(t.id): t for t in tasks}

        for decision in decisions:
            self.audit.log(
                run_id=self.run_id,
                agent="DeduplicationAgent",
                action=f"DEDUP_{decision.decision.upper()}",
                detail=f"{decision.task_id_a} vs {decision.task_id_b}: {decision.reason}",
            )

            if decision.decision == "merge":
                # Merge B into A
                task_a = task_map.get(decision.task_id_a)
                task_b = task_map.get(decision.task_id_b)
                if task_a and task_b:
                    task_a = self._merge_tasks(task_a, task_b)
                    task_a.flags = list(set(task_a.flags))
                    if TaskFlag.POTENTIAL_DUPLICATE not in task_a.flags:
                        pass  # merged successfully, no flag needed
                    drop_ids.add(decision.task_id_b)
                    task_b.status = TaskStatus.MERGED
                    task_b.merged_from = [task_a.id]

            elif decision.decision == "keep_first":
                drop_ids.add(decision.task_id_b)
                if decision.task_id_b in task_map:
                    task_map[decision.task_id_b].status = TaskStatus.MERGED

            elif decision.decision == "keep_second":
                drop_ids.add(decision.task_id_a)
                if decision.task_id_a in task_map:
                    task_map[decision.task_id_a].status = TaskStatus.MERGED

            # "keep_both" → no action

        result = [t for t in tasks if str(t.id) not in drop_ids]

        # Step 5: persist embeddings (full set, ordering matches `tasks`).
        # We keep ALL rows here — replay tooling can re-derive the kept set
        # from task ids. Cross-run flow slices below.
        self._persist_embeddings(tasks, embeddings)

        # Step 6: cross-run search + index update (only if project_key set)
        if self.project_key and result:
            # Rebuild embedding array aligned with `result` ordering.
            id_to_idx = {str(t.id): i for i, t in enumerate(tasks)}
            keep_idx = [id_to_idx[str(t.id)] for t in result]
            result_embeddings = embeddings[keep_idx]
            self._apply_cross_run_matches(result, result_embeddings)

        self.audit.log(
            run_id=self.run_id,
            agent="DeduplicationAgent",
            action="DEDUP_COMPLETE",
            detail=f"Started: {len(tasks)}, After dedup: {len(result)}, Removed: {len(tasks) - len(result)}",
        )
        return result

    def _merge_tasks(self, a: ManagedTask, b: ManagedTask) -> ManagedTask:
        """Merge task B content into task A. Returns A."""
        # Acceptance criteria are AcceptanceCriterion objects — use the
        # condition-aware merger (set() would dedup by object identity only).
        if not a.acceptance_criteria and b.acceptance_criteria:
            a.acceptance_criteria = b.acceptance_criteria
        elif a.acceptance_criteria and b.acceptance_criteria:
            a.acceptance_criteria = _merge_acceptance_criteria(
                a.acceptance_criteria, b.acceptance_criteria
            )

        if not a.use_case and b.use_case:
            a.use_case = b.use_case

        if not a.mockup_prototype and b.mockup_prototype:
            a.mockup_prototype = b.mockup_prototype

        if b.considerations_constraints:
            a.considerations_constraints = list(set(
                (a.considerations_constraints or []) + b.considerations_constraints
            ))

        if b.deliverables:
            a.deliverables = list(set(
                (a.deliverables or []) + b.deliverables
            ))

        # Combine dependencies (dedup by target_ref + kind)
        if b.dependencies:
            a.dependencies = _merge_dependencies(a.dependencies, b.dependencies)

        # Merge source refs
        a.source_refs.extend(b.source_refs)
        a.merged_from.append(b.id)
        a.confidence = max(a.confidence, b.confidence)

        import datetime
        a.updated_at = datetime.datetime.utcnow()
        return a
