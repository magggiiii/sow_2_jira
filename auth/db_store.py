"""DB-backed ``SessionStore`` over the relational ORM (WAVE 2 STEP 2.5).

``DbSessionStore`` is the persistent counterpart to
``auth.store.FakeSessionStore``: it satisfies the SAME sync
``core.ports.SessionStore`` protocol but stores users and sessions in
``pipeline.db.User`` / ``pipeline.db.Session`` rows. It runs on a *synchronous*
SQLAlchemy sessionmaker so it plugs directly into the existing sync
``auth.deps.current_user`` dependency (FastAPI runs sync deps in a threadpool).

Security invariants (mirrors ``FakeSessionStore``):
- Only ``sha256(raw)`` (``auth.tokens.hash_token``) is ever persisted, in the
  ``sessions.token_hash`` BYTEA column. The raw cookie token is never stored.
- Expired sessions are treated as absent and evicted on access.

Client-supplied UUID primary keys are used for both users and sessions: the
ORM's ``gen_random_uuid()`` server default is Postgres-only, so supplying a UUID
string lets the same models insert on SQLite too (matching
``integrations.repositories``).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from auth.tokens import hash_token, new_session_token
from pipeline.db import Session as SessionRow
from pipeline.db import User as UserRow
from pipeline.db import normalize_sync_db_url

__all__ = ["DbSessionStore", "make_sync_sessionmaker", "DEFAULT_SESSION_TTL"]

# Default session lifetime for newly minted sessions (matches FakeSessionStore).
DEFAULT_SESSION_TTL = timedelta(days=7)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware_utc(value: datetime) -> datetime:
    """Coerce a possibly-naive datetime (SQLite drops tz) to aware UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def make_sync_sessionmaker(url: str) -> sessionmaker:
    """Build a sync sessionmaker from a DB URL (psycopg2 for Postgres).

    Uses ``normalize_sync_db_url`` so a ``postgresql://`` / ``postgres://`` URL
    targets the blocking psycopg2 driver; SQLite URLs pass through unchanged.
    """
    engine = create_engine(normalize_sync_db_url(url))
    return sessionmaker(engine)


class DbSessionStore:
    """Synchronous SQLAlchemy ``SessionStore`` over ``pipeline.db``."""

    def __init__(self, session_factory: sessionmaker):
        self._sessionmaker = session_factory

    # ── seeding helper (not part of the Protocol) ────────────────────────────

    def add_user(
        self,
        email: str,
        google_sub: Optional[str] = None,
        display_name: Optional[str] = None,
        is_active: bool = True,
    ) -> UserRow:
        """Create and persist a ``User`` row; return it.

        ``google_sub`` is unique + NOT NULL in the schema; for the AUTH_MODE=local
        seeded dev-login it defaults to a ``local:<email>`` sentinel so seeded
        users do not collide with real Google subjects.
        """
        with self._sessionmaker() as s:
            user = UserRow(
                id=str(uuid4()),
                email=email,
                google_sub=google_sub or f"local:{email}",
                display_name=display_name,
                is_active=is_active,
            )
            s.add(user)
            s.commit()
            s.refresh(user)
            s.expunge(user)
            return user

    # ── SessionStore Protocol ────────────────────────────────────────────────

    def create_session(
        self, user_id: str, expires_at: Optional[datetime] = None
    ) -> tuple[str, SessionRow]:
        """Mint a session for ``user_id``; return ``(raw_token, session)``.

        Only ``sha256(raw_token)`` is stored. ``expires_at`` defaults to
        now + :data:`DEFAULT_SESSION_TTL`.
        """
        raw = new_session_token()
        if expires_at is None:
            expires_at = _now() + DEFAULT_SESSION_TTL
        with self._sessionmaker() as s:
            row = SessionRow(
                id=str(uuid4()),
                user_id=user_id,
                token_hash=hash_token(raw),
                expires_at=expires_at,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
            s.expunge(row)
            return raw, row

    def get_session(self, raw_token: str) -> Optional[SessionRow]:
        """Return the live session for ``raw_token``, or None if unknown/expired.

        Expired sessions are evicted on access.
        """
        if not raw_token:
            return None
        digest = hash_token(raw_token)
        with self._sessionmaker() as s:
            row = s.execute(
                select(SessionRow).where(SessionRow.token_hash == digest)
            ).scalar_one_or_none()
            if row is None:
                return None
            if _as_aware_utc(row.expires_at) <= _now():
                s.delete(row)
                s.commit()
                return None
            s.expunge(row)
            return row

    def get_user(self, user_id: str) -> Optional[UserRow]:
        """Return the ``User`` for ``user_id``, or None if absent."""
        with self._sessionmaker() as s:
            row = s.get(UserRow, user_id)
            if row is not None:
                s.expunge(row)
            return row

    def delete_session(self, raw_token: str) -> None:
        """Revoke the session identified by ``raw_token`` (idempotent)."""
        if not raw_token:
            return
        digest = hash_token(raw_token)
        with self._sessionmaker() as s:
            row = s.execute(
                select(SessionRow).where(SessionRow.token_hash == digest)
            ).scalar_one_or_none()
            if row is not None:
                s.delete(row)
                s.commit()
