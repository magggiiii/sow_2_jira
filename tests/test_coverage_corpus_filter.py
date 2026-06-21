# tests/test_coverage_corpus_filter.py
"""
C-4 / STEP 3.5: the embedding-tier coverage corpus filter.

The structural C-4 move (run-wide, post-dedup gate) stopped the BLANKET flagging.
This filter is what actually drops the real-run INCOMPLETE rate: it compares each
reported missed_item against the FINAL deduped task corpus by cosine similarity and
tiers it — drop >= 0.85 (already covered by a task extracted elsewhere; the
cross-section duplicate), likely_overlap 0.70-0.85 (advisory only), uncovered < 0.70
(a genuine miss that drives INCOMPLETE). A report whose genuine (uncovered) misses
are all filtered away no longer flags its section.

Determinism: a FakeEmbedder maps known text -> fixed unit vectors, so similarity is
exact and no real MiniLM is ever loaded in the suite. The orchestrator integration
tests inject the fake via the orch.coverage_filter_embed_fn seam + the env flag.
"""

from __future__ import annotations

import numpy as np

from pipeline.features.coverage_filter import (
    CoverageCorpusFilter,
    corpus_text,
    miss_text,
)
from models.schemas import (
    JiraHierarchy,
    LLMMode,
    ManagedTask,
    RunConfig,
    SourceRef,
    TaskFlag,
)


# ─── deterministic fake embedder ──────────────────────────────────────────────


def _unit(components: dict[int, float], dim: int) -> np.ndarray:
    """A unit vector with the given axis components (the rest zero)."""
    v = np.zeros(dim, dtype=np.float64)
    for i, c in components.items():
        v[i] = c
    n = np.linalg.norm(v)
    return v / n if n else v


def toward(axis: int, sim: float, dim: int, noise_axis: int) -> np.ndarray:
    """Unit vector with EXACT cosine ``sim`` to basis ``axis`` and 0 to the others
    (the remainder goes on a dedicated noise axis no corpus task uses)."""
    rem = max(0.0, 1.0 - sim * sim) ** 0.5
    v = np.zeros(dim, dtype=np.float64)
    v[axis] = sim
    v[noise_axis] = rem
    return v


class FakeEmbedder:
    """Maps exact text -> a fixed unit vector. Unmapped text -> the noise axis
    (cosine 0 to every corpus axis). Records calls so tests can assert MiniLM was
    never touched."""

    def __init__(self, mapping: dict[str, np.ndarray], dim: int):
        self.mapping = mapping
        self.dim = dim
        self.calls = 0

    def _default(self) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float64)
        v[self.dim - 1] = 1.0
        return v

    def encode(self, texts, normalize_embeddings=True):
        self.calls += 1
        return np.array([self.mapping.get(t, self._default()) for t in texts], dtype=np.float64)


# ─── builders ─────────────────────────────────────────────────────────────────


def _task(title: str, desc: str, *node_ids: str) -> ManagedTask:
    return ManagedTask(
        title=title,
        short_description=desc,
        confidence=0.9,
        source_refs=[
            SourceRef(node_id=n, section_title=n, page_start=1, page_end=1)
            for n in (node_ids or ("n0",))
        ],
    )


def _report(node_id: str, *descriptions: str, conf: float = 0.9) -> dict:
    return {
        "node_id": node_id,
        "extracted_count": 1,
        "checker_confidence": conf,
        "missed_items": [
            {"description": d, "confidence": conf, "reason": "r"} for d in descriptions
        ],
    }


# ─── filter unit tests (drop / overlap / uncovered tiers) ─────────────────────


def test_drop_at_high_similarity_removes_report():
    # corpus task "A x" on axis 0; the miss is 0.95-similar to it -> drop.
    dim = 2
    mapping = {"A x": _unit({0: 1.0}, dim), "alpha dup": toward(0, 0.95, dim, noise_axis=1)}
    embed = FakeEmbedder(mapping, dim).encode

    tasks = [_task("A", "x", "n1")]
    reports = {"n1": _report("n1", "alpha dup")}

    surviving, audit = CoverageCorpusFilter(embed).filter_reports(reports, tasks)

    assert "n1" not in surviving                       # all misses dropped -> report gone
    assert audit[0].dropped == 1 and audit[0].uncovered == 0
    assert audit[0].tiers[0].tier == "drop"


def test_uncovered_survives():
    dim = 2
    mapping = {"A x": _unit({0: 1.0}, dim), "gamma new": toward(0, 0.50, dim, noise_axis=1)}
    embed = FakeEmbedder(mapping, dim).encode

    tasks = [_task("A", "x", "n1")]
    reports = {"n1": _report("n1", "gamma new")}

    surviving, audit = CoverageCorpusFilter(embed).filter_reports(reports, tasks)

    assert "n1" in surviving
    assert [m["description"] for m in surviving["n1"]["missed_items"]] == ["gamma new"]
    assert audit[0].uncovered == 1 and audit[0].tiers[0].tier == "uncovered"


def test_likely_overlap_is_advisory_only_not_kept():
    dim = 2
    mapping = {"A x": _unit({0: 1.0}, dim), "overlap": toward(0, 0.75, dim, noise_axis=1)}
    embed = FakeEmbedder(mapping, dim).encode

    tasks = [_task("A", "x", "n1")]
    reports = {"n1": _report("n1", "overlap")}

    surviving, audit = CoverageCorpusFilter(embed).filter_reports(reports, tasks)

    assert "n1" not in surviving                       # likely_overlap is NOT a genuine miss
    assert audit[0].likely_overlap == 1 and audit[0].uncovered == 0
    assert audit[0].tiers[0].tier == "likely_overlap"


def test_boundary_0_85_is_drop_and_0_70_is_likely_overlap():
    dim = 3
    mapping = {
        "A x": _unit({0: 1.0}, dim),
        "B y": _unit({1: 1.0}, dim),
        "at85": toward(0, 0.85, dim, noise_axis=2),
        "at70": toward(1, 0.70, dim, noise_axis=2),
    }
    embed = FakeEmbedder(mapping, dim).encode
    tasks = [_task("A", "x", "n1"), _task("B", "y", "n2")]

    _, audit_drop = CoverageCorpusFilter(embed).filter_reports({"n1": _report("n1", "at85")}, tasks)
    _, audit_olap = CoverageCorpusFilter(embed).filter_reports({"n2": _report("n2", "at70")}, tasks)

    assert audit_drop[0].tiers[0].tier == "drop"           # 0.85 inclusive -> drop
    assert audit_olap[0].tiers[0].tier == "likely_overlap"  # 0.70 inclusive -> likely_overlap


def test_mixed_tiers_single_report_keeps_only_uncovered():
    dim = 2
    mapping = {
        "A x": _unit({0: 1.0}, dim),
        "hi": toward(0, 0.90, dim, noise_axis=1),
        "mid": toward(0, 0.75, dim, noise_axis=1),
        "lo": toward(0, 0.55, dim, noise_axis=1),
    }
    embed = FakeEmbedder(mapping, dim).encode
    tasks = [_task("A", "x", "n1")]
    reports = {"n1": _report("n1", "hi", "mid", "lo")}

    surviving, audit = CoverageCorpusFilter(embed).filter_reports(reports, tasks)

    assert [m["description"] for m in surviving["n1"]["missed_items"]] == ["lo"]
    cf = surviving["n1"]["_corpus_filter"]
    assert cf == {"total_before": 3, "dropped": 1, "likely_overlap": 1, "uncovered": 1}


def test_empty_corpus_all_uncovered_and_corpus_not_embedded():
    dim = 2
    fake = FakeEmbedder({}, dim)
    tasks: list = []
    reports = {"n1": _report("n1", "anything")}

    surviving, audit = CoverageCorpusFilter(fake.encode).filter_reports(reports, tasks)

    assert "n1" in surviving                                # nothing to match against -> survives
    assert audit[0].tiers[0].tier == "uncovered"
    assert audit[0].tiers[0].max_similarity == 0.0
    assert audit[0].tiers[0].best_task_index == -1
    assert fake.calls == 0                                  # no embedding at all (corpus empty)


def test_empty_miss_text_is_uncovered_and_not_embedded():
    dim = 2
    fake = FakeEmbedder({"A x": _unit({0: 1.0}, dim)}, dim)
    tasks = [_task("A", "x", "n1")]
    reports = {"n1": _report("n1", "", "   ")}            # blank + whitespace descriptions

    surviving, audit = CoverageCorpusFilter(fake.encode).filter_reports(reports, tasks)

    assert "n1" in surviving                                # blank misses can't be safely dropped
    assert all(t.tier == "uncovered" for t in audit[0].tiers)
    # only the corpus was embedded; the (zero) embeddable miss texts were not batched
    assert fake.calls == 1


def test_checker_confidence_not_recomputed():
    dim = 2
    mapping = {"A x": _unit({0: 1.0}, dim), "lo": toward(0, 0.5, dim, noise_axis=1)}
    embed = FakeEmbedder(mapping, dim).encode
    tasks = [_task("A", "x", "n1")]
    reports = {"n1": _report("n1", "lo", conf=0.83)}

    surviving, _ = CoverageCorpusFilter(embed).filter_reports(reports, tasks)

    assert surviving["n1"]["checker_confidence"] == 0.83    # untouched


def test_filter_is_idempotent():
    dim = 2
    mapping = {"A x": _unit({0: 1.0}, dim), "lo": toward(0, 0.5, dim, noise_axis=1)}
    embed = FakeEmbedder(mapping, dim).encode
    tasks = [_task("A", "x", "n1")]
    reports = {"n1": _report("n1", "lo")}

    once, _ = CoverageCorpusFilter(embed).filter_reports(reports, tasks)
    twice, _ = CoverageCorpusFilter(embed).filter_reports(once, tasks)

    assert [m["description"] for m in twice["n1"]["missed_items"]] == ["lo"]


def test_corpus_text_and_miss_text_assembly():
    t = _task("Build login", "Authenticate via email " * 40, "n1")  # long desc -> capped at 200
    text = corpus_text(t)
    assert text.startswith("Build login Authenticate via email")
    assert len(text) <= len("Build login ") + 200
    # dict-form task also works (getattr/_read tolerant)
    assert corpus_text({"title": "T", "short_description": "d"}) == "T d"
    assert miss_text({"description": "  a miss  "}) == "a miss"


# ─── orchestrator integration (the enabled path; still no MiniLM) ─────────────


def _make_orch():
    config = RunConfig(
        run_id="cov-filter-test", sow_pdf_path="/tmp/x.pdf",
        llm_mode=LLMMode.CUSTOM, jira_hierarchy=JiraHierarchy.EPIC_TASK,
        jira_project_key="TEST",
    )
    app_config = {"pipeline": {"max_gap_recovery_iterations": 1, "max_section_chars": 16000}}

    class _LLM:
        def complete(self, *a, **k): return ""
        def complete_json(self, *a, **k): return []

    class _Audit:
        def log(self, **k): return None

    from pipeline.orchestrator import PipelineOrchestrator
    orch = PipelineOrchestrator(config=config, app_config=app_config, audit=_Audit(), llm=_LLM())
    orch.classifier = None
    orch.critic = None
    return orch


def test_run_coverage_verify_filters_then_gates_when_enabled(monkeypatch):
    monkeypatch.setenv("SOW_COVERAGE_CORPUS_FILTER", "1")
    dim = 3
    mapping = {
        "Alpha aaa": _unit({0: 1.0}, dim),
        "Beta bbb": _unit({1: 1.0}, dim),
        "alpha dup": toward(0, 0.95, dim, noise_axis=2),  # matches the Alpha task -> drop
        "gamma new": toward(0, 0.50, dim, noise_axis=2),  # uncovered
    }
    fake = FakeEmbedder(mapping, dim)

    orch = _make_orch()
    orch.coverage_filter_embed_fn = fake.encode           # DI seam — never loads MiniLM

    task_a = _task("Alpha", "aaa", "n1")
    task_b = _task("Beta", "bbb", "n2")
    orch.section_coverage_reports = {
        "n1": _report("n1", "alpha dup"),
        "n2": _report("n2", "gamma new"),
    }

    result = orch._run_coverage_verify([task_a, task_b])

    assert TaskFlag.INCOMPLETE not in task_a.flags        # n1 miss was a cross-section dup -> dropped
    assert TaskFlag.INCOMPLETE in task_b.flags            # n2 miss is genuinely uncovered
    assert result.flagged_task_count == 1
    assert fake.calls > 0                                  # the fake (not MiniLM) did the work


def test_run_coverage_verify_disabled_by_default_is_byte_identical():
    # No env flag set -> filter skipped -> a high-sim miss STILL flags (legacy behavior).
    orch = _make_orch()
    # An embed_fn that would drop everything if it ran; it must NOT run when disabled.
    orch.coverage_filter_embed_fn = lambda texts: np.ones((len(texts), 2), dtype=np.float64)
    task = _task("Alpha", "aaa", "n1")
    orch.section_coverage_reports = {"n1": _report("n1", "alpha dup")}

    result = orch._run_coverage_verify([task])

    assert TaskFlag.INCOMPLETE in task.flags
    assert result.flagged_task_count == 1


def test_coverage_embed_fn_prefers_override_and_skips_when_disabled():
    orch = _make_orch()
    sentinel = lambda texts: np.zeros((len(texts), 2))
    orch.coverage_filter_embed_fn = sentinel
    assert orch._coverage_embed_fn() is sentinel          # override wins over the dedup embedder
    # And: a real dedup _get_embedder must NOT be called when the filter is disabled.
    import pipeline.agents.deduplication as dedup_mod

    def _boom(self):
        raise AssertionError("MiniLM must not load when the corpus filter is disabled")

    orch.coverage_filter_embed_fn = None
    object.__setattr__(orch.dedup_agent, "_get_embedder", lambda: _boom(orch.dedup_agent))
    # disabled by default -> _run_coverage_verify must not touch the embedder
    orch.section_coverage_reports = {"n1": _report("n1", "x")}
    orch._run_coverage_verify([_task("A", "x", "n1")])    # would raise if it loaded MiniLM
