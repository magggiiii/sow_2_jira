# tests/test_source_ref_validators.py
"""GATE D1 — SourceRef raising validators.

SourceRef must REJECT (raise ValueError via pydantic v2 validation) two
invariants rather than silently clamping:

1. page_end must be >= page_start (a reference cannot end before it starts).
2. depth must be >= 0 (PageIndex tree depth is non-negative).

RED before this gate: SourceRef(page_start=2, page_end=1) and
SourceRef(depth=-1) construct without error.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from models.schemas import SourceRef


def _valid_kwargs(**overrides):
    base = dict(
        node_id="n1",
        section_title="Section 1",
        page_start=3,
        page_end=5,
        depth=1,
    )
    base.update(overrides)
    return base


def test_source_ref_valid_baseline_constructs():
    ref = SourceRef(**_valid_kwargs())
    assert ref.page_start == 3
    assert ref.page_end == 5
    assert ref.depth == 1


def test_source_ref_equal_pages_allowed():
    # A single-page reference (start == end) is valid.
    ref = SourceRef(**_valid_kwargs(page_start=4, page_end=4))
    assert ref.page_start == ref.page_end == 4


def test_source_ref_page_end_before_start_raises():
    with pytest.raises(ValidationError):
        SourceRef(**_valid_kwargs(page_start=2, page_end=1))


def test_source_ref_negative_depth_raises():
    with pytest.raises(ValidationError):
        SourceRef(**_valid_kwargs(depth=-1))


def test_source_ref_zero_depth_allowed():
    ref = SourceRef(**_valid_kwargs(depth=0))
    assert ref.depth == 0
