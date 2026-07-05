"""Tests for the org-domain allowlist gate (auth/domain_gate.py).

The gate is the allowlist applied to Google-sign-in emails: only addresses in an
allowed domain may proceed. Matching is case-insensitive.
"""

import pytest

from auth.domain_gate import allowed_domains_from_env, is_allowed_email


def test_allowed_email_in_allowlist():
    assert is_allowed_email("a@calibraint.com", ["calibraint.com"]) is True


def test_disallowed_domain_rejected():
    assert is_allowed_email("a@evil.com", ["calibraint.com"]) is False


def test_case_insensitive_match():
    assert is_allowed_email("A@Calibraint.COM", ["calibraint.com"]) is True
    assert is_allowed_email("a@calibraint.com", ["CALIBRAINT.COM"]) is True


def test_multiple_allowed_domains():
    domains = ["calibraint.com", "example.org"]
    assert is_allowed_email("x@example.org", domains) is True
    assert is_allowed_email("x@nope.net", domains) is False


def test_malformed_email_rejected():
    assert is_allowed_email("not-an-email", ["calibraint.com"]) is False
    assert is_allowed_email("", ["calibraint.com"]) is False
    assert is_allowed_email("a@", ["calibraint.com"]) is False


def test_allowed_domains_from_env_parses_comma_separated(monkeypatch):
    monkeypatch.setenv("ALLOWED_EMAIL_DOMAINS", "foo.com, Bar.COM ,baz.io")
    domains = allowed_domains_from_env()
    assert "foo.com" in domains
    assert "bar.com" in domains  # normalized to lowercase
    assert "baz.io" in domains


def test_allowed_domains_from_env_default(monkeypatch):
    monkeypatch.delenv("ALLOWED_EMAIL_DOMAINS", raising=False)
    domains = allowed_domains_from_env()
    # A sensible non-empty default (the org domain).
    assert isinstance(domains, list)
    assert len(domains) >= 1
    assert "calibraint.com" in domains
