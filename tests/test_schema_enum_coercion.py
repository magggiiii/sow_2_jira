# tests/test_schema_enum_coercion.py
"""
H-23: real-field coercion. The closed-set string fields promoted to
`_NormalizedStrEnum` must coerce legacy/dirty LLM values at the Pydantic
boundary instead of crashing, while staying behavior-preserving for the
existing `== "merge"` style comparisons in the pipeline.

These feed mixed-case / separator-drifted values (the kind a real LLM emits)
through model construction and assert they land on the right enum member AND
still compare equal to the canonical lowercase string the rest of the code
checks against.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from models.schemas import (
    AcceptanceCriterion,
    DedupDecision,
    DedupDecisionType,
    DependencyKind,
    TaskDependency,
    VerifiedBy,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("merge", DedupDecisionType.MERGE),
        ("MERGE", DedupDecisionType.MERGE),
        (" Merge ", DedupDecisionType.MERGE),
        ("keep-first", DedupDecisionType.KEEP_FIRST),
        ("keep first", DedupDecisionType.KEEP_FIRST),
        ("Keep_Second", DedupDecisionType.KEEP_SECOND),
        ("keep-both", DedupDecisionType.KEEP_BOTH),
    ],
)
def test_dedup_decision_coerces_dirty_values(raw, expected):
    d = DedupDecision(task_id_a="a", task_id_b="b", decision=raw, reason="r")
    assert d.decision is expected
    # Behavior-preserving: downstream `decision.decision in ("merge", ...)`
    # and `.upper()` must keep working.
    assert d.decision == expected.value
    assert d.decision.upper() == expected.value.upper()


def test_dedup_decision_in_tuple_comparison_still_works():
    d = DedupDecision(task_id_a="a", task_id_b="b", decision="KEEP-FIRST", reason="r")
    assert d.decision in ("merge", "keep_first")  # the actual check in deduplication.py


def test_dedup_decision_unknown_raises():
    with pytest.raises(Exception):  # pydantic ValidationError wraps ValueError
        DedupDecision(task_id_a="a", task_id_b="b", decision="teleport", reason="r")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("blocks", DependencyKind.BLOCKS),
        ("Blocks", DependencyKind.BLOCKS),
        ("relates-to", DependencyKind.RELATES_TO),
        ("relates to", DependencyKind.RELATES_TO),
        ("RELATES_TO", DependencyKind.RELATES_TO),
        (" duplicates ", DependencyKind.DUPLICATES),
    ],
)
def test_dependency_kind_coerces_dirty_values(raw, expected):
    dep = TaskDependency(target_ref="t", reason="r", kind=raw)
    assert dep.kind is expected
    # Behavior-preserving: `(dep.kind or "blocks").lower()` dict lookups still work.
    assert (dep.kind or "blocks").lower() == expected.value


def test_dependency_kind_default_is_blocks():
    dep = TaskDependency(target_ref="t", reason="r")
    assert dep.kind == "blocks"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("test", VerifiedBy.TEST),
        ("Test", VerifiedBy.TEST),
        (" REVIEW ", VerifiedBy.REVIEW),
        ("demo", VerifiedBy.DEMO),
        ("inspection", VerifiedBy.INSPECTION),
    ],
)
def test_verified_by_coerces_dirty_values(raw, expected):
    ac = AcceptanceCriterion(condition="x", verified_by=raw)
    assert ac.verified_by is expected
    # Behavior-preserving: f-string rendering into Jira text must stay the value.
    assert f"verified by: {ac.verified_by}" == f"verified by: {expected.value}"
    # and the `(ac.verified_by or "test").lower() == "test"` style check holds
    assert (ac.verified_by or "test").lower() == expected.value


def test_verified_by_default_is_test():
    ac = AcceptanceCriterion(condition="x")
    assert ac.verified_by == "test"
