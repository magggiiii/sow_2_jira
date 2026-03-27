# pipeline/agents/deduplication.py

import json
import numpy as np
from sentence_transformers import SentenceTransformer
from models.schemas import ManagedTask, TaskStatus, TaskFlag, DedupDecision
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
    ):
        self.llm = llm_client
        self.audit = audit_logger
        self.run_id = run_id
        self.threshold = similarity_threshold
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

    def deduplicate(self, tasks: list[ManagedTask]) -> list[ManagedTask]:
        """
        1. Embed all task titles + descriptions
        2. Find pairs with cosine similarity >= threshold
        3. Send candidate pairs to LLM for final decision
        4. Apply decisions (merge / drop)
        Returns cleaned task list.
        """
        if len(tasks) < 2:
            return tasks

        # Step 1: Embed texts
        embedder = self._get_embedder()
        texts = self._get_embedding_texts(tasks)
        embeddings = embedder.encode(texts, normalize_embeddings=True)

        # Step 2: Find candidate pairs
        candidate_pairs = []
        for i in range(len(tasks)):
            for j in range(i + 1, len(tasks)):
                similarity = float(np.dot(embeddings[i], embeddings[j]))
                if similarity >= self.threshold:
                    candidate_pairs.append((tasks[i], tasks[j], similarity))

        if not candidate_pairs:
            self.audit.log(
                run_id=self.run_id,
                agent="DeduplicationAgent",
                action="NO_DUPLICATES_FOUND",
                detail=f"No pairs above threshold {self.threshold}",
            )
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
            return tasks  # Fail safe: return all tasks unmodified

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
        self.audit.log(
            run_id=self.run_id,
            agent="DeduplicationAgent",
            action="DEDUP_COMPLETE",
            detail=f"Started: {len(tasks)}, After dedup: {len(result)}, Removed: {len(tasks) - len(result)}",
        )
        return result

    def _merge_tasks(self, a: ManagedTask, b: ManagedTask) -> ManagedTask:
        """Merge task B content into task A. Returns A."""
        # Prefer non-null fields from B if A is missing them
        if not a.acceptance_criteria and b.acceptance_criteria:
            a.acceptance_criteria = b.acceptance_criteria
        elif a.acceptance_criteria and b.acceptance_criteria:
            a.acceptance_criteria = list(set(a.acceptance_criteria + b.acceptance_criteria))

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

        # Merge source refs
        a.source_refs.extend(b.source_refs)
        a.merged_from.append(b.id)
        a.confidence = max(a.confidence, b.confidence)

        import datetime
        a.updated_at = datetime.datetime.utcnow()
        return a