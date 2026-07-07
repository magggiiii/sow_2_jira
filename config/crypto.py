"""Environment-driven secret encryption with key rotation (WAVE 1 STEP 1.1).

This is the forward-looking crypto seam for the hosted/multi-tenant pivot. It
replaces the on-disk ``data/.keyfile`` model with keys supplied purely via the
environment, and supports zero-downtime key rotation via ``MultiFernet``:

    APP_ENC_KEY       primary key — used to encrypt new secrets       (required)
    SOW_FERNET_KEY    alias for APP_ENC_KEY (back-compat)             (optional)
    APP_ENC_KEY_OLD   previous key — can still *decrypt* old secrets  (optional)

Rotation flow: deploy with ``APP_ENC_KEY`` = new key and ``APP_ENC_KEY_OLD`` =
previous key so both can decrypt; call :func:`rotate_secret` to re-encrypt each
stored ciphertext under the new key; once everything is rotated, drop
``APP_ENC_KEY_OLD``.

Design notes:
- No disk keyfile is ever read or written here.
- Keys are validated lazily on first use (not at import) so importing this
  module never crashes test collection or a process that hasn't set the key
  yet; the clear error is raised the moment encryption/decryption is attempted.
- Errors never include the key material or the ciphertext — decrypt failures are
  surfaced (not swallowed) but without leaking secrets.

``SettingsManager`` (config/settings.py) intentionally still owns its own Fernet
until the WAVE 2 cutover — nothing flips to this module yet.
"""

from __future__ import annotations

import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

__all__ = [
    "CryptoError",
    "CryptoConfigError",
    "SecretDecryptError",
    "encrypt_secret",
    "decrypt_secret",
    "rotate_secret",
    "reset_fernet_cache",
]


# ─── errors ──────────────────────────────────────────────────────────────────


class CryptoError(Exception):
    """Base class for all crypto-seam errors."""


class CryptoConfigError(CryptoError):
    """The encryption key is missing or malformed (a deploy/config problem)."""


class SecretDecryptError(CryptoError):
    """A ciphertext could not be decrypted with any configured key."""


# ─── key loading ─────────────────────────────────────────────────────────────

_cached: Optional[MultiFernet] = None
# Snapshot of the env values the cache was built from, so a rotation in-process
# (env change) rebuilds instead of serving a stale MultiFernet.
_cached_env: Optional[tuple] = None


def _current_env() -> tuple:
    return (
        os.environ.get("APP_ENC_KEY"),
        os.environ.get("SOW_FERNET_KEY"),
        os.environ.get("APP_ENC_KEY_OLD"),
    )


def _make_fernet(raw: str, *, var_name: str) -> Fernet:
    """Build a Fernet, translating malformed keys into a non-leaking error."""
    try:
        return Fernet(raw.encode("utf-8") if isinstance(raw, str) else raw)
    except (ValueError, TypeError):  # malformed base64 / wrong length
        # Deliberately does NOT include the key value or the underlying message
        # (which can echo the key) — only the variable name and the requirement.
        raise CryptoConfigError(
            f"{var_name} is malformed: it must be a 32-byte url-safe "
            "base64-encoded Fernet key (see Fernet.generate_key())."
        ) from None


def _build() -> MultiFernet:
    primary = os.environ.get("APP_ENC_KEY") or os.environ.get("SOW_FERNET_KEY")
    if not primary:
        raise CryptoConfigError(
            "APP_ENC_KEY (or its alias SOW_FERNET_KEY) is not set; cannot "
            "encrypt or decrypt secrets."
        )
    primary_var = "APP_ENC_KEY" if os.environ.get("APP_ENC_KEY") else "SOW_FERNET_KEY"
    keys = [_make_fernet(primary, var_name=primary_var)]

    old = os.environ.get("APP_ENC_KEY_OLD")
    if old:
        keys.append(_make_fernet(old, var_name="APP_ENC_KEY_OLD"))

    return MultiFernet(keys)


def _get_fernet() -> MultiFernet:
    global _cached, _cached_env
    env = _current_env()
    if _cached is None or _cached_env != env:
        _cached = _build()
        _cached_env = env
    return _cached


def reset_fernet_cache() -> None:
    """Drop the cached MultiFernet (used by tests and after key rotation)."""
    global _cached, _cached_env
    _cached = None
    _cached_env = None


# ─── public API ──────────────────────────────────────────────────────────────


def encrypt_secret(value: str) -> str:
    """Encrypt ``value`` under the primary key; return a str token."""
    return _get_fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(token: str) -> str:
    """Decrypt ``token`` using any configured key.

    Raises :class:`SecretDecryptError` (without leaking the token) if no key can
    decrypt it. Configuration problems surface as :class:`CryptoConfigError`.
    """
    fernet = _get_fernet()
    try:
        return fernet.decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        raise SecretDecryptError(
            "Could not decrypt secret with any configured key "
            "(APP_ENC_KEY / APP_ENC_KEY_OLD). The key may have rotated away "
            "or the ciphertext is corrupt."
        ) from None


def rotate_secret(token: str) -> str:
    """Re-encrypt ``token`` under the primary key (decrypting with any key).

    Returns the new ciphertext. After rotating every stored secret you can drop
    ``APP_ENC_KEY_OLD``. Raises :class:`SecretDecryptError` if the token cannot
    be decrypted by any configured key.
    """
    fernet = _get_fernet()
    try:
        return fernet.rotate(token.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        raise SecretDecryptError(
            "Could not rotate secret: no configured key can decrypt it."
        ) from None
