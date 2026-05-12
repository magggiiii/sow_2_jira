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
