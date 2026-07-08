"""tz-awareness contract tests for the WAVE 1 STEP 1.3 datetime migration.

These pin the sole behavioral guarantee of 1.3: every timestamp default carries
tzinfo=UTC. Without them a regression back to naive ``datetime.utcnow()`` would
keep the whole suite green.
"""

import datetime

from models.schemas import AuditEntry, ManagedTask, utcnow


def _managed_task():
    return ManagedTask(title="t", short_description="d", confidence=0.5)


def _audit_entry():
    return AuditEntry(
        run_id="r", agent="A", node_id=None, action="X", task_id=None, detail="d"
    )


def test_utcnow_is_tz_aware_utc():
    n = utcnow()
    assert n.tzinfo is not None
    assert n.utcoffset() == datetime.timedelta(0)


def test_managed_task_datetimes_are_tz_aware():
    t = _managed_task()
    assert t.created_at.tzinfo is not None
    assert t.updated_at.tzinfo is not None
    assert t.created_at.utcoffset() == datetime.timedelta(0)


def test_audit_entry_timestamp_is_tz_aware():
    assert _audit_entry().timestamp.tzinfo is not None


def test_section_coverage_report_checked_at_is_tz_aware():
    from pipeline.agents.coverage_check import SectionCoverageReport

    r = SectionCoverageReport(node_id="n", extracted_count=0)
    assert r.checked_at.tzinfo is not None


def test_two_task_datetimes_are_comparable_no_naive_mix():
    """Guards the state/dedup reassignment fix: model datetimes must all be
    aware, so comparing/subtracting them never raises the aware-vs-naive
    TypeError."""
    a, b = _managed_task(), _managed_task()
    # Would raise "can't compare offset-naive and offset-aware datetimes" if any
    # were naive.
    assert (b.created_at >= a.created_at) in (True, False)
    _ = b.updated_at - a.created_at  # subtraction must not raise


def test_managed_task_datetime_survives_json_roundtrip_tz_aware():
    """Serialization drift guard: model_dump(mode='json') -> model_validate must
    preserve tz-awareness."""
    t = _managed_task()
    dumped = t.model_dump(mode="json")
    # Pydantic v2 serializes aware-UTC as "...Z"; raw datetime.isoformat() uses
    # "+00:00". Both are valid ISO-8601 UTC — accept either, and require that the
    # round-trip restores a tz-aware datetime (no silent drift to naive).
    assert dumped["created_at"].endswith("Z") or dumped["created_at"].endswith("+00:00")
    restored = ManagedTask.model_validate(dumped)
    assert restored.created_at.tzinfo is not None
    assert restored.created_at.utcoffset() == datetime.timedelta(0)
