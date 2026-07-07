# tests/test_orchestrator_coverage_gate.py
"""
C-4 / STEP 3.3: coverage flagging moves out of the per-node loop and into a
run-wide, post-dedup gate.

Two behavioral changes, pinned here without PageIndex / LLM / network:

  A. ``_apply_node`` must NOT flag INCOMPLETE inline anymore (it still produces
     and stores the section coverage report for the gate to consume later).
  B. ``_run_coverage_verify`` applies INCOMPLETE to the FINAL task set,
     report-level — only tasks whose ``source_refs`` belong to a confident,
     missed section, never a blanket sweep.
"""

from __future__ import annotations

from models.schemas import (
    JiraHierarchy,
    LLMMode,
    ManagedTask,
    RunConfig,
    SourceRef,
    TaskFlag,
)
from pipeline.agents.coverage_check import MissedItem, SectionCoverageReport
from pipeline.orchestrator import PipelineOrchestrator

# ─── fakes ────────────────────────────────────────────────────────────────────


class FakeLLM:
    def complete(self, *a, **k):
        return ""

    def complete_json(self, *a, **k):
        return []


class FakeAudit:
    def __init__(self):
        self.entries: list[dict] = []

    def log(self, **kwargs):
        self.entries.append(kwargs)


class FakeIndexer:
    def get_node_text(self, node):
        return node.get("text", "")


class _FakeState:
    """Returns the supplied managed tasks as closed; no continuation merge."""

    def __init__(self, tasks):
        self.tasks = tasks

    def process(self, raw_tasks, open_tasks, node):
        return open_tasks, list(self.tasks)


class _FakeCoverageChecker:
    min_confidence = 0.6

    def __init__(self, report):
        self.report = report

    def check_section(self, node, section_text, extracted_tasks):
        return self.report


def _make_orch():
    config = RunConfig(
        run_id="cov-gate-test",
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
    orch.indexer = FakeIndexer()
    return orch


def _task(*node_ids: str, flags=None) -> ManagedTask:
    return ManagedTask(
        title="t",
        short_description="d",
        confidence=0.9,
        flags=list(flags or []),
        source_refs=[
            SourceRef(node_id=n, section_title=n, page_start=1, page_end=1)
            for n in node_ids
        ],
    )


# ─── A: per-node loop no longer flags inline ──────────────────────────────────


def test_apply_node_does_not_flag_incomplete_inline():
    orch = _make_orch()
    task = _task("n1")
    orch.state_agent = _FakeState([task])
    report = SectionCoverageReport(
        node_id="n1",
        extracted_count=1,
        missed_items=[MissedItem(description="missed", confidence=0.9, reason="dropped")],
        checker_confidence=0.9,
    )
    orch.coverage_checker = _FakeCoverageChecker(report)

    node = {"node_id": "n1", "title": "N1", "text": "x" * 200}
    orch._apply_node(node, [{"raw": "n1"}], [])

    # Flagging is deferred to the post-dedup run-wide gate; the per-node loop must
    # not flag INCOMPLETE inline anymore (that was the double-flag risk).
    assert TaskFlag.INCOMPLETE not in task.flags
    # ...but the report is still produced + stored for the gate to consume.
    assert "n1" in orch.section_coverage_reports


# ─── B: run-wide gate applies flags post-dedup, report-level ──────────────────


def test_run_coverage_verify_flags_post_dedup_report_level():
    orch = _make_orch()
    orch.coverage_checker = _FakeCoverageChecker(None)  # supplies the min_confidence floor
    orch.section_coverage_reports = {
        "n1": {
            "node_id": "n1",
            "extracted_count": 1,
            "checker_confidence": 0.9,
            "missed_items": [{"description": "m", "confidence": 0.9, "reason": "r"}],
        }
    }
    task_a = _task("n1")  # confident-miss section
    task_b = _task("n2")  # clean section (no report at all)

    result = orch._run_coverage_verify([task_a, task_b])

    assert TaskFlag.INCOMPLETE in task_a.flags
    assert TaskFlag.INCOMPLETE not in task_b.flags
    assert result.flagged_task_count == 1
    assert result.total_task_count == 2
    # The run-level advisory result is retained on the orchestrator.
    assert orch.coverage_gate_result is result


def test_run_coverage_verify_low_confidence_does_not_flag():
    orch = _make_orch()
    orch.coverage_checker = _FakeCoverageChecker(None)
    orch.section_coverage_reports = {
        "n1": {
            "node_id": "n1",
            "extracted_count": 1,
            "checker_confidence": 0.3,  # below the 0.6 floor
            "missed_items": [{"description": "m", "confidence": 0.3, "reason": "r"}],
        }
    }
    task = _task("n1")

    result = orch._run_coverage_verify([task])

    assert TaskFlag.INCOMPLETE not in task.flags
    assert result.flagged_task_count == 0


def test_run_coverage_verify_noop_when_no_reports():
    orch = _make_orch()
    orch.section_coverage_reports = {}
    task = _task("n1")

    result = orch._run_coverage_verify([task])

    assert TaskFlag.INCOMPLETE not in task.flags
    assert result.flagged_task_count == 0
    assert result.total_task_count == 1
