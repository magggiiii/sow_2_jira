# tests/test_orchestrator_resume.py
"""
C1: per-node checkpoint + resume.

A long run (50-60pg SOW, ~25 min-2 h) only had a single end-of-run checkpoint —
a crash at node 45/60 lost everything. C1 writes an extraction checkpoint after
each applied node (atomic) and resumes from it on the next run: done nodes are
skipped, their tasks + coverage restored, and the remaining nodes processed —
with no duplicate tasks. A checkpoint whose node-set no longer matches the
current document triggers a fresh run.
"""

from __future__ import annotations

from types import SimpleNamespace

from pipeline.orchestrator import PipelineOrchestrator
from pipeline.coverage import CoverageTracker
from models.schemas import (
    RunConfig, LLMMode, JiraHierarchy, ManagedTask, TaskStatus,
)


# ─── Fakes ────────────────────────────────────────────────────────────────────


class FakeLLM:
    def complete(self, *a, **k):
        return ""

    def complete_json(self, *a, **k):
        return []


class FakeAudit:
    def log(self, **kwargs):
        return None


class FakeIndexer:
    def get_node_text(self, node):
        return node.get("text", "")


class FakeExtraction:
    def extract(self, node, section_text, hierarchy=None, status_callback=None):
        return [{"node": node["node_id"]}]


class FakeState:
    """Promotes each raw task to a REAL ManagedTask (so checkpoints serialize),
    titled with its source node id for duplicate detection."""

    def process(self, raw_tasks, open_tasks, node):
        closed = [
            ManagedTask(
                title=r["node"], short_description=f"from {r['node']}",
                confidence=0.9, status=TaskStatus.CLOSED,
            )
            for r in raw_tasks
        ]
        return open_tasks, closed

    def close_all_remaining(self, open_tasks):
        return list(open_tasks)


def _make_orchestrator(tmp_path, run_id="resume-test"):
    config = RunConfig(
        run_id=run_id,
        sow_pdf_path="/tmp/x.pdf",
        llm_mode=LLMMode.CUSTOM,
        jira_hierarchy=JiraHierarchy.EPIC_TASK,
        jira_project_key="TEST",
    )
    app_config = {"pipeline": {"max_gap_recovery_iterations": 1, "max_section_chars": 16000}}
    orch = PipelineOrchestrator(
        config=config, app_config=app_config, audit=FakeAudit(), llm=FakeLLM()
    )
    orch.classifier = None
    orch.critic = None
    orch.coverage_checker = None
    orch.indexer = FakeIndexer()
    orch.extraction_agent = FakeExtraction()
    orch.state_agent = FakeState()
    # Keep checkpoints out of the real data/ tree.
    orch._cp_file = tmp_path / "extraction_checkpoint.json"
    orch._extraction_checkpoint_path = lambda: orch._cp_file
    return orch


def _nodes(n):
    return [{"node_id": f"n{i}", "title": f"N{i}", "text": "x" * 200} for i in range(n)]


# ─── CoverageTracker serialization ────────────────────────────────────────────


def test_coverage_tracker_to_dict_round_trip():
    nodes = _nodes(4)
    cov = CoverageTracker(nodes)
    cov.mark_covered("n0", "t1")
    cov.mark_covered("n0", "t2")
    cov.mark_covered("n2", "t3")

    data = cov.to_dict()

    fresh = CoverageTracker(nodes)
    fresh.restore_from(data)

    assert fresh.coverage_report() == cov.coverage_report()
    assert fresh.coverage_report()["covered_nodes"] == 2


# ─── RunConfig.enable_resumption ──────────────────────────────────────────────


def test_run_config_enable_resumption_defaults_on():
    cfg = RunConfig(
        sow_pdf_path="/tmp/x.pdf", llm_mode=LLMMode.CUSTOM,
        jira_hierarchy=JiraHierarchy.EPIC_TASK, jira_project_key="T",
    )
    assert cfg.enable_resumption is True


# ─── Checkpoint + resume round-trip ───────────────────────────────────────────


def test_resume_skips_done_nodes_no_duplicate_tasks(tmp_path):
    nodes = _nodes(6)

    # Simulate a crash after node 2: 3 tasks done, coverage marked for n0-n2.
    orch1 = _make_orchestrator(tmp_path)
    done_tasks = [
        ManagedTask(title=f"n{i}", short_description="x", confidence=0.9, status=TaskStatus.CLOSED)
        for i in range(3)
    ]
    cov1 = CoverageTracker(nodes)
    for i in range(3):
        cov1.mark_covered(f"n{i}", str(done_tasks[i].id))
    orch1._write_extraction_checkpoint(nodes, 2, done_tasks, [], cov1)

    # New run resumes from the checkpoint.
    orch2 = _make_orchestrator(tmp_path)
    cov2 = CoverageTracker(nodes)
    start_index, seeded_closed, seeded_open = orch2._maybe_resume(nodes, cov2)

    assert start_index == 3
    assert {t.title for t in seeded_closed} == {"n0", "n1", "n2"}
    assert cov2.coverage_report()["covered_nodes"] == 3

    final_closed, _, cancelled = orch2._extract_all(
        nodes, cov2, start_index=start_index,
        all_closed_tasks=seeded_closed, open_tasks=seeded_open,
    )

    assert cancelled is False
    # All six nodes covered exactly once — no duplicates from the resumed prefix.
    assert sorted(t.title for t in final_closed) == [f"n{i}" for i in range(6)]
    assert cov2.coverage_report()["covered_nodes"] == 6


def test_resume_restores_section_coverage_reports(tmp_path):
    """C-4: coverage flagging is now post-dedup off ``section_coverage_reports``,
    so a resumed run must restore the reports for already-processed nodes — else
    the run-wide gate would under-flag the pre-crash sections (a regression vs the
    old inline path, where INCOMPLETE rode along on the persisted tasks)."""
    nodes = _nodes(4)
    orch1 = _make_orchestrator(tmp_path)
    reports = {
        "n0": {
            "node_id": "n0",
            "extracted_count": 1,
            "checker_confidence": 0.9,
            "missed_items": [{"description": "m", "confidence": 0.9, "reason": "r"}],
        }
    }
    orch1.section_coverage_reports = dict(reports)
    cov1 = CoverageTracker(nodes)
    cov1.mark_covered("n0", "t")
    task = ManagedTask(title="n0", short_description="x", confidence=0.9, status=TaskStatus.CLOSED)
    orch1._write_extraction_checkpoint(nodes, 0, [task], [], cov1)

    orch2 = _make_orchestrator(tmp_path)
    assert orch2.section_coverage_reports == {}  # fresh instance starts empty
    orch2._maybe_resume(nodes, CoverageTracker(nodes))

    assert orch2.section_coverage_reports == reports


def test_resume_ignored_when_node_set_changed(tmp_path):
    """A checkpoint whose node-set no longer matches the document is discarded
    (fresh run from index 0) rather than corrupting the resume."""
    orig_nodes = _nodes(4)
    orch1 = _make_orchestrator(tmp_path)
    cov1 = CoverageTracker(orig_nodes)
    cov1.mark_covered("n0", "t")
    task = ManagedTask(title="n0", short_description="x", confidence=0.9, status=TaskStatus.CLOSED)
    orch1._write_extraction_checkpoint(orig_nodes, 0, [task], [], cov1)

    # Document changed: different node ids.
    new_nodes = [{"node_id": f"m{i}", "title": f"M{i}", "text": "x" * 200} for i in range(4)]
    orch2 = _make_orchestrator(tmp_path)
    cov2 = CoverageTracker(new_nodes)
    start_index, seeded_closed, seeded_open = orch2._maybe_resume(new_nodes, cov2)

    assert start_index == 0
    assert not seeded_closed
    assert cov2.coverage_report()["covered_nodes"] == 0


def test_resume_disabled_ignores_checkpoint(tmp_path):
    nodes = _nodes(4)
    orch1 = _make_orchestrator(tmp_path)
    cov1 = CoverageTracker(nodes)
    cov1.mark_covered("n0", "t")
    task = ManagedTask(title="n0", short_description="x", confidence=0.9, status=TaskStatus.CLOSED)
    orch1._write_extraction_checkpoint(nodes, 0, [task], [], cov1)

    orch2 = _make_orchestrator(tmp_path)
    orch2.config.enable_resumption = False
    cov2 = CoverageTracker(nodes)
    start_index, seeded_closed, _ = orch2._maybe_resume(nodes, cov2)

    assert start_index == 0
    assert not seeded_closed


def test_checkpoint_deleted_on_completion(tmp_path):
    nodes = _nodes(2)
    orch = _make_orchestrator(tmp_path)
    cov = CoverageTracker(nodes)
    task = ManagedTask(title="n0", short_description="x", confidence=0.9, status=TaskStatus.CLOSED)
    orch._write_extraction_checkpoint(nodes, 0, [task], [], cov)
    assert orch._extraction_checkpoint_path().exists()

    orch._delete_extraction_checkpoint()
    assert not orch._extraction_checkpoint_path().exists()
