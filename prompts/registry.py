# prompts/registry.py
"""
Versioned prompt registry (STEP 3.6).

Each prompt lives in its own ``<name>.txt`` file in this directory, where
``name`` is ``<agent>.<kind>.<version>`` — e.g. ``extraction.system.v1`` or
``critic.user.v1``. ``load(name)`` returns the file's exact text (UTF-8),
cached. The six agents source their system/user prompts through here so the
prompt text is data, not code: it can be versioned, diffed, and snapshot-tested
in one place without editing the agents.

The move from the inline ``*_SYSTEM_PROMPT`` / ``*_PROMPT_TEMPLATE`` constants is
BYTE-FAITHFUL — the ``.txt`` files contain the constants' exact bytes (including
``{placeholders}`` and ``{{escaped braces}}`` for the ``str.format`` templates),
guarded by frozen sha256 snapshots in ``tests/test_prompts_registry.py``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent


@lru_cache(maxsize=None)
def load(name: str) -> str:
    """
    Return the prompt text registered under ``name`` (e.g. ``"gap.user.v1"``).

    Reads ``<name>.txt`` from this directory verbatim and caches it, so repeated
    lookups return the same string object. The text is returned unchanged — for
    ``str.format`` templates the caller renders it exactly as before.

    Raises
    ------
    FileNotFoundError
        If no ``<name>.txt`` exists for the requested name.
    """
    path = _PROMPTS_DIR / f"{name}.txt"
    return path.read_text(encoding="utf-8")


__all__ = ["load"]
