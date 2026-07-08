"""Offline authentication core (WAVE 0 — STREAM AUTH).

Dependency-light building blocks for opaque cookie sessions:

- :mod:`auth.tokens`      — mint/hash/compare session tokens (stdlib only).
- :mod:`auth.domain_gate` — org email-domain allowlist for Google sign-in.
- :mod:`auth.store`       — ``SessionStore`` Protocol impl (in-memory fake).
- :mod:`auth.deps`        — ``current_user`` FastAPI dependency.

Real Google OIDC / token exchange is intentionally NOT here — this layer is the
offline core that the hosted flow will plug into. No third-party auth libs.
"""
