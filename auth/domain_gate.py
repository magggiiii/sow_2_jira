"""Org email-domain allowlist for Google sign-in — stdlib only.

Only emails whose domain is on the allowlist may complete sign-in. Matching is
case-insensitive. The allowlist is sourced from ``ALLOWED_EMAIL_DOMAINS`` (comma
separated) with the org domain as a sensible default.
"""

from __future__ import annotations

import os
from typing import Iterable

__all__ = ["is_allowed_email", "allowed_domains_from_env", "DEFAULT_ALLOWED_DOMAINS"]

# Sensible default: the org that runs this deployment.
DEFAULT_ALLOWED_DOMAINS: tuple[str, ...] = ("calibraint.com",)

_ENV_VAR = "ALLOWED_EMAIL_DOMAINS"


def _email_domain(email: str) -> str | None:
    """Return the lowercased domain of ``email``, or None if malformed.

    A valid email here has exactly one ``@`` with a non-empty local part and a
    non-empty domain part.
    """
    if not isinstance(email, str):
        return None
    parts = email.strip().split("@")
    if len(parts) != 2:
        return None
    local, domain = parts
    if not local or not domain:
        return None
    return domain.lower()


def is_allowed_email(email: str, allowed_domains: Iterable[str]) -> bool:
    """Return True if ``email``'s domain is in ``allowed_domains`` (case-insensitive)."""
    domain = _email_domain(email)
    if domain is None:
        return False
    normalized = {d.strip().lower() for d in allowed_domains if d and d.strip()}
    return domain in normalized


def allowed_domains_from_env() -> list[str]:
    """Return the configured allowlist from ``ALLOWED_EMAIL_DOMAINS``.

    Values are comma-separated, whitespace-trimmed, and lowercased. Falls back to
    :data:`DEFAULT_ALLOWED_DOMAINS` when the env var is unset or empty.
    """
    raw = os.environ.get(_ENV_VAR, "")
    domains = [d.strip().lower() for d in raw.split(",") if d.strip()]
    if domains:
        return domains
    return list(DEFAULT_ALLOWED_DOMAINS)
