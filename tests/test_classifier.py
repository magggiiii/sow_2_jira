# tests/test_classifier.py
"""
Wave 2B coverage: SectionClassifier gating decisions.

Three things under test:
1. classify() returns the correct SectionType when the LLM returns a
   well-formed dict; should_extract() respects the type/confidence rules.
2. Low confidence overrides any non-actionable type → still extract.
3. LLM errors and malformed responses fall back to MIXED with confidence 0
   (caller still runs extraction — false negatives are expensive).
"""

from __future__ import annotations

import pathlib
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from pipeline.agents.classifier import (
    ClassificationResult,
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


def _llm_returning(value):
    """Build a mock LLMClient whose complete_json returns the given value."""
    client = MagicMock()
    client.complete_json.return_value = value
    return client


def _llm_raising(exc: Exception):
    client = MagicMock()
    client.complete_json.side_effect = exc
    return client


# ─── classify() shape happy paths ────────────────────────────────────────────

def test_classifier_actionable_section():
    llm = _llm_returning(
        {"type": "actionable", "confidence": 0.9, "reason": "Lists deliverables"}
    )
    audit = _DummyAudit()
    clf = SectionClassifier(
        llm_client=llm, audit_logger=audit, run_id="r1", min_confidence=0.7
    )

    result = clf.classify(_node(), _section_text())
    assert isinstance(result, ClassificationResult)
    assert result.type == SectionType.ACTIONABLE
    assert result.confidence == pytest.approx(0.9)
    assert result.reason == "Lists deliverables"
    assert clf.should_extract(result) is True


def test_classifier_legal_section():
    llm = _llm_returning(
        {"type": "legal", "confidence": 0.95, "reason": "Liability and warranty"}
    )
    clf = SectionClassifier(
        llm_client=llm, audit_logger=_DummyAudit(), run_id="r1", min_confidence=0.7
    )

    result = clf.classify(_node(), _section_text())
    assert result.type == SectionType.LEGAL
    assert result.confidence == pytest.approx(0.95)
    # Confident legal → SKIP extraction.
    assert clf.should_extract(result) is False


def test_classifier_mixed_section_extracts():
    llm = _llm_returning(
        {"type": "mixed", "confidence": 0.8, "reason": "Has both context and work"}
    )
    clf = SectionClassifier(
        llm_client=llm, audit_logger=_DummyAudit(), run_id="r1", min_confidence=0.7
    )

    result = clf.classify(_node(), _section_text())
    assert result.type == SectionType.MIXED
    # MIXED always extracts regardless of confidence.
    assert clf.should_extract(result) is True


# ─── Low-confidence override ─────────────────────────────────────────────────

def test_classifier_low_confidence_extracts():
    """If the LLM says legal but is only 40% sure, we still extract."""
    llm = _llm_returning(
        {"type": "legal", "confidence": 0.4, "reason": "Unclear — could be SOW terms"}
    )
    clf = SectionClassifier(
        llm_client=llm, audit_logger=_DummyAudit(), run_id="r1", min_confidence=0.7
    )

    result = clf.classify(_node(), _section_text())
    assert result.type == SectionType.LEGAL
    # min_confidence default is 0.7 → 0.4 < 0.7 → extract anyway.
    assert clf.should_extract(result) is True


# ─── Error and malformed-response fallbacks ──────────────────────────────────

def test_classifier_llm_error_defaults_to_mixed():
    llm = _llm_raising(RuntimeError("provider timeout"))
    audit = _DummyAudit()
    clf = SectionClassifier(
        llm_client=llm, audit_logger=audit, run_id="r1", min_confidence=0.7
    )

    result = clf.classify(_node(), _section_text())
    assert result.type == SectionType.MIXED
    assert result.confidence == 0.0
    assert "provider timeout" in result.reason or "LLM error" in result.reason
    # Safe default: extract anyway.
    assert clf.should_extract(result) is True
    # Audit-logged the failure.
    assert any(r.get("action") == "CLASSIFY_ERROR" for r in audit.records)


def test_classifier_value_error_defaults_to_mixed():
    """complete_json raises ValueError on unparseable LLM output — same fallback."""
    llm = _llm_raising(ValueError("bad json"))
    clf = SectionClassifier(
        llm_client=llm, audit_logger=_DummyAudit(), run_id="r1", min_confidence=0.7
    )

    result = clf.classify(_node(), _section_text())
    assert result.type == SectionType.MIXED
    assert result.confidence == 0.0
    assert clf.should_extract(result) is True


def test_classifier_invalid_json_defaults_to_mixed():
    """LLM returned valid JSON but it's not a dict (e.g. an array)."""
    llm = _llm_returning(["not", "a", "dict"])
    clf = SectionClassifier(
        llm_client=llm, audit_logger=_DummyAudit(), run_id="r1", min_confidence=0.7
    )

    result = clf.classify(_node(), _section_text())
    assert result.type == SectionType.MIXED
    assert result.confidence == 0.0
    assert clf.should_extract(result) is True


def test_classifier_invalid_type_defaults_to_mixed():
    """Dict is well-formed but the type field is something we don't recognize."""
    llm = _llm_returning({"type": "philosophical", "confidence": 0.9, "reason": "??"})
    clf = SectionClassifier(
        llm_client=llm, audit_logger=_DummyAudit(), run_id="r1", min_confidence=0.7
    )

    result = clf.classify(_node(), _section_text())
    assert result.type == SectionType.MIXED
    assert clf.should_extract(result) is True


def test_classifier_short_section_short_circuits_to_mixed():
    """Snippets under 30 chars never reach the LLM — return MIXED for safety."""
    llm = MagicMock()
    clf = SectionClassifier(
        llm_client=llm, audit_logger=_DummyAudit(), run_id="r1", min_confidence=0.7
    )

    result = clf.classify(_node(), "tiny")
    assert result.type == SectionType.MIXED
    assert result.confidence == 0.0
    assert clf.should_extract(result) is True
    # No LLM call should have been made.
    llm.complete_json.assert_not_called()


def test_classifier_confidence_outside_range_is_clamped():
    """Defensive: an LLM returning 1.7 must not crash the Pydantic validator."""
    llm = _llm_returning(
        {"type": "actionable", "confidence": 1.7, "reason": "overconfident"}
    )
    clf = SectionClassifier(
        llm_client=llm, audit_logger=_DummyAudit(), run_id="r1", min_confidence=0.7
    )

    result = clf.classify(_node(), _section_text())
    assert result.confidence == 1.0
    assert result.type == SectionType.ACTIONABLE
