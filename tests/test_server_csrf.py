# tests/test_server_csrf.py
"""SERVER-B security lane — CSRF double-submit + CORS config (2.6c).

CSRF is ENFORCED ONLY in hardened mode (auth ON), so existing POST tests (which
send no token) still pass. In hardened mode a state-changing request must carry
an ``X-CSRF-Token`` header equal to the ``sow_csrf`` cookie; otherwise HTTP 403.

CORS must be sensible — no wildcard origin combined with credentials (a browser
would reject that anyway, and it is a footgun).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import ui.server as srv
from auth.deps import SESSION_COOKIE_NAME, get_session_store
from auth.store import FakeSessionStore


@pytest.fixture
def auth_ctx(monkeypatch):
    # In-memory run data so /api/push / task routes don't touch disk.
    data = {
        "config": {"jira_project_key": "PROJ"},
        "tasks": [],
    }
    monkeypatch.setattr(srv, "load_data", lambda sid=None: dict(data))
    monkeypatch.setattr(srv, "save_data", lambda d, sid=None: None)

    store = FakeSessionStore()
    user = store.add_user(email="csrf@calibraint.com")
    raw, _ = store.create_session(user.id)

    # Seed an owned run so ownership doesn't 404 before CSRF is evaluated.
    monkeypatch.setattr(
        srv, "_read_run_meta",
        lambda rid: {"run_id": rid, "owner_id": user.id} if rid == "run-A" else None,
    )

    srv.app.dependency_overrides[get_session_store] = lambda: store
    srv.app.state.session_store = store
    try:
        yield {"store": store, "user": user, "raw": raw}
    finally:
        srv.app.dependency_overrides.pop(get_session_store, None)
        if hasattr(srv.app.state, "session_store"):
            delattr(srv.app.state, "session_store")


def _cookies(raw, csrf=None):
    c = {SESSION_COOKIE_NAME: raw}
    if csrf is not None:
        c[srv.CSRF_COOKIE_NAME] = csrf
    return c


def _get_csrf(client, raw):
    resp = client.get("/api/csrf", cookies=_cookies(raw))
    assert resp.status_code == 200
    return resp.json()["csrf_token"]


# ─── CSRF enforced in hardened mode ──────────────────────────────────────────


def test_state_change_without_token_403(auth_ctx):
    client = TestClient(srv.app)
    resp = client.post(
        "/api/tasks/approve_all",
        params={"session_id": "run-A"},
        cookies=_cookies(auth_ctx["raw"]),
    )
    assert resp.status_code == 403


def test_state_change_with_wrong_token_403(auth_ctx):
    client = TestClient(srv.app)
    tok = _get_csrf(client, auth_ctx["raw"])
    resp = client.post(
        "/api/tasks/approve_all",
        params={"session_id": "run-A"},
        cookies=_cookies(auth_ctx["raw"], csrf=tok),
        headers={srv.CSRF_HEADER_NAME: "WRONG-" + tok},
    )
    assert resp.status_code == 403


def test_state_change_with_correct_token_succeeds(auth_ctx):
    client = TestClient(srv.app)
    tok = _get_csrf(client, auth_ctx["raw"])
    resp = client.post(
        "/api/tasks/approve_all",
        params={"session_id": "run-A"},
        cookies=_cookies(auth_ctx["raw"], csrf=tok),
        headers={srv.CSRF_HEADER_NAME: tok},
    )
    assert resp.status_code == 200


def test_csrf_missing_cookie_but_header_403(auth_ctx):
    """Header present but no matching cookie must fail (double-submit requires both)."""
    client = TestClient(srv.app)
    resp = client.post(
        "/api/tasks/approve_all",
        params={"session_id": "run-A"},
        cookies=_cookies(auth_ctx["raw"]),  # no csrf cookie
        headers={srv.CSRF_HEADER_NAME: "some-token"},
    )
    assert resp.status_code == 403


# ─── settings is a state-changing route: must be CSRF- AND auth-guarded ───────


def test_settings_without_csrf_403(auth_ctx):
    """POST /api/settings rewrites encrypted creds + os.environ — in hardened mode
    it MUST be rejected without a CSRF token, exactly like every other mutating
    route. A cross-site page could otherwise silently repoint LITELLM_API_BASE or
    swap Jira creds."""
    client = TestClient(srv.app)
    resp = client.post(
        "/api/settings",
        json={"provider": "openai"},
        cookies=_cookies(auth_ctx["raw"]),  # valid session, but no CSRF token
    )
    assert resp.status_code == 403


def test_settings_without_session_401(auth_ctx):
    """POST /api/settings must require a valid session in hardened mode (authn)."""
    client = TestClient(srv.app)
    tok = _get_csrf(client, auth_ctx["raw"])
    resp = client.post(
        "/api/settings",
        json={"provider": "openai"},
        # CSRF cookie+header present but NO session cookie → 401 before body runs.
        cookies={srv.CSRF_COOKIE_NAME: tok},
        headers={srv.CSRF_HEADER_NAME: tok},
    )
    assert resp.status_code == 401


# ─── credential-using routes must be auth+CSRF guarded in hardened mode ──────
# (round-2 review: in auth-ON mode both were reachable with NO session cookie —
# /api/jira/test leaked the deployment's Jira identity + confirmed valid creds;
# /api/providers/{id}/models made an outbound call with the server's stored key.)


def test_jira_test_without_session_401(auth_ctx):
    client = TestClient(srv.app)
    tok = _get_csrf(client, auth_ctx["raw"])
    resp = client.post(
        "/api/jira/test",
        cookies={srv.CSRF_COOKIE_NAME: tok},  # CSRF present, but NO session cookie
        headers={srv.CSRF_HEADER_NAME: tok},
    )
    assert resp.status_code == 401


def test_jira_test_without_csrf_403(auth_ctx):
    client = TestClient(srv.app)
    resp = client.post("/api/jira/test", cookies=_cookies(auth_ctx["raw"]))
    assert resp.status_code == 403


def test_provider_models_without_session_401(auth_ctx):
    client = TestClient(srv.app)
    tok = _get_csrf(client, auth_ctx["raw"])
    resp = client.post(
        "/api/providers/openai/models",
        json={},
        cookies={srv.CSRF_COOKIE_NAME: tok},  # CSRF present, but NO session cookie
        headers={srv.CSRF_HEADER_NAME: tok},
    )
    assert resp.status_code == 401


def test_provider_models_without_csrf_403(auth_ctx):
    client = TestClient(srv.app)
    resp = client.post(
        "/api/providers/openai/models",
        json={},
        cookies=_cookies(auth_ctx["raw"]),  # valid session, no CSRF token
    )
    assert resp.status_code == 403


# ─── CSRF NOT enforced in single-user mode (back-compat) ─────────────────────


def test_csrf_not_enforced_when_auth_off(monkeypatch):
    # Ensure OFF.
    srv.app.dependency_overrides.pop(get_session_store, None)
    if hasattr(srv.app.state, "session_store"):
        delattr(srv.app.state, "session_store")
    monkeypatch.setattr(srv, "load_data", lambda sid=None: {"config": {}, "tasks": []})
    monkeypatch.setattr(srv, "save_data", lambda d, sid=None: None)

    client = TestClient(srv.app)
    # No token at all — must still succeed (byte-for-byte old behaviour).
    resp = client.post("/api/tasks/approve_all")
    assert resp.status_code == 200


# ─── CORS is sensible: no wildcard-with-credentials ──────────────────────────


def test_cors_no_wildcard_with_credentials():
    """allow_credentials + allow_origins=['*'] is invalid/insecure — must not ship."""
    cors_mw = None
    for mw in srv.app.user_middleware:
        if "CORSMiddleware" in str(mw.cls):
            cors_mw = mw
            break
    assert cors_mw is not None, "CORS middleware must be configured"

    kwargs = getattr(cors_mw, "kwargs", {}) or getattr(cors_mw, "options", {})
    origins = kwargs.get("allow_origins", [])
    allow_creds = kwargs.get("allow_credentials", False)
    if allow_creds:
        assert "*" not in origins, "wildcard origin with credentials is insecure"
