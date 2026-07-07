# tests/test_push_gate.py
"""
STEP 3.5: PushGate — the deterministic guard on what may be pushed to Jira.

Now that coverage flags tasks INCOMPLETE trustworthily (C-4) and a run can be
marked DEGRADED, the push path must refuse to ship known-bad work. PushGate is a
pure, read-only gate (beside ConfidenceGate/CoverageGate in core/guardrails.py):

  * a task already carrying ``jira_issue_key`` is SKIPPED (idempotent re-push),
    never blocked — even under override;
  * a task carrying a BLOCKING flag (INCOMPLETE / LOW_CONFIDENCE / AMBIGUOUS_SCOPE
    / NO_ACCEPTANCE_CRITERIA), or ANY task on a DEGRADED run, is BLOCKED unless
    ``override=True``;
  * everything else is pushable.

It raises ``PushBlocked`` (carrying the full partition) when a block exists and
override is off. Additive this increment — the live push path wires it in W5.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from core.guardrails import BLOCKING_FLAGS, PushBlocked, PushGate, PushGateResult
from core.health import RunHealthReport, StageHealth
from core.results import StageStatus
from models.schemas import ManagedTask, TaskFlag, TaskStatus


def _task(*flags: TaskFlag, key=None, status=TaskStatus.APPROVED) -> ManagedTask:
    return ManagedTask(
        title="t", short_description="d", confidence=0.9,
        flags=list(flags), jira_issue_key=key, status=status,
    )


def _degraded() -> RunHealthReport:
    return RunHealthReport().add(
        StageHealth(name="dedup", status=StageStatus.DEGRADED, reason="0 merges")
    )


# ─── happy path + return shape ────────────────────────────────────────────────


def test_pushable_clean_task_passes():
    task = _task()
    result = PushGate().assert_pushable([task], run_status=False)

    assert isinstance(result, PushGateResult)
    assert result.pushable == [task]
    assert result.blocked == [] and result.skipped == []
    assert [d.verdict for d in result.decisions] == ["push"]


# ─── blocking flags ───────────────────────────────────────────────────────────


def test_blocking_flag_task_raises_without_override():
    task = _task(TaskFlag.INCOMPLETE)
    with pytest.raises(PushBlocked) as exc:
        PushGate().assert_pushable([task], run_status=False)
    assert any(d.verdict == "block" and "INCOMPLETE" in d.reason for d in exc.value.blocked)


@pytest.mark.parametrize(
    "flag",
    [
        TaskFlag.INCOMPLETE,
        TaskFlag.LOW_CONFIDENCE,
        TaskFlag.AMBIGUOUS_SCOPE,
        TaskFlag.NO_ACCEPTANCE_CRITERIA,
    ],
)
def test_each_blocking_flag_blocks(flag):
    with pytest.raises(PushBlocked):
        PushGate().assert_pushable([_task(flag)], run_status=False)


@pytest.mark.parametrize(
    "flag",
    [TaskFlag.NO_MOCKUP, TaskFlag.POTENTIAL_DUPLICATE, TaskFlag.GAP_RECOVERED, TaskFlag.TRUNCATION],
)
def test_informational_flags_do_not_block(flag):
    task = _task(flag)
    result = PushGate().assert_pushable([task], run_status=False)
    assert result.pushable == [task]
    assert result.blocked == []


# ─── degraded run ─────────────────────────────────────────────────────────────


def test_degraded_run_blocks_even_a_clean_task():
    task = _task()  # clean, unflagged
    with pytest.raises(PushBlocked) as exc:
        PushGate().assert_pushable([task], run_status=_degraded())
    assert any(d.reason == "DEGRADED run" for d in exc.value.blocked)


# ─── override ─────────────────────────────────────────────────────────────────


def test_override_bypasses_flag_and_degraded():
    flagged = _task(TaskFlag.INCOMPLETE)
    res_flag = PushGate().assert_pushable([flagged], run_status=False, override=True)
    assert res_flag.pushable == [flagged] and res_flag.blocked == []

    clean = _task()
    res_deg = PushGate().assert_pushable([clean], run_status=_degraded(), override=True)
    assert res_deg.pushable == [clean] and res_deg.blocked == []


def test_override_must_be_keyword_only():
    with pytest.raises(TypeError):
        PushGate().assert_pushable([_task()], False, True)  # positional override


# ─── already-pushed (idempotent skip) ─────────────────────────────────────────


def test_already_pushed_is_skipped_not_blocked():
    task = _task(key="PROJ-42")
    result = PushGate().assert_pushable([task], run_status=False)
    assert result.pushable == []
    assert len(result.skipped) == 1 and "PROJ-42" in result.skipped[0].reason
    assert result.blocked == []


def test_already_pushed_skips_even_with_blocking_flag():
    task = _task(TaskFlag.INCOMPLETE, key="PROJ-7")
    result = PushGate().assert_pushable([task], run_status=False)  # no raise — skip wins
    assert result.skipped and result.blocked == [] and result.pushable == []


def test_already_pushed_skips_under_override():
    task = _task(key="PROJ-9")
    result = PushGate().assert_pushable([task], run_status=_degraded(), override=True)
    assert result.skipped and task not in result.pushable


def test_all_already_pushed_no_raise_even_on_degraded_run():
    tasks = [_task(key="A-1"), _task(key="A-2")]
    result = PushGate().assert_pushable(tasks, run_status=_degraded())  # no raise: all skipped
    assert len(result.skipped) == 2 and result.blocked == [] and result.pushable == []


# ─── batch partitioning ───────────────────────────────────────────────────────


def test_mixed_batch_partitions_and_override_clears_blocks():
    clean, flagged, pushed = _task(), _task(TaskFlag.INCOMPLETE), _task(key="P-1")

    with pytest.raises(PushBlocked) as exc:
        PushGate().assert_pushable([clean, flagged, pushed], run_status=False)
    # the exception still exposes the full partition so the caller can act
    res = exc.value.result
    assert res.pushable == [clean]
    assert [d.task_id for d in res.skipped] == [str(pushed.id)]
    assert [d.task_id for d in res.blocked] == [str(flagged.id)]

    ok = PushGate().assert_pushable([clean, flagged, pushed], run_status=False, override=True)
    assert ok.pushable == [clean, flagged] and len(ok.skipped) == 1 and ok.blocked == []


# ─── edges ────────────────────────────────────────────────────────────────────


def test_empty_task_list_is_noop():
    result = PushGate().assert_pushable([], run_status=_degraded())  # no tasks => no block
    assert result.pushable == [] and result.blocked == [] and result.skipped == []


def test_accepts_dict_form_tasks_and_status():
    tasks = [
        {"id": str(uuid4()), "flags": ["INCOMPLETE"], "jira_issue_key": None},
        {"id": str(uuid4()), "flags": None, "jira_issue_key": "X-1"},
    ]
    with pytest.raises(PushBlocked) as exc:
        PushGate().assert_pushable(tasks, run_status={"is_degraded": False})
    res = exc.value.result
    assert len(res.blocked) == 1 and len(res.skipped) == 1


def test_gate_does_not_mutate_tasks():
    clean, flagged = _task(), _task(TaskFlag.INCOMPLETE)
    try:
        PushGate().assert_pushable([clean, flagged], run_status=False)
    except PushBlocked:
        pass
    assert clean.flags == [] and clean.status == TaskStatus.APPROVED
    assert flagged.flags == [TaskFlag.INCOMPLETE]  # not mutated, no PUSHED


def test_scalar_string_flags_field_fails_closed():
    """A malformed dict with a SCALAR flags string (not a list) must fail CLOSED —
    a push admission gate should never let a blocking flag slip through on a shape
    quirk. (Out of the documented list-form contract, but a gate defaults to safe.)"""
    task = {"id": "t1", "flags": "INCOMPLETE", "jira_issue_key": None}
    with pytest.raises(PushBlocked):
        PushGate().assert_pushable([task], run_status=False)


def test_blocking_flags_constructor_injectable():
    # TRUNCATION is advisory by default but a stricter deployment can add it.
    strict = PushGate(blocking_flags=BLOCKING_FLAGS | {TaskFlag.TRUNCATION})
    with pytest.raises(PushBlocked):
        strict.assert_pushable([_task(TaskFlag.TRUNCATION)], run_status=False)
    # default gate lets it through
    assert PushGate().assert_pushable([_task(TaskFlag.TRUNCATION)], run_status=False).pushable
