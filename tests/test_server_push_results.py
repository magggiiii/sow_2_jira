# tests/test_server_push_results.py
"""SERVER-A backend lane — push-result persistence + read-only Jira test.

Covers:
  BE-1  run_push_task PERSISTS the per-task JiraPushResults into the saved run
        data (previously built then discarded) so a MIX of success/failure
        survives a reload.
  BE-2  GET /api/tasks surfaces the persisted push outcome on each task
        (issue key/url on success; error_class/message on failure).
  BE-3  POST /api/jira/test is a READ-ONLY connection test — validates creds /
        reaches the server, never creates an issue, and maps failures onto the
        app ErrorClass taxonomy (401 → user_fixable, 429 → transient).

All Jira / disk I/O is monkeypatched; no network, no real writes.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from core.errors import ErrorClass
from models.schemas import JiraPushResult, ManagedTask, TaskFlag, TaskStatus


def _task(*flags: TaskFlag, key=None) -> ManagedTask:
    return ManagedTask(
        title="t", short_description="d", confidence=0.9,
        flags=list(flags), jira_issue_key=key, status=TaskStatus.APPROVED,
    )


# ─── BE-1: run_push_task persists a MIX of success/failure results ────────────


def test_run_push_task_persists_mixed_push_results(monkeypatch):
    import ui.server as srv

    ok_task, fail_task = _task(), _task()
    data = {
        "config": {"jira_hierarchy": "flat", "jira_project_key": "PROJ"},
        "tasks": [ok_task.model_dump(mode="json"), fail_task.model_dump(mode="json")],
        "health": {"is_degraded": False},
    }

    saved = {}
    monkeypatch.setattr(srv, "load_data", lambda sid: data)
    monkeypatch.setattr(srv, "save_data", lambda d, sid=None: saved.update(d))

    class FakeJira:
        def __init__(self, *a, **k):
            pass

        def push_tasks(self, tasks):
            out = []
            for t in tasks:
                if t.id == ok_task.id:
                    out.append(JiraPushResult(
                        task_id=t.id, success=True,
                        jira_issue_key="PROJ-1",
                        jira_issue_url="https://ex.atlassian.net/browse/PROJ-1",
                    ))
                else:
                    out.append(JiraPushResult(
                        task_id=t.id, success=False,
                        error="boom", error_class=ErrorClass.TERMINAL,
                    ))
            return out

    monkeypatch.setattr(srv, "JiraClient", FakeJira)
    monkeypatch.setattr(
        srv, "AuditLogger", lambda *a, **k: type("A", (), {"log": lambda *a, **k: None})()
    )

    srv.run_push_task(srv.PushRequest(), session_id="s1", run_id="r1")

    # The saved data must carry the per-task push outcome — not discard it.
    by_id = {str(t["id"]): t for t in saved["tasks"]}
    ok_saved = by_id[str(ok_task.id)]
    fail_saved = by_id[str(fail_task.id)]

    assert ok_saved.get("push_result") is not None
    assert ok_saved["push_result"]["success"] is True
    assert ok_saved["push_result"]["jira_issue_key"] == "PROJ-1"
    assert ok_saved["push_result"]["jira_issue_url"].endswith("/PROJ-1")
    # Success flips status to PUSHED (existing behaviour preserved).
    assert ok_saved["status"] == "PUSHED"

    assert fail_saved.get("push_result") is not None
    assert fail_saved["push_result"]["success"] is False
    assert fail_saved["push_result"]["error"] == "boom"
    assert fail_saved["push_result"]["error_class"] == "terminal"
    # A failed push does NOT mark the task pushed.
    assert fail_saved["status"] != "PUSHED"


# ─── BE-2: GET /api/tasks surfaces persisted push outcomes after reload ───────


def test_get_tasks_exposes_persisted_push_result(monkeypatch):
    import ui.server as srv

    ok_task, fail_task = _task(), _task()

    # Simulate the run data AS PERSISTED after a push (i.e. what BE-1 saves and a
    # reload reads back). We monkeypatch load_data so no session dir is needed.
    persisted = {
        "config": {},
        "tasks": [
            {
                **ok_task.model_dump(mode="json"),
                "status": "PUSHED",
                "push_result": {
                    "task_id": str(ok_task.id), "success": True,
                    "jira_issue_key": "PROJ-1",
                    "jira_issue_url": "https://ex.atlassian.net/browse/PROJ-1",
                },
            },
            {
                **fail_task.model_dump(mode="json"),
                "push_result": {
                    "task_id": str(fail_task.id), "success": False,
                    "error": "boom", "error_class": "terminal",
                },
            },
        ],
    }
    monkeypatch.setattr(srv, "load_data", lambda sid=None: dict(persisted))

    client = TestClient(srv.app)
    resp = client.get("/api/tasks", params={"session_id": "s1"})
    assert resp.status_code == 200
    body = resp.json()
    by_id = {t["id"]: t for t in body["tasks"]}

    ok_out = by_id[str(ok_task.id)]["push_result"]
    assert ok_out["success"] is True
    assert ok_out["jira_issue_key"] == "PROJ-1"
    assert ok_out["jira_issue_url"].endswith("/PROJ-1")

    fail_out = by_id[str(fail_task.id)]["push_result"]
    assert fail_out["success"] is False
    assert fail_out["error"] == "boom"
    assert fail_out["error_class"] == "terminal"


# ─── BE-3: POST /api/jira/test is read-only + classifies failures ─────────────


def _jira_test_client(monkeypatch, fake_jira_factory):
    import ui.server as srv
    monkeypatch.setenv("JIRA_SERVER", "https://ex.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "e@x.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "tok")
    # The endpoint must construct the raw jira SDK client, not JiraClient
    # (which does hierarchy/issue-type work). Patch the SDK symbol it uses.
    monkeypatch.setattr(srv, "JIRA", fake_jira_factory, raising=False)
    return TestClient(srv.app)


def test_jira_test_success_never_creates_issue(monkeypatch):
    created = {"issue": False}

    class FakeJira:
        def __init__(self, *a, **k):
            pass

        def myself(self):
            return {"displayName": "Test User", "emailAddress": "e@x.com"}

        def create_issue(self, *a, **k):  # pragma: no cover - must NOT be called
            created["issue"] = True
            raise AssertionError("connection test must not create issues")

    client = _jira_test_client(monkeypatch, FakeJira)
    resp = client.post("/api/jira/test")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert created["issue"] is False


def test_jira_test_401_classifies_user_fixable(monkeypatch):
    class Auth401(Exception):
        status_code = 401

    class FakeJira:
        def __init__(self, *a, **k):
            raise Auth401("Unauthorized (401)")

    client = _jira_test_client(monkeypatch, FakeJira)
    resp = client.post("/api/jira/test")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert body["error_class"] == ErrorClass.USER_FIXABLE.value


def test_jira_test_429_classifies_transient(monkeypatch):
    class Rate429(Exception):
        status_code = 429

    class FakeJira:
        def __init__(self, *a, **k):
            raise Rate429("Too many requests (429)")

    client = _jira_test_client(monkeypatch, FakeJira)
    resp = client.post("/api/jira/test")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert body["error_class"] == ErrorClass.TRANSIENT.value
