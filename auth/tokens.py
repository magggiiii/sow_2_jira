"""Opaque session-token primitives — stdlib only.

The raw token is what lives in the browser cookie; only its sha256 digest is ever
persisted (matching ``pipeline.db.Session.token_hash`` BYTEA = sha256 of the raw
cookie). Comparison is constant-time to avoid timing side-channels.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

__all__ = ["new_session_token", "hash_token", "tokens_equal"]

# 32 bytes of entropy -> ~43-char urlsafe string. Comfortably unguessable.
_TOKEN_NBYTES = 32


def new_session_token() -> str:
    """Return a fresh, high-entropy, URL-safe opaque session token.

    Uses :func:`secrets.token_urlsafe` (CSPRNG). Each call is independent, so
    two calls are astronomically unlikely to collide.
    """
    return secrets.token_urlsafe(_TOKEN_NBYTES)


def hash_token(raw: str) -> bytes:
    """Return the sha256 digest (32 raw bytes) of ``raw``.

    Deterministic: the same input always hashes to the same digest, which is how
    a stored ``token_hash`` is matched against an incoming cookie. The digest is
    NOT reversible to the raw token.
    """
    return hashlib.sha256(raw.encode("utf-8")).digest()


def tokens_equal(a: str, b: str) -> bool:
    """Constant-time equality for two raw tokens.

    Compares the sha256 digests via :func:`hmac.compare_digest` so the check does
    not leak how many leading characters matched.
    """
    return hmac.compare_digest(hash_token(a), hash_token(b))
