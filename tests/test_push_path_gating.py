# tests/test_push_path_gating.py
"""
STEP 5.3: PushGate wired into the live push path.

PushGate exists (core/guardrails.py) but was dormant. The push handler must run
approved tasks through it BEFORE the Jira create loop so that:
  - already-pushed tasks (jira_issue_key) are skipped idempotently (success + warning),
  - blocked tasks (INCOMPLETE/LOW_CONFIDENCE/AMBIGUOUS_SCOPE/NO_ACCEPTANCE_CRITERIA,
    or ANY task on a DEGRADED run) surface as a FAILED JiraPushResult — never crash
    the whole push, and are NOT sent to Jira,
  - an explicit override force-pushes.

The gating logic lives in a pure ``ui.server._gate_push`` helper (TDD seam); the
end-to-end wiring is covered by a monkeypatched run_push_task test.
"""

from __future__ import annotations

from models.schemas import JiraPushResult, ManagedTask, TaskFlag, TaskStatus


def _task(*flags: TaskFlag, key=None) -> ManagedTask:
    return ManagedTask(
        title="t", short_description="d", confidence=0.9,
        flags=list(flags), jira_issue_key=key, status=TaskStatus.APPROVED,
    )


# ─── _gate_push (pure helper) ─────────────────────────────────────────────────


def test_gate_push_clean_tasks_all_pushable():
    from ui.server import _gate_push
    a, b = _task(), _task()
    pushable, gated = _gate_push([a, b], run_status=None, override=False)
    assert pushable == [a, b]
    assert gated == []


def test_gate_push_blocks_flagged_task_as_failed_result():
    from ui.server import _gate_push
    clean, flagged = _task(), _task(TaskFlag.INCOMPLETE)
    pushable, gated = _gate_push([clean, flagged], run_status=None)
    assert pushable == [clean]
    assert len(gated) == 1
    assert gated[0].task_id == flagged.id
    assert gated[0].success is False
    assert "INCOMPLETE" in (gated[0].error or "")


def test_gate_push_skips_already_pushed_as_idempotent_success():
    from ui.server import _gate_push
    pushed = _task(key="PROJ-42")
    pushable, gated = _gate_push([pushed], run_status=None)
    assert pushable == []
    assert len(gated) == 1
    assert gated[0].success is True
    assert gated[0].jira_issue_key == "PROJ-42"
    assert gated[0].warning  # notes the idempotent skip


def test_gate_push_degraded_run_blocks_all_unless_override():
    from ui.server import _gate_push
    clean = _task()
    pushable, gated = _gate_push([clean], run_status={"is_degraded": True})
    assert pushable == []
    assert len(gated) == 1 and gated[0].success is False

    pushable2, gated2 = _gate_push([clean], run_status={"is_degraded": True}, override=True)
    assert pushable2 == [clean] and gated2 == []


def test_gate_push_override_pushes_flagged():
    from ui.server import _gate_push
    flagged = _task(TaskFlag.INCOMPLETE)
    pushable, gated = _gate_push([flagged], run_status=None, override=True)
    assert pushable == [flagged] and gated == []


# ─── run_push_task wiring (integration; deps monkeypatched, no I/O / network) ──


def test_run_push_task_only_pushes_cleared_tasks(monkeypatch):
    import ui.server as srv

    clean, flagged = _task(), _task(TaskFlag.INCOMPLETE)
    data = {
        "config": {"jira_hierarchy": "flat", "jira_project_key": "PROJ"},
        "tasks": [clean.model_dump(mode="json"), flagged.model_dump(mode="json")],
        "health": {"is_degraded": False},
    }
    monkeypatch.setattr(srv, "load_data", lambda sid: data)
    monkeypatch.setattr(srv, "save_data", lambda d, sid=None: None)

    received = {}

    class FakeJira:
        def __init__(self, *a, **k):
            pass

        def push_tasks(self, tasks):
            received["tasks"] = list(tasks)
            return [
                JiraPushResult(task_id=t.id, success=True, jira_issue_key="PROJ-1")
                for t in tasks
            ]

    monkeypatch.setattr(srv, "JiraClient", FakeJira)
    monkeypatch.setattr(
        srv, "AuditLogger", lambda *a, **k: type("A", (), {"log": lambda *a, **k: None})()
    )

    srv.run_push_task(srv.PushRequest(), session_id="s1", run_id="r1")

    # Only the clean task reached Jira; the INCOMPLETE one was gated out.
    assert [t.id for t in received["tasks"]] == [clean.id]
    # The run's status reflects the gated failure (not a crash).
    status = srv.active_runs.get("r1")
    assert status is not None and status.is_running is False


def test_run_push_task_override_pushes_flagged(monkeypatch):
    import ui.server as srv

    flagged = _task(TaskFlag.INCOMPLETE)
    data = {
        "config": {"jira_hierarchy": "flat", "jira_project_key": "PROJ"},
        "tasks": [flagged.model_dump(mode="json")],
        "health": {"is_degraded": True},  # degraded — would block without override
    }
    monkeypatch.setattr(srv, "load_data", lambda sid: data)
    monkeypatch.setattr(srv, "save_data", lambda d, sid=None: None)

    received = {}

    class FakeJira:
        def __init__(self, *a, **k):
            pass

        def push_tasks(self, tasks):
            received["tasks"] = list(tasks)
            return [
                JiraPushResult(task_id=t.id, success=True, jira_issue_key="PROJ-9")
                for t in tasks
            ]

    monkeypatch.setattr(srv, "JiraClient", FakeJira)
    monkeypatch.setattr(
        srv, "AuditLogger", lambda *a, **k: type("A", (), {"log": lambda *a, **k: None})()
    )

    srv.run_push_task(srv.PushRequest(override=True), session_id="s2", run_id="r2")

    assert [t.id for t in received["tasks"]] == [flagged.id]  # override forced it through
