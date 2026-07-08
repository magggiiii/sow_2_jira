# tests/test_server_idor.py
"""SERVER-B security lane — per-user auth wiring (2.5c) + IDOR ownership (2.6a).

THE OVERRIDING CONSTRAINT: hardening ACTIVATES ONLY WHEN AUTH IS CONFIGURED.
When no SessionStore is wired (the default), routes behave exactly as before —
``current_user`` resolves to a fixed default local user and nothing 404s. These
tests flip the gate ON by wiring a ``FakeSessionStore`` onto the app + sending
session cookies, then prove:

  2.5c  data routes require a valid session when auth is configured (401 on
        missing/forged/expired cookie); a stable default user when it is not.
  2.6a  every data route that takes a run_id/session_id resolves ownership and
        returns 404 (NOT 403 — do not leak existence) when the run's owner is
        not the current user. The owner still gets 200.

All disk I/O is monkeypatched via an in-memory run store; no network, no writes.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import ui.server as srv
from auth.deps import SESSION_COOKIE_NAME, get_session_store
from auth.store import FakeSessionStore

# ─── shared in-memory harness ────────────────────────────────────────────────


class _RunHarness:
    """In-memory replacement for the on-disk session store used by the server.

    Backs load_data/save_data/get_sessions and the metadata read/write helpers so
    tests never touch data/sessions/<run_id>/ on disk.
    """

    def __init__(self):
        self.data: dict[str, dict] = {}       # session_id -> pipeline_output dict
        self.meta: dict[str, dict] = {}       # run_id -> metadata dict

    def load(self, sid=None):
        return dict(self.data.get(sid, {"tasks": [], "config": {}}))

    def save(self, d, sid=None):
        self.data[sid] = d


@pytest.fixture
def harness(monkeypatch):
    h = _RunHarness()
    monkeypatch.setattr(srv, "load_data", h.load)
    monkeypatch.setattr(srv, "save_data", h.save)
    # In-memory metadata read/write so ownership persists without disk.
    monkeypatch.setattr(srv, "_read_run_meta", lambda rid: h.meta.get(rid))
    monkeypatch.setattr(
        srv, "_write_run_meta", lambda rid, meta: h.meta.__setitem__(rid, meta)
    )
    monkeypatch.setattr(srv, "_list_run_meta", lambda: list(h.meta.values()))
    return h


def _client_with_store(store):
    """Return a TestClient with auth turned ON via a wired SessionStore."""
    srv.app.dependency_overrides[get_session_store] = lambda: store
    srv.app.state.session_store = store
    client = TestClient(srv.app)
    return client


def _teardown_auth():
    srv.app.dependency_overrides.pop(get_session_store, None)
    if hasattr(srv.app.state, "session_store"):
        delattr(srv.app.state, "session_store")


@pytest.fixture
def auth_on(harness):
    store = FakeSessionStore()
    user_a = store.add_user(email="a@calibraint.com")
    user_b = store.add_user(email="b@calibraint.com")
    raw_a, _ = store.create_session(user_a.id)
    raw_b, _ = store.create_session(user_b.id)
    client = _client_with_store(store)
    try:
        yield {
            "store": store,
            "client": client,
            "user_a": user_a,
            "user_b": user_b,
            "raw_a": raw_a,
            "raw_b": raw_b,
            "harness": harness,
        }
    finally:
        _teardown_auth()


def _cookies(raw):
    return {SESSION_COOKIE_NAME: raw}


def _seed_run(harness, run_id, owner_id, tasks=None):
    """Create an owned run: metadata (owner) + pipeline output data."""
    harness.meta[run_id] = {
        "run_id": run_id,
        "filename": "x.pdf",
        "owner_id": owner_id,
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    harness.data[run_id] = {
        "config": {"jira_project_key": "PROJ"},
        "tasks": tasks
        or [
            {
                "id": "t1",
                "title": "task one",
                "short_description": "d",
                "status": "APPROVED",
                "confidence": 1.0,
                "flags": [],
                "source_refs": [],
            }
        ],
    }


# ─── 2.5c: auth gate — default OFF means no 401 ──────────────────────────────


def test_gate_off_by_default_no_auth_required():
    """With no store wired, current_user resolves to the default user (no 401)."""
    _teardown_auth()  # be defensive against leakage from another test
    client = TestClient(srv.app)
    # A data route with no cookie must still be reachable (byte-for-byte old
    # behaviour): 200, not 401.
    resp = client.get("/api/tasks")
    assert resp.status_code == 200


def test_auth_enabled_flag_reflects_wiring():
    _teardown_auth()
    assert srv.auth_enabled() is False
    store = FakeSessionStore()
    srv.app.state.session_store = store
    try:
        assert srv.auth_enabled() is True
    finally:
        _teardown_auth()
    assert srv.auth_enabled() is False


# ─── 2.5c: auth gate ON — data routes require a valid session ────────────────


def test_gate_on_missing_cookie_401(auth_on):
    client = auth_on["client"]
    resp = client.get("/api/tasks", params={"session_id": "any"})
    assert resp.status_code == 401


def test_gate_on_forged_cookie_401(auth_on):
    client = auth_on["client"]
    resp = client.get(
        "/api/tasks", params={"session_id": "any"}, cookies=_cookies("forged")
    )
    assert resp.status_code == 401


# ─── 2.6a: IDOR — owner 200, other user 404 on EVERY data route ──────────────


def test_owner_reads_own_run(auth_on):
    h = auth_on["harness"]
    _seed_run(h, "run-A", auth_on["user_a"].id)
    resp = auth_on["client"].get(
        "/api/tasks", params={"session_id": "run-A"}, cookies=_cookies(auth_on["raw_a"])
    )
    assert resp.status_code == 200
    assert resp.json()["tasks"][0]["id"] == "t1"


def test_other_user_get_tasks_404(auth_on):
    h = auth_on["harness"]
    _seed_run(h, "run-A", auth_on["user_a"].id)
    resp = auth_on["client"].get(
        "/api/tasks", params={"session_id": "run-A"}, cookies=_cookies(auth_on["raw_b"])
    )
    assert resp.status_code == 404


def test_other_user_get_status_404(auth_on):
    h = auth_on["harness"]
    _seed_run(h, "run-A", auth_on["user_a"].id)
    # active_runs entry stamped with owner id.
    st = srv.ProcessingStatus(run_id="run-A")
    st.owner_id = auth_on["user_a"].id
    srv.active_runs["run-A"] = st
    try:
        resp_owner = auth_on["client"].get(
            "/api/status", params={"session_id": "run-A"}, cookies=_cookies(auth_on["raw_a"])
        )
        assert resp_owner.status_code == 200
        resp_other = auth_on["client"].get(
            "/api/status", params={"session_id": "run-A"}, cookies=_cookies(auth_on["raw_b"])
        )
        assert resp_other.status_code == 404
    finally:
        srv.active_runs.pop("run-A", None)


def test_other_user_cancel_404(auth_on):
    h = auth_on["harness"]
    _seed_run(h, "run-A", auth_on["user_a"].id)

    class _Orch:
        class _Ev:
            def set(self):
                pass

        stop_event = _Ev()

    srv.active_orchestrators["run-A"] = _Orch()
    st = srv.ProcessingStatus(run_id="run-A")
    st.owner_id = auth_on["user_a"].id
    srv.active_runs["run-A"] = st
    try:
        # CSRF token needed for POST in hardened mode — fetch one for each user.
        tok_b = _csrf(auth_on["client"], auth_on["raw_b"])
        resp_other = auth_on["client"].post(
            "/api/cancel/run-A",
            cookies={**_cookies(auth_on["raw_b"]), srv.CSRF_COOKIE_NAME: tok_b},
            headers={srv.CSRF_HEADER_NAME: tok_b},
        )
        assert resp_other.status_code == 404
        tok_a = _csrf(auth_on["client"], auth_on["raw_a"])
        resp_owner = auth_on["client"].post(
            "/api/cancel/run-A",
            cookies={**_cookies(auth_on["raw_a"]), srv.CSRF_COOKIE_NAME: tok_a},
            headers={srv.CSRF_HEADER_NAME: tok_a},
        )
        assert resp_owner.status_code == 200
    finally:
        srv.active_orchestrators.pop("run-A", None)
        srv.active_runs.pop("run-A", None)


def test_other_user_delete_session_404(auth_on, tmp_path, monkeypatch):
    # delete_session removes a real on-disk dir; create one under a tmp data root
    # and point the server's Path lookups at it via cwd so nothing real is touched.
    monkeypatch.chdir(tmp_path)
    sess_dir = tmp_path / "data" / "sessions" / "run-A"
    sess_dir.mkdir(parents=True)
    (sess_dir / "metadata.json").write_text(
        '{"run_id": "run-A", "owner_id": "%s"}' % auth_on["user_a"].id
    )
    # Point the meta reader at the real (tmp) dir so the route sees ownership.
    monkeypatch.setattr(srv, "_read_run_meta", _real_read_meta)

    tok_b = _csrf(auth_on["client"], auth_on["raw_b"])
    resp = auth_on["client"].request(
        "DELETE",
        "/api/sessions/run-A",
        cookies={**_cookies(auth_on["raw_b"]), srv.CSRF_COOKIE_NAME: tok_b},
        headers={srv.CSRF_HEADER_NAME: tok_b},
    )
    assert resp.status_code == 404
    assert sess_dir.exists()  # non-owner delete did NOT remove it
    # owner can still delete
    tok_a = _csrf(auth_on["client"], auth_on["raw_a"])
    resp_owner = auth_on["client"].request(
        "DELETE",
        "/api/sessions/run-A",
        cookies={**_cookies(auth_on["raw_a"]), srv.CSRF_COOKIE_NAME: tok_a},
        headers={srv.CSRF_HEADER_NAME: tok_a},
    )
    assert resp_owner.status_code == 200
    assert not sess_dir.exists()


def _real_read_meta(run_id):
    import json as _json
    from pathlib import Path as _Path
    p = _Path(f"data/sessions/{run_id}/metadata.json")
    if not p.exists():
        return None
    try:
        return _json.loads(p.read_text())
    except Exception:
        return None


def test_other_user_update_task_404(auth_on):
    h = auth_on["harness"]
    _seed_run(h, "run-A", auth_on["user_a"].id)
    tok_b = _csrf(auth_on["client"], auth_on["raw_b"])
    body = {"id": "t1", "title": "hacked", "status": "APPROVED"}
    resp = auth_on["client"].post(
        "/api/tasks",
        params={"session_id": "run-A"},
        json=body,
        cookies={**_cookies(auth_on["raw_b"]), srv.CSRF_COOKIE_NAME: tok_b},
        headers={srv.CSRF_HEADER_NAME: tok_b},
    )
    assert resp.status_code == 404


def test_other_user_add_task_404(auth_on):
    h = auth_on["harness"]
    _seed_run(h, "run-A", auth_on["user_a"].id)
    tok_b = _csrf(auth_on["client"], auth_on["raw_b"])
    resp = auth_on["client"].post(
        "/api/tasks/add",
        params={"session_id": "run-A"},
        json={"title": "x", "short_description": "y"},
        cookies={**_cookies(auth_on["raw_b"]), srv.CSRF_COOKIE_NAME: tok_b},
        headers={srv.CSRF_HEADER_NAME: tok_b},
    )
    assert resp.status_code == 404


def test_other_user_approve_all_404(auth_on):
    h = auth_on["harness"]
    _seed_run(h, "run-A", auth_on["user_a"].id)
    tok_b = _csrf(auth_on["client"], auth_on["raw_b"])
    resp = auth_on["client"].post(
        "/api/tasks/approve_all",
        params={"session_id": "run-A"},
        cookies={**_cookies(auth_on["raw_b"]), srv.CSRF_COOKIE_NAME: tok_b},
        headers={srv.CSRF_HEADER_NAME: tok_b},
    )
    assert resp.status_code == 404


def test_other_user_push_404(auth_on):
    h = auth_on["harness"]
    _seed_run(h, "run-A", auth_on["user_a"].id)
    tok_b = _csrf(auth_on["client"], auth_on["raw_b"])
    resp = auth_on["client"].post(
        "/api/push",
        params={"session_id": "run-A"},
        json={},
        cookies={**_cookies(auth_on["raw_b"]), srv.CSRF_COOKIE_NAME: tok_b},
        headers={srv.CSRF_HEADER_NAME: tok_b},
    )
    assert resp.status_code == 404


def test_sessions_list_filtered_to_owner(auth_on):
    h = auth_on["harness"]
    _seed_run(h, "run-A", auth_on["user_a"].id)
    _seed_run(h, "run-B", auth_on["user_b"].id)
    resp_a = auth_on["client"].get("/api/sessions", cookies=_cookies(auth_on["raw_a"]))
    assert resp_a.status_code == 200
    ids = {s["run_id"] for s in resp_a.json()}
    assert ids == {"run-A"}


# ─── helper: fetch a CSRF token via the client (double-submit cookie) ─────────


def _csrf(client, raw):
    """Establish a CSRF token for the session by hitting the token endpoint."""
    resp = client.get("/api/csrf", cookies=_cookies(raw))
    assert resp.status_code == 200
    return resp.json()["csrf_token"]
