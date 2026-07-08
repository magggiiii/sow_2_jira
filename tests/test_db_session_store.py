# tests/test_db_session_store.py
"""WAVE 2 STEP 2.5 — DB-backed SessionStore over pipeline.db (sqlite shim).

``DbSessionStore`` is the persistent counterpart to ``auth.store.FakeSessionStore``
— it satisfies the SAME sync ``core.ports.SessionStore`` protocol but stores
users/sessions in the relational ORM (``pipeline.db.User`` / ``.Session``). Only
``sha256(raw)`` is ever persisted; the raw cookie token is never stored. Runs on
a sync SQLAlchemy engine so it plugs into the existing sync ``current_user``
dependency; tested here on file-based sqlite.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from auth.db_store import DbSessionStore
from auth.tokens import hash_token
from core.ports import SessionStore
from pipeline.db import Base
from pipeline.db import Session as SessionRow


@pytest.fixture
def store(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/auth.db")
    Base.metadata.create_all(engine)
    return DbSessionStore(sessionmaker(engine))


def test_satisfies_sessionstore_protocol(store):
    assert isinstance(store, SessionStore)


def test_add_user_and_get_user(store):
    user = store.add_user(email="dev@calibraint.com")
    fetched = store.get_user(user.id)
    assert fetched is not None
    assert fetched.email == "dev@calibraint.com"
    assert fetched.is_active is True


def test_create_and_get_session_roundtrip(store):
    user = store.add_user(email="dev@calibraint.com")
    raw, session = store.create_session(user.id)
    assert isinstance(raw, str) and raw
    got = store.get_session(raw)
    assert got is not None
    assert got.user_id == user.id


def test_only_hash_is_persisted_never_raw(store):
    user = store.add_user(email="dev@calibraint.com")
    raw, _ = store.create_session(user.id)
    # The stored token_hash is sha256(raw); the raw token itself is nowhere.
    sm = store._sessionmaker
    with sm() as s:
        rows = s.execute(select(SessionRow)).scalars().all()
        assert len(rows) == 1
        row = rows[0]
        # The digest is exactly sha256(raw)…
        assert bytes(row.token_hash) == hash_token(raw)
        # …and the raw token appears in NO stored column value (id/user_id/ip/
        # user_agent are strings; token_hash is the digest bytes). This is the
        # real leak check — the tautological "raw not in the 32-byte digest"
        # would always pass regardless of any leak.
        stored_strings = [
            getattr(row, col.name)
            for col in SessionRow.__table__.columns
            if isinstance(getattr(row, col.name), str)
        ]
        assert all(raw not in value for value in stored_strings)


def test_get_session_unknown_token_returns_none(store):
    assert store.get_session("not-a-real-token") is None
    assert store.get_session("") is None


def test_get_session_expired_returns_none(store):
    user = store.add_user(email="dev@calibraint.com")
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    raw, _ = store.create_session(user.id, expires_at=past)
    assert store.get_session(raw) is None


def test_delete_session_is_idempotent(store):
    user = store.add_user(email="dev@calibraint.com")
    raw, _ = store.create_session(user.id)
    assert store.get_session(raw) is not None
    store.delete_session(raw)
    assert store.get_session(raw) is None
    store.delete_session(raw)  # no raise on second delete
    store.delete_session("")   # no raise on empty


def test_current_user_dependency_resolves_via_db_store(store):
    """The store plugs into the existing sync current_user dependency."""
    from auth.deps import current_user

    user = store.add_user(email="dev@calibraint.com")
    raw, _ = store.create_session(user.id)
    resolved = current_user(sow_session=raw, store=store)
    assert resolved.id == user.id
