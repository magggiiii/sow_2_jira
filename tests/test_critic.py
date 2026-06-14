# tests/test_critic.py
"""
Wave 2C coverage: per-section task critic.

The critic agent reviews extracted tasks for verb-first titles, testable ACs,
atomic scope, and likely duplicates. It auto-fixes low-risk issues (with a
confidence threshold) and flags higher-risk ones via existing TaskFlag values.

Tests cover:
- Empty input shortcuts (no LLM call).
- Confident NON_VERB_TITLE -> title rewritten.
- Low-confidence NON_VERB_TITLE -> title unchanged, LOW_CONFIDENCE flagged.
- Confident UNTESTABLE_AC with structured ACs -> ACs replaced.
- TOO_BROAD -> never auto-fixed; AMBIGUOUS_SCOPE added.
- MISSING_AC at low confidence -> ACs still added (task had none).
- LLM error -> tasks unchanged, empty report.
- Non-list LLM response -> tasks unchanged, empty report.
"""

from __future__ import annotations

import pathlib
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from models.schemas import (
    AcceptanceCriterion,
    AcceptanceCriterionType,
    ManagedTask,
    SourceRef,
    TaskFlag,
)
from pipeline.agents.critic import (
    CritiqueIssue,
    CritiqueReport,
    TaskCritic,
)


# ─── Fixtures / helpers ──────────────────────────────────────────────────────

def _node(node_id: str = "n1", title: str = "Section A") -> dict:
    return {
        "node_id": node_id,
        "title": title,
        "page_start": 1,
        "page_end": 2,
    }


def _source_ref(node_id: str = "n1") -> SourceRef:
    return SourceRef(
        node_id=node_id,
        section_title="Section A",
        page_start=1,
        page_end=2,
        snippet="",
    )


def _make_task(
    title: str = "User Authentication",
    acceptance_criteria: list[AcceptanceCriterion] | None = None,
    confidence: float = 0.8,
) -> ManagedTask:
    return ManagedTask(
        title=title,
        short_description="something",
        acceptance_criteria=acceptance_criteria,
        confidence=confidence,
        source_refs=[_source_ref()],
    )


def _make_critic(llm_response=None, llm_raises: Exception | None = None) -> TaskCritic:
    llm = MagicMock()
    if llm_raises is not None:
        llm.complete_json.side_effect = llm_raises
    else:
        llm.complete_json.return_value = llm_response
    audit = MagicMock()
    return TaskCritic(
        llm_client=llm,
        audit_logger=audit,
        run_id="r1",
        auto_fix_threshold=0.8,
    )


# ─── Tests ───────────────────────────────────────────────────────────────────

def test_empty_tasks_returns_empty_report():
    critic = _make_critic(llm_response=[])  # response is irrelevant; we expect no call
    fixed, report = critic.critique(tasks=[], section_text="anything", node=_node())

    assert fixed == []
    assert isinstance(report, CritiqueReport)
    assert report.reviewed_count == 0
    assert report.auto_fixed_count == 0
    assert report.flagged_count == 0
    # No LLM call when there's nothing to review.
    critic.llm.complete_json.assert_not_called()


def test_non_verb_title_auto_fix():
    task = _make_task(title="User Authentication")
    critic = _make_critic(llm_response=[
        {
            "task_id": str(task.id),
            "issues": ["non_verb_title"],
            "suggested_title": "Implement user authentication API",
            "suggested_acceptance_criteria": None,
            "confidence": 0.9,
            "reason": "Title lacks an action verb.",
        }
    ])

    fixed, report = critic.critique([task], section_text="...", node=_node())

    assert fixed[0].title == "Implement user authentication API"
    assert report.reviewed_count == 1
    assert report.auto_fixed_count == 1
    assert report.flagged_count == 0
    # No spurious LOW_CONFIDENCE flag once auto-fix happened.
    assert TaskFlag.LOW_CONFIDENCE not in task.flags


def test_non_verb_title_low_confidence_flag_only():
    task = _make_task(title="User Authentication")
    critic = _make_critic(llm_response=[
        {
            "task_id": str(task.id),
            "issues": ["non_verb_title"],
            "suggested_title": "Implement user authentication API",
            "suggested_acceptance_criteria": None,
            "confidence": 0.5,  # below default threshold of 0.8
            "reason": "Not sure.",
        }
    ])

    fixed, report = critic.critique([task], section_text="...", node=_node())

    # Title unchanged
    assert fixed[0].title == "User Authentication"
    # Flag added
    assert TaskFlag.LOW_CONFIDENCE in task.flags
    assert report.auto_fixed_count == 0
    assert report.flagged_count == 1


def test_untestable_ac_replaced_when_confident():
    task = _make_task(
        title="Implement billing flow",
        acceptance_criteria=[AcceptanceCriterion(condition="System works correctly")],
    )
    critic = _make_critic(llm_response=[
        {
            "task_id": str(task.id),
            "issues": ["untestable_ac"],
            "suggested_title": None,
            "suggested_acceptance_criteria": [
                {
                    "condition": "POST /billing returns 201 with invoice id",
                    "type": "functional",
                    "verified_by": "test",
                },
                {
                    "condition": "Failed charge logs an audit event",
                    "type": "security",
                    "verified_by": "review",
                },
            ],
            "confidence": 0.9,
            "reason": "Original AC was unverifiable.",
        }
    ])

    fixed, report = critic.critique([task], section_text="billing", node=_node())

    assert fixed[0].acceptance_criteria is not None
    assert len(fixed[0].acceptance_criteria) == 2
    conditions = [ac.condition for ac in fixed[0].acceptance_criteria]
    assert "POST /billing returns 201 with invoice id" in conditions
    assert "Failed charge logs an audit event" in conditions
    # Type/verified_by preserved from suggestion
    sec_ac = next(ac for ac in fixed[0].acceptance_criteria if "audit event" in ac.condition)
    assert sec_ac.type == AcceptanceCriterionType.SECURITY
    assert sec_ac.verified_by == "review"
    assert report.auto_fixed_count == 1


def test_too_broad_always_flagged_never_fixed():
    original_title = "Implement entire reporting module"
    original_acs = [AcceptanceCriterion(condition="Reports work")]
    task = _make_task(title=original_title, acceptance_criteria=list(original_acs))

    critic = _make_critic(llm_response=[
        {
            "task_id": str(task.id),
            "issues": ["too_broad"],
            # Even if the LLM helpfully suggests a rewrite, we must ignore it.
            "suggested_title": "Implement weekly summary report",
            "suggested_acceptance_criteria": [
                {"condition": "Renders 5 widgets", "type": "functional", "verified_by": "test"},
            ],
            "confidence": 0.95,
            "reason": "Spans many sprints.",
        }
    ])

    fixed, report = critic.critique([task], section_text="...", node=_node())

    # Title and ACs unchanged
    assert fixed[0].title == original_title
    assert fixed[0].acceptance_criteria is not None
    assert len(fixed[0].acceptance_criteria) == 1
    assert fixed[0].acceptance_criteria[0].condition == "Reports work"
    # AMBIGUOUS_SCOPE flag added
    assert TaskFlag.AMBIGUOUS_SCOPE in task.flags
    assert report.auto_fixed_count == 0
    assert report.flagged_count == 1


def test_missing_ac_added_at_any_confidence():
    task = _make_task(title="Implement password reset", acceptance_criteria=None)
    critic = _make_critic(llm_response=[
        {
            "task_id": str(task.id),
            "issues": ["missing_ac"],
            "suggested_title": None,
            "suggested_acceptance_criteria": [
                {
                    "condition": "Reset email arrives within 60s",
                    "type": "functional",
                    "verified_by": "test",
                },
            ],
            "confidence": 0.5,  # below threshold, but missing_ac applies anyway
            "reason": "Task had no AC at all.",
        }
    ])

    fixed, report = critic.critique([task], section_text="...", node=_node())

    assert fixed[0].acceptance_criteria is not None
    assert len(fixed[0].acceptance_criteria) == 1
    assert fixed[0].acceptance_criteria[0].condition == "Reset email arrives within 60s"
    assert report.auto_fixed_count == 1


def test_zero_confidence_critique_yields_no_flags_or_mutations():
    # Audit H-3: a critique below the confidence floor (incl. 0.0) must produce
    # NO flag and NO mutation. The conf=0.00 mass-flag bug.
    original_title = "User Authentication"
    task = _make_task(title=original_title)
    original_flags = list(task.flags)

    critic = _make_critic(llm_response=[
        {
            "task_id": str(task.id),
            "issues": ["non_verb_title", "too_broad"],
            "suggested_title": "Implement user authentication API",
            "suggested_acceptance_criteria": None,
            "confidence": 0.0,  # below the floor — must not mutate or flag
            "reason": "Mass-flag at zero confidence.",
        }
    ])

    fixed, report = critic.critique([task], section_text="...", node=_node())

    # Title unchanged, no flags added.
    assert fixed[0].title == original_title
    assert fixed[0].flags == original_flags
    assert TaskFlag.LOW_CONFIDENCE not in task.flags
    assert TaskFlag.AMBIGUOUS_SCOPE not in task.flags
    assert report.auto_fixed_count == 0
    assert report.flagged_count == 0


def test_likely_duplicate_does_not_add_flag():
    # Dedup owns duplicate detection — the critic must not flag LIKELY_DUPLICATE.
    task = _make_task(title="Implement user authentication API")
    original_flags = list(task.flags)

    critic = _make_critic(llm_response=[
        {
            "task_id": str(task.id),
            "issues": ["likely_duplicate"],
            "suggested_title": None,
            "suggested_acceptance_criteria": None,
            "confidence": 0.95,  # high confidence, but still must not flag
            "reason": "Overlaps another task.",
        }
    ])

    fixed, report = critic.critique([task], section_text="...", node=_node())

    # No duplicate flag added by the critic.
    assert fixed[0].flags == original_flags
    assert TaskFlag.LOW_CONFIDENCE not in task.flags
    assert report.auto_fixed_count == 0


def test_llm_error_returns_unmodified_tasks():
    task = _make_task(title="User Authentication")
    original_title = task.title
    original_flags = list(task.flags)

    critic = _make_critic(llm_raises=RuntimeError("provider blew up"))

    fixed, report = critic.critique([task], section_text="...", node=_node())

    assert fixed[0].title == original_title
    assert fixed[0].flags == original_flags
    assert report.reviewed_count == 0
    assert report.auto_fixed_count == 0
    assert report.flagged_count == 0
    assert report.critiques == []


def test_invalid_json_response_returns_unmodified():
    task = _make_task(title="User Authentication")
    original_title = task.title
    original_flags = list(task.flags)

    # LLM returns a dict instead of a list — must be handled gracefully.
    critic = _make_critic(llm_response={"oops": "not a list"})

    fixed, report = critic.critique([task], section_text="...", node=_node())

    assert fixed[0].title == original_title
    assert fixed[0].flags == original_flags
    assert report.reviewed_count == 0
    assert report.auto_fixed_count == 0
    assert report.flagged_count == 0
    assert report.critiques == []


# ─── CONF-2: ConfidenceGate is the flag-floor decision path ────────────────────

def test_flag_gate_is_a_confidence_gate_on_the_floor():
    """The critic's flag floor is wired to core.guardrails.ConfidenceGate."""
    from core.guardrails import ConfidenceGate

    critic = _make_critic(llm_response=[])
    assert isinstance(critic._flag_gate, ConfidenceGate)
    assert critic._flag_gate.field == "confidence"
    assert critic._flag_gate.floor == critic.flag_confidence_floor


def test_critique_just_below_floor_not_flagged_just_at_floor_flagged():
    """Boundary on the gate: below the floor is a no-op; exactly at the floor flags."""
    # Just below the 0.5 floor -> no flag, no mutation.
    below_task = _make_task(title="User Authentication")
    below_flags = list(below_task.flags)
    critic_below = _make_critic(llm_response=[
        {
            "task_id": str(below_task.id),
            "issues": ["too_broad"],
            "suggested_title": None,
            "suggested_acceptance_criteria": None,
            "confidence": 0.49,  # below floor
            "reason": "Just under the floor.",
        }
    ])
    fixed_below, report_below = critic_below.critique(
        [below_task], section_text="...", node=_node()
    )
    assert fixed_below[0].flags == below_flags
    assert report_below.flagged_count == 0

    # Exactly at the 0.5 floor -> flag applied (inclusive >=).
    at_task = _make_task(title="User Authentication")
    critic_at = _make_critic(llm_response=[
        {
            "task_id": str(at_task.id),
            "issues": ["too_broad"],
            "suggested_title": None,
            "suggested_acceptance_criteria": None,
            "confidence": 0.5,  # exactly at floor
            "reason": "At the floor.",
        }
    ])
    fixed_at, report_at = critic_at.critique(
        [at_task], section_text="...", node=_node()
    )
    assert TaskFlag.AMBIGUOUS_SCOPE in fixed_at[0].flags
    assert report_at.flagged_count == 1
