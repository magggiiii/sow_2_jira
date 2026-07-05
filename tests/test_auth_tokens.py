"""Tests for the offline session-token primitives (auth/tokens.py).

Covers entropy/uniqueness of minted tokens, the deterministic sha256 hash that
matches ``pipeline.db.Session.token_hash`` (BYTEA = sha256 of the raw cookie),
and the constant-time comparison helper.
"""

import string

from auth.tokens import hash_token, new_session_token, tokens_equal


def test_new_session_token_is_urlsafe_and_high_entropy():
    tok = new_session_token()
    assert isinstance(tok, str)
    # token_urlsafe(32) yields ~43 chars; be generous but insist on real entropy.
    assert len(tok) >= 32
    allowed = set(string.ascii_letters + string.digits + "-_")
    assert set(tok) <= allowed, f"token has non-urlsafe chars: {tok!r}"


def test_two_new_session_tokens_differ():
    a = new_session_token()
    b = new_session_token()
    assert a != b


def test_hash_token_is_deterministic_32_bytes_and_not_raw():
    raw = new_session_token()
    h1 = hash_token(raw)
    h2 = hash_token(raw)
    assert isinstance(h1, (bytes, bytearray))
    assert bytes(h1) == bytes(h2)  # deterministic
    assert len(h1) == 32  # sha256 digest size
    assert bytes(h1) != raw.encode()  # hash is not the raw token


def test_hash_token_matches_sha256_of_raw():
    import hashlib

    raw = "some-fixed-token-value"
    assert bytes(hash_token(raw)) == hashlib.sha256(raw.encode("utf-8")).digest()


def test_tokens_equal_true_and_false():
    a = new_session_token()
    assert tokens_equal(a, a) is True
    assert tokens_equal(a, new_session_token()) is False
