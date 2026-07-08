# pipeline/features/coverage_filter.py
"""
C-4 / STEP 3.5: the embedding-tier coverage corpus filter.

The structural C-4 move (the run-wide, post-dedup ``CoverageGate``) stopped the
BLANKET per-section INCOMPLETE flagging. This filter is what actually drops the
real-run INCOMPLETE rate: it compares each reported missed_item against the FINAL
deduped task corpus by cosine similarity and tiers it (AUDIT.md:67-71 C-4
elevation):

    sim >= DROP_THRESHOLD (0.85)          -> drop            (already covered by a
                                                              task extracted elsewhere
                                                              — the cross-section dup)
    OVERLAP_THRESHOLD (0.70) <= sim < 0.85 -> likely_overlap  (advisory only — does NOT
                                                              by itself flag INCOMPLETE)
    sim < 0.70                             -> uncovered       (a genuine miss; drives
                                                              the section's INCOMPLETE flag)

Only ``uncovered`` misses survive into ``report["missed_items"]``; a report whose
uncovered misses are all filtered away is dropped from the report set entirely, so
``CoverageGate.report_flags`` returns False for that section. ``checker_confidence``
is left exactly as the checker produced it — the filter only changes the miss count,
keeping the gate's confidence contract stable.

This is a FEATURE-stage component (numpy-dependent), deliberately NOT in numpy-free
``core/``. It is embedder-agnostic: it takes an ``embed_fn`` callable (the same
``SentenceTransformer.encode(normalize_embeddings=True)`` the dedup agent uses, or a
deterministic fake in tests), so it loads no model itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, List, Literal, Mapping, Tuple

import numpy as np

# Thresholds match AUDIT.md:71 and the dedup agent's 0.85 default. Env-tunable
# overrides are a later step (out of scope here).
DROP_THRESHOLD = 0.85
OVERLAP_THRESHOLD = 0.70

# (texts) -> ndarray (N, D), L2-normalized — i.e. SentenceTransformer.encode(
# normalize_embeddings=True). Cosine similarity is then a plain dot product.
EmbedFn = Callable[[List[str]], "np.ndarray"]

_Tier = Literal["drop", "likely_overlap", "uncovered"]


def _read(obj: Any, key: str, default: Any = None) -> Any:
    """Read ``key`` from a dict or an object — so reports/tasks work as either
    serialized dicts or live Pydantic models."""
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


@dataclass(frozen=True)
class MissTier:
    """The filter's verdict for one reported missed_item."""

    description: str
    tier: _Tier
    max_similarity: float
    best_task_index: int  # index into corpus_tasks of the closest task; -1 if none


@dataclass(frozen=True)
class CorpusFilterReport:
    """Per-section advisory summary of the filter pass."""

    node_id: str
    total_before: int
    dropped: int
    likely_overlap: int
    uncovered: int
    tiers: List[MissTier] = field(default_factory=list)


def corpus_text(task: Any) -> str:
    """Identity text for a task — EXACT reuse of the dedup agent's
    ``_get_embedding_texts`` (title + first 200 chars of description)."""
    title = _read(task, "title", "") or ""
    desc = (_read(task, "short_description", "") or "")[:200]
    return f"{title} {desc}".strip()


def miss_text(miss: Any) -> str:
    return (_read(miss, "description", "") or "").strip()


class CoverageCorpusFilter:
    """Tier reported misses against the final corpus; keep only genuine (uncovered) ones."""

    def __init__(
        self,
        embed_fn: EmbedFn,
        drop_threshold: float = DROP_THRESHOLD,
        overlap_threshold: float = OVERLAP_THRESHOLD,
    ) -> None:
        self.embed_fn = embed_fn
        self.drop_threshold = float(drop_threshold)
        self.overlap_threshold = float(overlap_threshold)

    def _tier(self, max_sim: float) -> _Tier:
        if max_sim >= self.drop_threshold:
            return "drop"
        if max_sim >= self.overlap_threshold:
            return "likely_overlap"
        return "uncovered"

    def filter_reports(
        self,
        reports: Mapping[str, dict],
        corpus_tasks: list,
    ) -> Tuple[dict, List[CorpusFilterReport]]:
        """Return ``(surviving_reports, audit)``.

        ``surviving_reports`` keeps only sections with at least one uncovered miss;
        each surviving report keeps only its uncovered missed_items and gains a
        ``_corpus_filter`` advisory key (ignored by the gate). ``checker_confidence``
        is untouched. Idempotent.
        """
        # Embed the corpus ONCE (reused across every report). Empty corpus -> there
        # is nothing to match against, so every miss is uncovered.
        corpus_texts = [corpus_text(t) for t in corpus_tasks]
        corpus_vecs = self.embed_fn(corpus_texts) if corpus_texts else None

        surviving: dict = {}
        audit: List[CorpusFilterReport] = []

        for node_id, report in reports.items():
            misses = list(_read(report, "missed_items", []) or [])
            texts = [miss_text(m) for m in misses]

            # Batch-embed only the non-empty miss texts (a blank description can't be
            # safely matched, so it stays uncovered without an embedding).
            embeddable = [(i, t) for i, t in enumerate(texts) if t]
            sims_by_i: dict[int, tuple[float, int]] = {}
            if corpus_vecs is not None and embeddable:
                mvecs = self.embed_fn([t for _, t in embeddable])  # (k, D)
                sims = mvecs @ corpus_vecs.T                        # (k, N), cosine
                for row, (i, _) in enumerate(embeddable):
                    sims_by_i[i] = (float(sims[row].max()), int(sims[row].argmax()))

            tiers: List[MissTier] = []
            kept: list = []
            for i, miss in enumerate(misses):
                if i in sims_by_i:
                    max_sim, best = sims_by_i[i]
                    tier = self._tier(max_sim)
                else:
                    # No corpus, or empty miss text -> conservatively uncovered.
                    max_sim, best, tier = 0.0, -1, "uncovered"
                tiers.append(MissTier(_read(miss, "description", "") or "", tier, max_sim, best))
                if tier == "uncovered":
                    kept.append(miss)

            dropped = sum(1 for t in tiers if t.tier == "drop")
            overlap = sum(1 for t in tiers if t.tier == "likely_overlap")
            audit.append(
                CorpusFilterReport(
                    node_id=_read(report, "node_id", "") or "",
                    total_before=len(misses),
                    dropped=dropped,
                    likely_overlap=overlap,
                    uncovered=len(kept),
                    tiers=tiers,
                )
            )

            if kept:
                new_report = dict(report)
                new_report["missed_items"] = kept
                new_report["_corpus_filter"] = {
                    "total_before": len(misses),
                    "dropped": dropped,
                    "likely_overlap": overlap,
                    "uncovered": len(kept),
                }
                surviving[node_id] = new_report
            # else: every miss was drop/likely_overlap -> section no longer flags.

        return surviving, audit


__all__ = [
    "CoverageCorpusFilter",
    "CorpusFilterReport",
    "MissTier",
    "corpus_text",
    "miss_text",
    "DROP_THRESHOLD",
    "OVERLAP_THRESHOLD",
    "EmbedFn",
]
