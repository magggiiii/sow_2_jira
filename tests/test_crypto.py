"""Tests for config/crypto.py — MultiFernet-based secret encryption with rotation.

This module is the forward-looking crypto seam (WAVE 1 STEP 1.1). It is
standalone and additive: SettingsManager still owns its own Fernet/keyfile until
WAVE 2, so nothing in the running app flips to this module yet. These tests pin
the contract: env-driven keys (APP_ENC_KEY, alias SOW_FERNET_KEY, optional
APP_ENC_KEY_OLD), round-trip, rotation, and clear non-secret-leaking errors.
"""

import pytest
from cryptography.fernet import Fernet

from config import crypto


_KEY_ENV_VARS = ("APP_ENC_KEY", "SOW_FERNET_KEY", "APP_ENC_KEY_OLD")


@pytest.fixture(autouse=True)
def _clean_crypto_env(monkeypatch):
    """Every test starts from a known, empty key environment + fresh cache."""
    for var in _KEY_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    crypto.reset_fernet_cache()
    yield
    crypto.reset_fernet_cache()


def _set_keys(monkeypatch, *, primary=None, old=None, alias=None):
    for var in _KEY_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    if primary is not None:
        monkeypatch.setenv("APP_ENC_KEY", primary.decode() if isinstance(primary, bytes) else primary)
    if alias is not None:
        monkeypatch.setenv("SOW_FERNET_KEY", alias.decode() if isinstance(alias, bytes) else alias)
    if old is not None:
        monkeypatch.setenv("APP_ENC_KEY_OLD", old.decode() if isinstance(old, bytes) else old)
    crypto.reset_fernet_cache()


# ─── round-trip ──────────────────────────────────────────────────────────────


def test_round_trip(monkeypatch):
    _set_keys(monkeypatch, primary=Fernet.generate_key())
    token = crypto.encrypt_secret("hunter2")
    assert token != "hunter2"
    assert "hunter2" not in token
    assert crypto.decrypt_secret(token) == "hunter2"


def test_encrypt_is_nondeterministic(monkeypatch):
    _set_keys(monkeypatch, primary=Fernet.generate_key())
    assert crypto.encrypt_secret("same") != crypto.encrypt_secret("same")


def test_alias_sow_fernet_key(monkeypatch):
    """When APP_ENC_KEY is absent, SOW_FERNET_KEY is used as the alias."""
    _set_keys(monkeypatch, alias=Fernet.generate_key())
    token = crypto.encrypt_secret("via-alias")
    assert crypto.decrypt_secret(token) == "via-alias"


def test_app_enc_key_takes_precedence_over_alias(monkeypatch):
    primary = Fernet.generate_key()
    _set_keys(monkeypatch, primary=primary, alias=Fernet.generate_key())
    token = crypto.encrypt_secret("primary-wins")
    # A fernet built from primary-only must decrypt it.
    assert Fernet(primary).decrypt(token.encode()).decode() == "primary-wins"


# ─── misconfiguration errors (must be clear + not leak secrets) ──────────────


def test_missing_key_raises_clear_error(monkeypatch):
    _set_keys(monkeypatch)  # no keys at all
    with pytest.raises(crypto.CryptoConfigError) as exc:
        crypto.encrypt_secret("x")
    assert "APP_ENC_KEY" in str(exc.value)


def test_malformed_key_raises_clear_error(monkeypatch):
    bad = "this-is-not-a-valid-fernet-key"
    _set_keys(monkeypatch, primary=bad)
    with pytest.raises(crypto.CryptoConfigError) as exc:
        crypto.encrypt_secret("x")
    # The malformed key value must NOT appear in the error message.
    assert bad not in str(exc.value)
    assert "APP_ENC_KEY" in str(exc.value)


def test_decrypt_invalid_token_raises_without_leaking_token(monkeypatch):
    _set_keys(monkeypatch, primary=Fernet.generate_key())
    bogus = "gAAAAABm....definitely-not-a-real-token"
    with pytest.raises(crypto.SecretDecryptError) as exc:
        crypto.decrypt_secret(bogus)
    assert bogus not in str(exc.value)


# ─── rotation ────────────────────────────────────────────────────────────────


def test_rotation_full_cycle(monkeypatch):
    old_key = Fernet.generate_key()
    new_key = Fernet.generate_key()

    # 1. Encrypt a secret while ONLY the old key is configured.
    _set_keys(monkeypatch, primary=old_key)
    token_old = crypto.encrypt_secret("s3cr3t")

    # 2. Roll the keys: new is primary, old is fallback. Both can decrypt.
    _set_keys(monkeypatch, primary=new_key, old=old_key)
    assert crypto.decrypt_secret(token_old) == "s3cr3t"

    # 3. Rotate the token → re-encrypted under the primary (new) key.
    rotated = crypto.rotate_secret(token_old)
    assert rotated != token_old

    # 4. Drop the old key entirely; only the new key remains.
    _set_keys(monkeypatch, primary=new_key)
    assert crypto.decrypt_secret(rotated) == "s3cr3t"

    # 5. The original (old-key) token can no longer be decrypted → proves the
    #    rotation actually moved the ciphertext onto the new key.
    with pytest.raises(crypto.SecretDecryptError):
        crypto.decrypt_secret(token_old)


def test_rotate_requires_key(monkeypatch):
    _set_keys(monkeypatch)
    with pytest.raises(crypto.CryptoConfigError):
        crypto.rotate_secret("anything")
