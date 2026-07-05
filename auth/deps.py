"""FastAPI dependencies for offline auth.

``current_user`` reads the opaque session cookie, resolves it through an injected
``SessionStore``, and returns the authenticated user. A missing, forged, or
expired cookie raises ``HTTPException(401)``.

The store is provided via :func:`get_session_store`, a dependency that callers
(and tests) override with ``app.dependency_overrides[get_session_store]`` to
inject a concrete store (e.g. ``FakeSessionStore``). This module deliberately
imports neither ``ui.server`` nor ``pipeline.observability``.
"""

from __future__ import annotations

from fastapi import Cookie, Depends, HTTPException, status

# Name of the browser cookie holding the raw opaque session token.
SESSION_COOKIE_NAME = "sow_session"

__all__ = ["SESSION_COOKIE_NAME", "get_session_store", "current_user"]

_UNAUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
)


def get_session_store():
    """Return the active ``SessionStore``.

    This default raises: a running app MUST override it via
    ``app.dependency_overrides[get_session_store]`` (or a future composition
    root) with a concrete store. Keeping the default un-wired avoids importing
    or constructing any store at module import time.
    """
    raise RuntimeError(
        "No SessionStore configured. Override get_session_store via "
        "app.dependency_overrides[get_session_store]."
    )


def current_user(
    sow_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    store=Depends(get_session_store),
):
    """Resolve the authenticated user from the session cookie.

    Raises ``HTTPException(401)`` if the cookie is missing, the session is
    unknown/forged/expired, the backing user is gone, or the user is inactive.
    """
    if not sow_session:
        raise _UNAUTHENTICATED

    session = store.get_session(sow_session)
    if session is None:
        raise _UNAUTHENTICATED

    user = store.get_user(session.user_id)
    if user is None or not getattr(user, "is_active", False):
        raise _UNAUTHENTICATED

    return user
