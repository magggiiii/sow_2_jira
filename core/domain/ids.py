# core/domain/ids.py
"""Canonical identifier factories (WAVE 1 STEP 1.5).

One place to mint identifiers so the timestamp-slug and ``uuid4()[:8]`` schemes
(collision-prone, and awkward as primary keys for the DB pivot) are retired in
favor of full UUID4 strings.
"""

from __future__ import annotations

from uuid import uuid4


def make_run_id() -> str:
    """Return a fresh, collision-resistant run identifier (full UUID4 string)."""
    return str(uuid4())
