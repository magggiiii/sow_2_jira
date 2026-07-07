"""Unit tests for the typed error taxonomy (core/errors.py).

`classify_exception` maps an arbitrary exception onto a 3-way ErrorClass so the
Jira-push + pipeline boundaries can tell callers/UI how to react:
  TRANSIENT    → worth a retry (rate limit, 5xx, timeout, connection blip)
  USER_FIXABLE → the user must change something (bad/expired creds, 401/403)
  TERMINAL     → permanent; retry won't help (bad request, unparseable, bug)
"""

import pytest

from core.errors import ErrorClass, JiraPushError, classify_exception


class _HTTPish(Exception):
    """An exception carrying a `status_code`, like litellm / requests errors."""

    def __init__(self, message, status_code):
        super().__init__(message)
        self.status_code = status_code


# ── TRANSIENT ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "err",
    [
        _HTTPish("rate limited", 429),
        _HTTPish("service unavailable", 503),
        _HTTPish("bad gateway", 502),
        _HTTPish("gateway timeout", 504),
        _HTTPish("request timeout", 408),
        RuntimeError("Rate limit exceeded, too many requests"),
        TimeoutError("connection timed out"),
        ConnectionError("connection reset by peer"),
    ],
)
def test_transient_errors_classified_transient(err):
    assert classify_exception(err) is ErrorClass.TRANSIENT


# ── USER_FIXABLE ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "err",
    [
        _HTTPish("unauthorized", 401),
        _HTTPish("forbidden", 403),
        RuntimeError("Invalid API key provided"),
        RuntimeError("Authentication failed"),
        RuntimeError("insufficient balance"),
    ],
)
def test_user_fixable_errors_classified_user_fixable(err):
    assert classify_exception(err) is ErrorClass.USER_FIXABLE


# ── TERMINAL ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "err",
    [
        _HTTPish("bad request", 400),
        _HTTPish("unprocessable entity", 422),
        ValueError("LLM returned unparseable JSON"),
        RuntimeError("some internal bug with no useful markers"),
    ],
)
def test_terminal_errors_classified_terminal(err):
    assert classify_exception(err) is ErrorClass.TERMINAL


# ── Precedence + explicit classification ─────────────────────────────────────

def test_auth_status_takes_precedence_over_transient_text():
    # A 401 must stay user-fixable even if the message mentions "try again".
    err = _HTTPish("unauthorized - try again later", 401)
    assert classify_exception(err) is ErrorClass.USER_FIXABLE


def test_explicit_error_class_attribute_is_honored():
    # An error that already carries an ErrorClass is trusted verbatim, even
    # when its text would otherwise classify differently.
    err = JiraPushError("looks terminal but is transient", error_class=ErrorClass.TRANSIENT)
    assert err.error_class is ErrorClass.TRANSIENT
    assert classify_exception(err) is ErrorClass.TRANSIENT


def test_jira_push_error_preserves_cause_and_class():
    cause = ValueError("root cause")
    err = JiraPushError("wrapped", error_class=ErrorClass.TERMINAL, cause=cause)
    assert err.__cause__ is cause
    assert classify_exception(err) is ErrorClass.TERMINAL


def test_error_class_is_json_friendly_str_enum():
    # str-enum so it serializes cleanly through pydantic / JSON payloads.
    assert ErrorClass.TRANSIENT == "transient"
    assert ErrorClass.USER_FIXABLE == "user_fixable"
    assert ErrorClass.TERMINAL == "terminal"


# ── Regression: bare 3-digit numbers must not be read as an HTTP status ───────
# A token count / column / index that merely LOOKS like a 4xx/5xx must NOT be
# mistaken for an HTTP status and silently upgraded to TRANSIENT — truncation,
# unparseable output, and internal bugs are documented TERMINAL (fail-safe).

@pytest.mark.parametrize(
    "err",
    [
        ValueError("Expecting value: line 1 column 512 (char 511)"),  # unparseable JSON
        RuntimeError(
            "LLM response truncated at token limit "
            "(finish_reason=length, max_tokens=512)."
        ),
        RuntimeError("index 500 out of range"),
        ValueError("Chunk of 512 tokens exceeds context window"),
    ],
)
def test_bare_numbers_in_terminal_messages_stay_terminal(err):
    assert classify_exception(err) is ErrorClass.TERMINAL


def test_status_in_message_is_extracted_only_when_anchored():
    # A genuine HTTP status expressed only in the message (no status_code attr)
    # is still recognized when it's unambiguously a status.
    assert classify_exception(RuntimeError("Request failed: HTTP 503")) is ErrorClass.TRANSIENT
    assert (
        classify_exception(RuntimeError("server returned status code 429"))
        is ErrorClass.TRANSIENT
    )


def test_real_status_code_attribute_still_transient():
    # Guard against over-correcting: an attribute-borne 5xx is still transient.
    assert classify_exception(_HTTPish("boom", 503)) is ErrorClass.TRANSIENT
