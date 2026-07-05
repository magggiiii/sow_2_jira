"""Secret-redaction regression tests for pipeline.observability._redact_text.

These lock in the exact leak cases the R0 adversarial reviewers found in the
first observability rewrite (JSON/quoted secrets and Bearer/Basic auth tokens
leaking) WITHOUT re-introducing the false-positives an earlier fix caused
(clobbering ordinary prose and UUID run_id/session_id correlation keys).
"""

import pytest

from pipeline.observability import _redact_text

REDACTED = "***REDACTED***"

# (label, input, the secret substring that MUST NOT survive)
LEAK_CASES = [
    ("env_form", "JIRA_API_TOKEN=abcd1234efgh5678", "abcd1234efgh5678"),
    ("colon_form", "api_key: sk-SECRETvalue999", "sk-SECRETvalue999"),
    ("json_double_quote", '{"api_key": "sk-JSONsecret12345"}', "sk-JSONsecret12345"),
    ("json_single_quote", "{'password': 'p@ssw0rdLEAK'}", "p@ssw0rdLEAK"),
    ("auth_header_bearer", "Authorization: Bearer eyJhbGLEAKtoken0000", "eyJhbGLEAKtoken0000"),
    ("bare_bearer", "Bearer eyJhbGLEAKtoken1111", "eyJhbGLEAKtoken1111"),
    ("basic_scheme", "Authorization: Basic dXNlcjpwYXNzd29yZExFQUs=", "dXNlcjpwYXNzd29yZExFQUs="),
    ("high_entropy_blob", "aws AKIA1234567890ABCDEFghij0987654321XY done", "AKIA1234567890ABCDEFghij0987654321XY"),
]

PRESERVE_CASES = [
    ("prose_auth", "auth failed for user bob"),
    ("prose_authorization", "the authorization was granted yesterday"),
    ("uuid_correlation", "session 3f2504e0-4f89-41d3-9a0c-0305e82c3301 started"),
    ("plain_words", "please review the token bucket rate limiter design"),
]


@pytest.mark.parametrize("label,text,secret", LEAK_CASES, ids=[c[0] for c in LEAK_CASES])
def test_secret_never_survives_redaction(label, text, secret):
    out = _redact_text(text)
    assert secret not in out, f"{label}: secret leaked -> {out!r}"
    assert REDACTED in out, f"{label}: nothing redacted -> {out!r}"


@pytest.mark.parametrize("label,text", PRESERVE_CASES, ids=[c[0] for c in PRESERVE_CASES])
def test_ordinary_text_is_preserved(label, text):
    out = _redact_text(text)
    assert out == text, f"{label}: false-positive redaction -> {out!r}"
