# tests/test_run_health.py
"""
GUARDRAIL-3: focused tests for the run health summary.

These exercise the pure assembly helper ``build_health_report`` (no pipeline,
no LLM, no network) and the orchestrator's default ``health_report`` attribute.
The headline assertion: a degraded dedup signal yields an overall-DEGRADED
report.
"""

from core.health import RunHealthReport, build_health_report
from core.results import StageStatus


def test_degraded_dedup_yields_overall_degraded():
    """A degraded dedup signal must drive the overall verdict to DEGRADED."""
    report = build_health_report({
        "dedup_degraded": True,
        "dedup_degraded_reason": "large input yielded 0 merges",
        "extraction_error_count": 0,
        "coverage_pct": 100,
    })

    assert isinstance(report, RunHealthReport)
    assert report.overall_status is StageStatus.DEGRADED
    assert report.is_degraded is True
    assert report.degraded == 1
    # The dedup stage carries the reason through to the report.
    assert "large input yielded 0 merges" in report.degraded_reasons


def test_dedup_degraded_without_reason_gets_fallback_reason():
    report = build_health_report({
        "dedup_degraded": True,
        "dedup_degraded_reason": None,
        "extraction_error_count": 0,
        "coverage_pct": 50,
    })
    assert report.overall_status is StageStatus.DEGRADED
    assert report.degraded_reasons == ["deduplication degraded"]


def test_all_ok_signals_yield_overall_ok():
    report = build_health_report({
        "dedup_degraded": False,
        "extraction_error_count": 0,
        "coverage_pct": 100,
    })
    assert report.overall_status is StageStatus.OK
    assert report.is_degraded is False
    assert report.failed == 0
    assert report.degraded == 0
    # dedup OK + extraction OK + coverage OK
    assert report.ok == 3


def test_extraction_errors_yield_degraded_extraction_stage():
    report = build_health_report({
        "dedup_degraded": False,
        "extraction_error_count": 3,
        "coverage_pct": 90,
    })
    assert report.overall_status is StageStatus.DEGRADED
    names = {s.name: s.status for s in report.stages}
    assert names["extraction"] is StageStatus.DEGRADED
    assert names["dedup"] is StageStatus.OK
    assert any("3 extraction error" in r for r in report.degraded_reasons)


def test_extraction_failed_overrides_error_count():
    report = build_health_report({
        "dedup_degraded": False,
        "extraction_failed": True,
        "extraction_error_count": 2,  # ignored: hard failure is more severe
        "coverage_pct": 0,
    })
    assert report.overall_status is StageStatus.FAILED
    names = {s.name: s.status for s in report.stages}
    assert names["extraction"] is StageStatus.FAILED


def test_missing_signals_are_omitted_not_failed():
    """Absent signals add no stage and never invent a failure."""
    report = build_health_report({})  # nothing known
    assert report.stages == []
    # Empty report's overall verdict is SKIPPED (see core.health).
    assert report.overall_status is StageStatus.SKIPPED

    # coverage_pct present-but-None should not add a coverage stage.
    report2 = build_health_report({"coverage_pct": None})
    assert all(s.name != "coverage" for s in report2.stages)


# ─── A2: capacity (graceful max_nodes) signal ─────────────────────────────────


def test_capacity_degraded_signal_yields_degraded_capacity_stage():
    """When the run capped the node count (graceful max_nodes), the capacity
    stage is DEGRADED and carries the 'Processed N of M' reason."""
    report = build_health_report({
        "capacity_degraded": True,
        "capacity_degraded_reason": "Processed 200 of 250 nodes (capacity cap)",
    })
    names = {s.name: s.status for s in report.stages}
    assert names["capacity"] is StageStatus.DEGRADED
    assert report.overall_status is StageStatus.DEGRADED
    assert "Processed 200 of 250 nodes (capacity cap)" in report.degraded_reasons


def test_capacity_not_degraded_signal_yields_ok_capacity_stage():
    report = build_health_report({"capacity_degraded": False})
    names = {s.name: s.status for s in report.stages}
    assert names["capacity"] is StageStatus.OK
    assert report.overall_status is StageStatus.OK


# ─── A2: per-node error-isolation signal ──────────────────────────────────────


def test_node_errors_over_threshold_yield_degraded_node_processing():
    """More than 5% of nodes failing to process flags node_processing DEGRADED."""
    report = build_health_report({"node_error_count": 4, "node_total": 50})  # 8% > 5%
    names = {s.name: s.status for s in report.stages}
    assert names["node_processing"] is StageStatus.DEGRADED
    assert report.is_degraded is True
    assert any("4 of 50" in r for r in report.degraded_reasons)


def test_node_errors_under_threshold_stay_ok():
    """A couple of isolated node failures (≤5%) are tolerated, not flagged."""
    report = build_health_report({"node_error_count": 2, "node_total": 50})  # 4% ≤ 5%
    names = {s.name: s.status for s in report.stages}
    assert names["node_processing"] is StageStatus.OK
    assert report.overall_status is StageStatus.OK


def test_node_processing_signal_absent_adds_no_stage():
    """No node_total → no node_processing stage (tolerant, like other signals)."""
    report = build_health_report({"node_error_count": 9})  # no node_total
    assert all(s.name != "node_processing" for s in report.stages)


def test_report_round_trips_via_model_validate():
    report = build_health_report({
        "dedup_degraded": True,
        "dedup_degraded_reason": "vector miss",
        "extraction_error_count": 1,
        "coverage_pct": 75,
    })
    rebuilt = RunHealthReport.model_validate(report.model_dump())
    assert rebuilt.overall_status is StageStatus.DEGRADED
    assert rebuilt.to_dict() == report.to_dict()


def test_orchestrator_has_default_health_report_attribute():
    """The orchestrator exposes an (empty) health_report before run() executes."""
    from unittest.mock import MagicMock

    from pipeline.orchestrator import PipelineOrchestrator
    from models.schemas import RunConfig, LLMMode, JiraHierarchy

    config = RunConfig(
        run_id="health-test-run",
        sow_pdf_path="/tmp/does-not-exist.pdf",
        llm_mode=LLMMode.LOCAL,
        jira_hierarchy=JiraHierarchy.EPIC_TASK,
        jira_project_key="TEST",
    )
    app_config = {
        "pipeline": {
            "max_gap_recovery_iterations": 1,
            "max_section_chars": 16000,
        }
    }

    orch = PipelineOrchestrator(
        config=config,
        app_config=app_config,
        audit=MagicMock(),
        llm=MagicMock(),  # injected provider seam; no LLM/network touched
    )

    assert isinstance(orch.health_report, RunHealthReport)
    # Fresh, unrun orchestrator: empty report -> SKIPPED overall.
    assert orch.health_report.overall_status is StageStatus.SKIPPED
