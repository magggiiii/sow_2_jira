# tests/test_classifier.py
"""
Wave 2B coverage: SectionClassifier gating decisions.

C-5 Instructor migration: the classifier's single LLM call now routes through
``runner.complete_structured(response_model=RawClassification)`` (Instructor-
validated) instead of ``complete_json`` (regex-scraped dict). The mock-point in
these tests moved accordingly — we stub ``clf.runner.complete_structured`` to
return a validated ``RawClassification`` instance, or to raise ``InstructorError``
to simulate a structured-output failure. Every behavioral assertion on the
``classify()`` / ``should_extract()`` result is preserved.

Three things under test:
1. classify() returns the correct SectionType when the model returns a
   well-formed RawClassification; should_extract() respects the type/confidence
   rules.
2. Low confidence overrides any non-actionable type → still extract.
3. Instructor errors (call failure, unparseable/invalid structured output) fall
   back to MIXED with confidence 0 and are RECORDED in the audit trail (caller
   still runs extraction — false negatives are expensive).
"""

from __future__ import annotations

import pathlib
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from core.agent_runner import InstructorError
from pipeline.agents.classifier import (
    ClassificationResult,
    RawClassification,
    SectionClassifier,
    SectionType,
)


class _DummyAudit:
    def __init__(self):
        self.records = []

    def log(self, **kwargs):
        self.records.append(kwargs)


def _node(node_id: str = "n1", title: str = "Section A") -> dict:
    return {"node_id": node_id, "title": title}


def _section_text(content: str = "x" * 200) -> str:
    # >30 chars so the classifier doesn't short-circuit to the empty-section
    # default. Use 200 chars to mimic a normal snippet.
    return content


def _classifier(audit=None, min_confidence: float = 0.7) -> SectionClassifier:
    """Build a SectionClassifier with a throwaway LLM client (the structured
    call boundary is stubbed per test via ``_with_structured``)."""
    return SectionClassifier(
        llm_client=MagicMock(),
        audit_logger=audit if audit is not None else _DummyAudit(),
        run_id="r1",
        min_confidence=min_confidence,
    )


def _with_structured(clf: SectionClassifier, *, returns=None, raises=None) -> MagicMock:
    """Stub ``clf.runner.complete_structured`` — the new Instructor seam.

    ``returns`` is the validated ``RawClassification`` instance the runner would
    hand back; ``raises`` is an exception to simulate a structured-output
    failure. Returns the mock so tests can assert call/no-call.
    """
    mock = MagicMock()
    if raises is not None:
        mock.side_effect = raises
    else:
        mock.return_value = returns
    clf.runner.complete_structured = mock
    return mock


# ─── classify() shape happy paths ────────────────────────────────────────────

def test_classifier_actionable_section():
    clf = _classifier()
    _with_structured(
        clf,
        returns=RawClassification(
            type=SectionType.ACTIONABLE, confidence=0.9, reason="Lists deliverables"
        ),
    )

    result = clf.classify(_node(), _section_text())
    assert isinstance(result, ClassificationResult)
    assert result.type == SectionType.ACTIONABLE
    assert result.confidence == pytest.approx(0.9)
    assert result.reason == "Lists deliverables"
    assert clf.should_extract(result) is True


def test_classifier_legal_section():
    clf = _classifier()
    _with_structured(
        clf,
        returns=RawClassification(
            type=SectionType.LEGAL, confidence=0.95, reason="Liability and warranty"
        ),
    )

    result = clf.classify(_node(), _section_text())
    assert result.type == SectionType.LEGAL
    assert result.confidence == pytest.approx(0.95)
    # Confident legal → SKIP extraction.
    assert clf.should_extract(result) is False


def test_classifier_mixed_section_extracts():
    clf = _classifier()
    _with_structured(
        clf,
        returns=RawClassification(
            type=SectionType.MIXED, confidence=0.8, reason="Has both context and work"
        ),
    )

    result = clf.classify(_node(), _section_text())
    assert result.type == SectionType.MIXED
    # MIXED always extracts regardless of confidence.
    assert clf.should_extract(result) is True


# ─── Low-confidence override ─────────────────────────────────────────────────

def test_classifier_low_confidence_extracts():
    """If the model says legal but is only 40% sure, we still extract."""
    clf = _classifier()
    _with_structured(
        clf,
        returns=RawClassification(
            type=SectionType.LEGAL, confidence=0.4, reason="Unclear — could be SOW terms"
        ),
    )

    result = clf.classify(_node(), _section_text())
    assert result.type == SectionType.LEGAL
    # min_confidence default is 0.7 → 0.4 < 0.7 → extract anyway.
    assert clf.should_extract(result) is True


# ─── Error and malformed-response fallbacks (now via InstructorError) ─────────

def test_classifier_llm_error_defaults_to_mixed():
    audit = _DummyAudit()
    clf = _classifier(audit=audit)
    _with_structured(clf, raises=InstructorError("provider timeout"))

    result = clf.classify(_node(), _section_text())
    assert result.type == SectionType.MIXED
    assert result.confidence == 0.0
    assert "provider timeout" in result.reason or "LLM error" in result.reason
    # Safe default: extract anyway.
    assert clf.should_extract(result) is True
    # Audit-logged the failure (surfaced, not silently swallowed).
    assert any(r.get("action") == "CLASSIFY_ERROR" for r in audit.records)


def test_classifier_unparseable_structured_output_defaults_to_mixed():
    """Instructor could not produce a valid RawClassification — same fallback."""
    clf = _classifier()
    _with_structured(clf, raises=InstructorError("could not parse structured output"))

    result = clf.classify(_node(), _section_text())
    assert result.type == SectionType.MIXED
    assert result.confidence == 0.0
    assert clf.should_extract(result) is True


def test_classifier_invalid_structure_defaults_to_mixed():
    """A structurally-invalid model response surfaces as InstructorError → MIXED."""
    clf = _classifier()
    _with_structured(clf, raises=InstructorError("validation failed after retries"))

    result = clf.classify(_node(), _section_text())
    assert result.type == SectionType.MIXED
    assert result.confidence == 0.0
    assert clf.should_extract(result) is True


def test_classifier_invalid_type_defaults_to_mixed():
    """An unknown section type can't validate into SectionType → InstructorError → MIXED."""
    clf = _classifier()
    _with_structured(clf, raises=InstructorError("invalid enum value 'philosophical'"))

    result = clf.classify(_node(), _section_text())
    assert result.type == SectionType.MIXED
    assert clf.should_extract(result) is True


def test_classifier_short_section_short_circuits_to_mixed():
    """Snippets under 30 chars never reach the LLM — return MIXED for safety."""
    clf = _classifier()
    structured = _with_structured(
        clf,
        returns=RawClassification(
            type=SectionType.ACTIONABLE, confidence=0.9, reason="unused"
        ),
    )

    result = clf.classify(_node(), "tiny")
    assert result.type == SectionType.MIXED
    assert result.confidence == 0.0
    assert clf.should_extract(result) is True
    # No structured LLM call should have been made.
    structured.assert_not_called()


def test_classifier_confidence_outside_range_is_clamped():
    """Defensive: a model returning 1.7 must not crash the Pydantic validator."""
    clf = _classifier()
    # RawClassification.confidence is a UnitInterval — 1.7 clamps to 1.0 on
    # construction, exactly as the validated structured output would arrive.
    _with_structured(
        clf,
        returns=RawClassification(
            type=SectionType.ACTIONABLE, confidence=1.7, reason="overconfident"
        ),
    )

    result = clf.classify(_node(), _section_text())
    assert result.confidence == 1.0
    assert result.type == SectionType.ACTIONABLE
