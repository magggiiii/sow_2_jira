"""In-memory ``SessionStore`` implementation for offline auth and tests.

``FakeSessionStore`` mirrors the shape a future DB-backed store (over
``pipeline.db.Session`` / ``pipeline.db.User``) will have, so both satisfy the
same ``core.ports.SessionStore`` Protocol. Sessions are keyed by
``hash_token(raw)`` — the raw cookie token is never retained.

Concrete ``User``/``Session`` are lightweight dataclasses that structurally
satisfy the ``core.ports.User`` / ``core.ports.Session`` Protocols.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from auth.tokens import hash_token, new_session_token

__all__ = ["User", "Session", "FakeSessionStore", "DEFAULT_SESSION_TTL"]

# Default session lifetime for newly minted sessions.
DEFAULT_SESSION_TTL = timedelta(days=7)


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class User:
    """Concrete principal satisfying ``core.ports.User``."""

    email: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    is_active: bool = True


@dataclass
class Session:
    """Concrete session satisfying ``core.ports.Session``."""

    user_id: str
    expires_at: datetime
    id: str = field(default_factory=lambda: str(uuid.uuid4()))


class FakeSessionStore:
    """In-memory ``SessionStore`` keyed by ``hash_token(raw)``.

    Not persistent and not thread-safe — intended for offline development and
    tests. It implements the full ``core.ports.SessionStore`` Protocol plus a
    small ``add_user`` helper so tests can seed principals.
    """

    def __init__(self) -> None:
        # digest(bytes) -> Session
        self._sessions: dict[bytes, Session] = {}
        # user_id -> User
        self._users: dict[str, User] = {}

    # ── seeding helpers (not part of the Protocol) ───────────────────────────

    def add_user(self, email: str, is_active: bool = True) -> User:
        """Create and register a ``User``; return it."""
        user = User(email=email, is_active=is_active)
        self._users[user.id] = user
        return user

    def get_user(self, user_id: str) -> Optional[User]:
        """Return the registered ``User`` for ``user_id``, or None."""
        return self._users.get(user_id)

    # ── SessionStore Protocol ────────────────────────────────────────────────

    def create_session(
        self, user_id: str, expires_at: Optional[datetime] = None
    ) -> tuple[str, Session]:
        """Mint a new session for ``user_id``; return ``(raw_token, session)``.

        ``expires_at`` defaults to now + :data:`DEFAULT_SESSION_TTL`. Only the
        sha256 digest of the raw token is stored.
        """
        raw = new_session_token()
        if expires_at is None:
            expires_at = _now() + DEFAULT_SESSION_TTL
        session = Session(user_id=user_id, expires_at=expires_at)
        self._sessions[hash_token(raw)] = session
        return raw, session

    def get_session(self, raw_token: str) -> Optional[Session]:
        """Return the live session for ``raw_token``, or None if unknown/expired.

        Expired sessions are evicted on access.
        """
        if not raw_token:
            return None
        session = self._sessions.get(hash_token(raw_token))
        if session is None:
            return None
        if session.expires_at <= _now():
            # Expired — evict and treat as absent.
            self._sessions.pop(hash_token(raw_token), None)
            return None
        return session

    def delete_session(self, raw_token: str) -> None:
        """Revoke the session identified by ``raw_token`` (idempotent)."""
        if not raw_token:
            return
        self._sessions.pop(hash_token(raw_token), None)
