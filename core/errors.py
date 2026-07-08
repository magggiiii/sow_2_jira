# core/errors.py
"""Typed error taxonomy for the SOW-to-Jira harness.

Dependency-free (stdlib only) so it can sit at the inward core of the hexagon
and be imported by any layer without creating a cycle.

The headline export is `ErrorClass` + `classify_exception`: a 3-way bucketing of
*how a caller/UI should react* to a failure, independent of the concrete
exception type. Error boundaries (the Jira push and the pipeline/server task
wrappers) classify a caught exception and surface the class so the UI can offer
"retry" (transient), "fix your settings" (user-fixable), or "this failed"
(terminal) instead of showing an opaque string.

This is the bounded slice of the planned `harn-8` taxonomy: the enum + a
classifier + a `JiraPushError` that carries its class. Migrating every raise
site to a full `SowError`/`AdapterError` hierarchy is deliberately out of scope.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Optional


class ErrorClass(str, Enum):
    """How a failure should be reacted to.

    A str-enum so it serializes cleanly through pydantic models and JSON.

    TRANSIENT
        Likely to succeed if retried later — rate limits, 5xx, timeouts,
        connection blips. The UI can offer a retry.
    USER_FIXABLE
        The user must change something to proceed — invalid/expired
        credentials, missing provider config, permission (401/403),
        insufficient balance. The UI should prompt the user to fix it.
    TERMINAL
        A permanent failure a retry won't fix and the user can't trivially
        resolve — malformed request (400/422), unparseable model output,
        truncation, or an internal bug. The UI should surface it as failed.
    """

    TRANSIENT = "transient"
    USER_FIXABLE = "user_fixable"
    TERMINAL = "terminal"


class JiraPushError(RuntimeError):
    """A Jira push failure that carries its :class:`ErrorClass`.

    Lets the push boundary raise/propagate a failure whose reaction class is
    already decided, instead of re-deriving it from the message downstream.
    `classify_exception` honors the attached class verbatim.
    """

    def __init__(
        self,
        message: str,
        *,
        error_class: ErrorClass,
        cause: Optional[BaseException] = None,
    ) -> None:
        super().__init__(message)
        self.error_class = error_class
        if cause is not None:
            # Preserve the chain even when raised without an active `from`.
            self.__cause__ = cause


# ── classification internals ─────────────────────────────────────────────────

# Auth / config / balance markers → the user must fix something.
_USER_FIXABLE_MARKERS = (
    "invalid api key",
    "invalid_api_key",
    "token expired",
    "token expired or incorrect",
    "unauthorized",
    "authentication",
    "forbidden",
    "permission denied",
    "insufficient balance",
    "no resource package",
    "provider not provided",
    "llm provider not provided",
)

# Exception TYPE-name fragments that mean "transient" regardless of message
# (covers litellm/openai/requests error classes without importing them).
_TRANSIENT_TYPE_MARKERS = (
    "ratelimit",
    "timeout",
    "apiconnection",
    "connectionerror",
    "serviceunavailable",
)

# Message fragments that mean "transient".
_TRANSIENT_MARKERS = (
    "rate limit",
    "too many requests",
    "timeout",
    "timed out",
    "temporarily unavailable",
    "service unavailable",
    "connection reset",
    "try again",
)


# Only read a 3-digit code from a message when it is clearly an HTTP status —
# anchored to a "status"/"HTTP" cue. A bare number (a token count, JSON column,
# list index) must NOT be mistaken for a status and silently upgraded to
# TRANSIENT; unknown errors stay TERMINAL (fail-safe). Attribute-borne codes
# (status_code / response.status_code) remain the primary, unambiguous source.
_STATUS_IN_MESSAGE = re.compile(
    r"(?:status(?:[ _]?code)?|http)\D{0,4}(4\d\d|5\d\d)\b", re.IGNORECASE
)


def _extract_status_code(err: BaseException) -> Optional[int]:
    """Best-effort HTTP status extraction.

    Prefers the exception's ``status_code`` / ``response.status_code`` (how
    litellm/openai/requests/jira errors expose it); only falls back to the
    message when a 3-digit code is anchored to a status/HTTP cue.
    """
    code = getattr(err, "status_code", None)
    if isinstance(code, int):
        return code
    response = getattr(err, "response", None)
    response_code = getattr(response, "status_code", None)
    if isinstance(response_code, int):
        return response_code
    match = _STATUS_IN_MESSAGE.search(str(err))
    return int(match.group(1)) if match else None


def classify_exception(err: BaseException) -> ErrorClass:
    """Bucket an exception into an :class:`ErrorClass`.

    Order matters: an explicit class wins; auth (user-fixable) is checked before
    transient so a 401 with a "try again" message stays user-fixable; anything
    unrecognized is TERMINAL (fail safe — we don't silently retry the unknown).
    """
    # 1. Trust an explicitly attached class (e.g. JiraPushError).
    explicit = getattr(err, "error_class", None)
    if isinstance(explicit, ErrorClass):
        return explicit

    text = str(err).lower()
    status = _extract_status_code(err)

    # 2. USER_FIXABLE — auth / credentials / config.
    if status in (401, 403):
        return ErrorClass.USER_FIXABLE
    if any(marker in text for marker in _USER_FIXABLE_MARKERS):
        return ErrorClass.USER_FIXABLE

    # 3. TRANSIENT — rate limits, timeouts, connection blips, 5xx.
    if status in (408, 429) or (status is not None and 500 <= status <= 599):
        return ErrorClass.TRANSIENT
    type_name = type(err).__name__.lower()
    if any(marker in type_name for marker in _TRANSIENT_TYPE_MARKERS):
        return ErrorClass.TRANSIENT
    if any(marker in text for marker in _TRANSIENT_MARKERS):
        return ErrorClass.TRANSIENT

    # 4. Everything else is terminal.
    return ErrorClass.TERMINAL
