"""Tests for the ``current_user`` FastAPI dependency and the session store.

A minimal single-route app is built here with the store dependency overridden to
a ``FakeSessionStore`` so the whole cookie -> session -> user flow is exercised
end-to-end via ``TestClient``, with no real OIDC/token exchange.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from auth.deps import SESSION_COOKIE_NAME, current_user, get_session_store
from auth.store import FakeSessionStore
from core.ports import Session, SessionStore, User


def _build_app(store: FakeSessionStore) -> FastAPI:
    app = FastAPI()

    @app.get("/me")
    def me(user=Depends(current_user)):
        return {"id": user.id, "email": user.email}

    app.dependency_overrides[get_session_store] = lambda: store
    return app


def test_ports_importable_and_fake_satisfies_protocol():
    # Protocol symbols import cleanly.
    assert User is not None and Session is not None and SessionStore is not None
    assert isinstance(FakeSessionStore(), SessionStore)


def test_session_store_protocol_declares_get_user():
    """The seam contract must include every method ``current_user`` calls.

    ``current_user`` (auth/deps.py) invokes ``store.get_user(session.user_id)``.
    If ``get_user`` is not a declared member of the ``SessionStore`` Protocol,
    ``@runtime_checkable`` isinstance() gives false confidence: a future
    DB-backed store built to satisfy the *advertised* contract could pass
    isinstance yet raise AttributeError at request time. Guard the contract.
    """
    assert "get_user" in dir(SessionStore)


def test_minimal_advertised_store_does_not_satisfy_protocol():
    """A store implementing only the OLD advertised methods must NOT pass isinstance.

    Reproduces the reported defect: build a store with exactly
    create_session/get_session/delete_session (no get_user). Once get_user is
    part of the contract, this store must FAIL isinstance — proving the Protocol
    now actually protects the dependency in ``current_user``.
    """

    class MinimalStore:
        def create_session(self, user_id):  # pragma: no cover - shape only
            ...

        def get_session(self, raw_token):  # pragma: no cover - shape only
            ...

        def delete_session(self, raw_token):  # pragma: no cover - shape only
            ...

    assert not isinstance(MinimalStore(), SessionStore)


def test_ghost_session_missing_user_returns_401():
    """A live session whose user_id no longer resolves must yield 401.

    This exercises the ``user is None`` branch of ``current_user`` — the same
    branch that would have masked the get_user Protocol gap.
    """
    store = FakeSessionStore()
    raw, _sess = store.create_session("ghost-user-id-never-seeded")
    client = TestClient(_build_app(store))
    client.cookies.set(SESSION_COOKIE_NAME, raw)
    resp = client.get("/me")
    assert resp.status_code == 401


def test_inactive_user_returns_401():
    """An authenticated-but-inactive principal must yield 401.

    Covers the ``not user.is_active`` branch of ``current_user``, which the
    docstring claims but no prior test exercised.
    """
    store = FakeSessionStore()
    user = store.add_user(email="inactive@calibraint.com", is_active=False)
    raw, _sess = store.create_session(user.id)
    client = TestClient(_build_app(store))
    client.cookies.set(SESSION_COOKIE_NAME, raw)
    resp = client.get("/me")
    assert resp.status_code == 401


def test_no_cookie_returns_401():
    store = FakeSessionStore()
    client = TestClient(_build_app(store))
    resp = client.get("/me")
    assert resp.status_code == 401


def test_forged_unknown_cookie_returns_401():
    store = FakeSessionStore()
    client = TestClient(_build_app(store))
    client.cookies.set(SESSION_COOKIE_NAME, "totally-forged-token")
    resp = client.get("/me")
    assert resp.status_code == 401


def test_expired_session_returns_401():
    store = FakeSessionStore()
    user = store.add_user(email="expired@calibraint.com")
    # Mint a session that is already expired.
    raw, _sess = store.create_session(
        user.id, expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)
    )
    client = TestClient(_build_app(store))
    client.cookies.set(SESSION_COOKIE_NAME, raw)
    resp = client.get("/me")
    assert resp.status_code == 401


def test_valid_cookie_returns_200_and_user():
    store = FakeSessionStore()
    user = store.add_user(email="valid@calibraint.com")
    raw, sess = store.create_session(user.id)
    assert isinstance(sess, Session)

    client = TestClient(_build_app(store))
    client.cookies.set(SESSION_COOKIE_NAME, raw)
    resp = client.get("/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "valid@calibraint.com"
    assert body["id"] == user.id


def test_get_session_returns_none_for_unknown_and_expired():
    store = FakeSessionStore()
    assert store.get_session("nope") is None

    user = store.add_user(email="u@calibraint.com")
    raw, _ = store.create_session(
        user.id, expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)
    )
    assert store.get_session(raw) is None  # expired -> None


def test_delete_session_removes_it():
    store = FakeSessionStore()
    user = store.add_user(email="u@calibraint.com")
    raw, _ = store.create_session(user.id)
    assert store.get_session(raw) is not None
    store.delete_session(raw)
    assert store.get_session(raw) is None
