# tests/test_coverage.py
"""
Tests for the structural CoverageTracker in pipeline/coverage.py.

Focus: get_gaps must honor its advertised min_text_length contract (audit H-7).
An uncovered node only counts as a gap when its content length is >=
min_text_length, so gap recovery does not fire on empty/structural nodes.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from pipeline.coverage import CoverageTracker


def _node(node_id: str, text: str = "", summary: str = "", title: str = "") -> dict:
    return {
        "node_id": node_id,
        "title": title,
        "summary": summary,
        "text": text,
        "page_start": 1,
        "page_end": 1,
        "parent_id": None,
        "parent_chain": [],
        "depth": 0,
    }


def test_get_gaps_filters_short_nodes_by_min_text_length():
    """A long uncovered node qualifies as a gap; a short/structural one does not."""
    long_text = "x" * 150  # >= 100
    nodes = [
        _node("long", text=long_text),
        _node("short", text="tiny", title="Appendix"),  # structural / empty-ish
    ]
    tracker = CoverageTracker(nodes)

    # Both nodes are uncovered.
    gaps = tracker.get_gaps(min_text_length=100)

    gap_ids = [g["node_id"] for g in gaps]
    assert gap_ids == ["long"]


def test_get_gaps_uses_summary_fallback_when_no_text():
    """When 'text' is empty, the summary length is used for the threshold."""
    nodes = [
        _node("via_summary", summary="y" * 120),
        _node("empty", text="", summary="", title="Section"),
    ]
    tracker = CoverageTracker(nodes)

    gaps = tracker.get_gaps(min_text_length=100)

    gap_ids = [g["node_id"] for g in gaps]
    assert gap_ids == ["via_summary"]


def test_get_gaps_excludes_covered_nodes():
    """Covered nodes never appear as gaps even when long."""
    nodes = [_node("a", text="z" * 200), _node("b", text="z" * 200)]
    tracker = CoverageTracker(nodes)
    tracker.mark_covered("a", "TASK-1")

    gaps = tracker.get_gaps(min_text_length=100)

    assert [g["node_id"] for g in gaps] == ["b"]
