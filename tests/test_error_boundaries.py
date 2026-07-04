# tests/test_error_boundaries.py
"""The pipeline + Jira-push error boundaries classify failures onto the run
status (ProcessingStatus.error_class) so the UI can react — retry (transient),
fix-settings (user_fixable), or fail (terminal) — instead of only showing a
string. Behavior otherwise unchanged; the error string is still populated.

Deps are monkeypatched; no network / real Jira / real LLM.
"""

from __future__ import annotations

from core.errors import ErrorClass
from models.schemas import JiraPushResult, ManagedTask, TaskStatus


def _task() -> ManagedTask:
    return ManagedTask(
        title="t", short_description="d", confidence=0.9, status=TaskStatus.APPROVED,
    )


def _noop_audit(*a, **k):
    return type("A", (), {"log": lambda *a, **k: None})()


def _push_data():
    return {
        "config": {"jira_hierarchy": "flat", "jira_project_key": "PROJ"},
        "tasks": [_task().model_dump(mode="json")],
        "health": {"is_degraded": False},
    }


# ── run_push_task ─────────────────────────────────────────────────────────────


def test_run_push_task_threads_error_class_from_failed_result(monkeypatch):
    """A classified failed JiraPushResult surfaces its class on the run status."""
    import ui.server as srv

    monkeypatch.setattr(srv, "load_data", lambda sid: _push_data())
    monkeypatch.setattr(srv, "save_data", lambda d, sid=None: None)
    monkeypatch.setattr(srv, "AuditLogger", _noop_audit)

    class FakeJira:
        def __init__(self, *a, **k):
            pass

        def push_tasks(self, tasks):
            return [
                JiraPushResult(
                    task_id=t.id,
                    success=False,
                    error="Unauthorized",
                    error_class=ErrorClass.USER_FIXABLE,
                )
                for t in tasks
            ]

    monkeypatch.setattr(srv, "JiraClient", FakeJira)

    srv.run_push_task(srv.PushRequest(), session_id="s_ec1", run_id="r_ec1")

    status = srv.active_runs.get("r_ec1")
    assert status is not None and status.is_running is False
    assert status.error  # string still present (unchanged)
    assert status.error_class is ErrorClass.USER_FIXABLE


def test_run_push_task_classifies_raised_exception(monkeypatch):
    """If the push itself raises, the boundary classifies it onto the status."""
    import ui.server as srv

    monkeypatch.setattr(srv, "load_data", lambda sid: _push_data())
    monkeypatch.setattr(srv, "save_data", lambda d, sid=None: None)
    monkeypatch.setattr(srv, "AuditLogger", _noop_audit)

    class _Boom(Exception):
        status_code = 503

    class FakeJira:
        def __init__(self, *a, **k):
            pass

        def push_tasks(self, tasks):
            raise _Boom("service unavailable")

    monkeypatch.setattr(srv, "JiraClient", FakeJira)

    srv.run_push_task(srv.PushRequest(), session_id="s_ec2", run_id="r_ec2")

    status = srv.active_runs.get("r_ec2")
    assert status is not None and status.is_running is False
    assert status.error
    assert status.error_class is ErrorClass.TRANSIENT


# ── run_pipeline_task ─────────────────────────────────────────────────────────


def test_run_pipeline_task_classifies_raised_exception(monkeypatch):
    """A crash inside the pipeline is classified onto the run status too."""
    import ui.server as srv

    monkeypatch.setattr(srv, "AuditLogger", _noop_audit)

    class _Boom(Exception):
        status_code = 401  # e.g. bad API key mid-run

    class FakeOrch:
        def __init__(self, run_cfg, app_config, audit, status_callback=None):
            pass

        def run(self):
            raise _Boom("invalid api key")

    monkeypatch.setattr(srv, "PipelineOrchestrator", FakeOrch)

    req = srv.ProcessRequest(
        pdf_filename="nonexistent.pdf",
        llm_mode="local",
        jira_hierarchy="flat",
        jira_project_key="PROJ",
    )
    srv.run_pipeline_task(req, run_id="r_ec3")

    status = srv.active_runs.get("r_ec3")
    assert status is not None and status.is_running is False
    assert status.error
    assert status.error_class is ErrorClass.USER_FIXABLE
