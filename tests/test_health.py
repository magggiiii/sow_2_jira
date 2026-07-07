# tests/test_health.py

from core.health import RunHealthReport, StageHealth
from core.results import StageStatus

# ─── overall_status reflects worst stage ──────────────────────────────────────

def test_all_ok_is_overall_ok():
    report = RunHealthReport(
        stages=[
            StageHealth(name="index", status=StageStatus.OK),
            StageHealth(name="extract", status=StageStatus.OK),
        ]
    )
    assert report.overall_status is StageStatus.OK
    assert report.is_degraded is False


def test_one_degraded_stage_is_overall_degraded():
    report = RunHealthReport(
        stages=[
            StageHealth(name="index", status=StageStatus.OK),
            StageHealth(name="dedup", status=StageStatus.DEGRADED, reason="llm fallback"),
        ]
    )
    assert report.overall_status is StageStatus.DEGRADED
    assert report.is_degraded is True


def test_failed_stage_makes_overall_failed():
    report = RunHealthReport(
        stages=[
            StageHealth(name="index", status=StageStatus.OK),
            StageHealth(name="dedup", status=StageStatus.DEGRADED, reason="llm fallback"),
            StageHealth(name="extract", status=StageStatus.FAILED, reason="model error"),
        ]
    )
    # FAILED outranks DEGRADED and OK.
    assert report.overall_status is StageStatus.FAILED
    assert report.is_degraded is False


def test_skipped_treated_as_ok_for_overall():
    report = RunHealthReport(
        stages=[
            StageHealth(name="index", status=StageStatus.OK),
            StageHealth(name="gap_recovery", status=StageStatus.SKIPPED),
        ]
    )
    assert report.overall_status is StageStatus.OK


def test_all_skipped_is_overall_skipped():
    report = RunHealthReport(
        stages=[
            StageHealth(name="a", status=StageStatus.SKIPPED),
            StageHealth(name="b", status=StageStatus.SKIPPED),
        ]
    )
    assert report.overall_status is StageStatus.SKIPPED


def test_empty_report_is_skipped_and_not_degraded():
    report = RunHealthReport()
    assert report.overall_status is StageStatus.SKIPPED
    assert report.is_degraded is False


# ─── counts ────────────────────────────────────────────────────────────────────

def test_counts_partition_stages():
    report = RunHealthReport(
        stages=[
            StageHealth(name="a", status=StageStatus.OK),
            StageHealth(name="b", status=StageStatus.OK),
            StageHealth(name="c", status=StageStatus.DEGRADED, reason="r1"),
            StageHealth(name="d", status=StageStatus.FAILED, reason="r2"),
            StageHealth(name="e", status=StageStatus.SKIPPED),
        ]
    )
    assert report.ok == 2
    assert report.degraded == 1
    assert report.failed == 1
    assert report.skipped == 1


# ─── cost sums ──────────────────────────────────────────────────────────────────

def test_total_cost_sums_across_stages():
    report = RunHealthReport(
        stages=[
            StageHealth(name="a", status=StageStatus.OK, cost_usd=0.01),
            StageHealth(name="b", status=StageStatus.DEGRADED, reason="x", cost_usd=0.025),
            StageHealth(name="c", status=StageStatus.SKIPPED),
        ]
    )
    assert report.total_cost_usd == 0.035


# ─── degraded_reasons ────────────────────────────────────────────────────────────

def test_degraded_reasons_collects_degraded_and_failed_with_reasons():
    report = RunHealthReport(
        stages=[
            StageHealth(name="a", status=StageStatus.OK, reason="ignored"),
            StageHealth(name="b", status=StageStatus.DEGRADED, reason="llm fallback"),
            StageHealth(name="c", status=StageStatus.FAILED, reason="model error"),
            StageHealth(name="d", status=StageStatus.DEGRADED, reason=""),  # empty skipped
        ]
    )
    assert report.degraded_reasons == ["llm fallback", "model error"]


# ─── add() ───────────────────────────────────────────────────────────────────────

def test_add_appends_and_updates_aggregates():
    report = RunHealthReport()
    assert report.overall_status is StageStatus.SKIPPED

    report.add(StageHealth(name="index", status=StageStatus.OK))
    assert report.overall_status is StageStatus.OK
    assert report.is_degraded is False

    returned = report.add(
        StageHealth(name="dedup", status=StageStatus.DEGRADED, reason="vector miss")
    )
    assert returned is report  # chainable
    assert report.overall_status is StageStatus.DEGRADED
    assert report.is_degraded is True
    assert report.degraded == 1
    assert report.degraded_reasons == ["vector miss"]


# ─── to_dict round-trips ──────────────────────────────────────────────────────────

def test_to_dict_shape_and_values():
    report = RunHealthReport(
        stages=[
            StageHealth(name="index", status=StageStatus.OK, cost_usd=0.01),
            StageHealth(
                name="dedup", status=StageStatus.DEGRADED, reason="fallback", cost_usd=0.02,
            ),
        ]
    )
    d = report.to_dict()
    assert d["overall_status"] == "DEGRADED"
    assert d["is_degraded"] is True
    assert d["counts"] == {"ok": 1, "degraded": 1, "failed": 0, "skipped": 0}
    assert d["total_cost_usd"] == 0.03
    assert d["degraded_reasons"] == ["fallback"]
    assert len(d["stages"]) == 2
    assert d["stages"][0]["name"] == "index"
    assert d["stages"][1]["status"] == "DEGRADED"


def test_model_round_trips_via_model_dump():
    report = RunHealthReport(
        stages=[
            StageHealth(name="a", status=StageStatus.OK, cost_usd=0.5),
            StageHealth(name="b", status=StageStatus.FAILED, reason="boom", cost_usd=1.5),
        ]
    )
    rebuilt = RunHealthReport.model_validate(report.model_dump())
    assert rebuilt == report
    assert rebuilt.overall_status is StageStatus.FAILED
    assert rebuilt.total_cost_usd == 2.0
    assert rebuilt.degraded_reasons == ["boom"]
