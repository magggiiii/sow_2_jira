# models/intelligence_schemas.py
"""Consolidated intelligence / evaluation output schemas.

WAVE 7 (GATE D): the Pydantic output contracts that the coverage-check,
critic, classifier, and hierarchical-judge agents produce used to be defined
inline in their respective modules. They are the *data contract* those agents
emit and validate against, so they belong in the schema layer next to the rest
of ``models/``.

Each original module now re-exports its classes from here
(``from models.intelligence_schemas import ...``) so every existing import site
keeps resolving to the SAME class object. The supporting enums (CritiqueIssue,
SectionType) move alongside the models that reference them.

CONF-1 invariant is preserved: confidence fields are the clamped ``UnitInterval``
(a BeforeValidator, not Field ge/le) so out-of-range model values coerce into
[0, 1] rather than failing validation, and no minimum/maximum is emitted into
the JSON schema (Anthropic strict structured output).
"""

from __future__ import annotations

import datetime
from enum import Enum
from typing import Optional, Union
from uuid import UUID

from pydantic import BaseModel, Field

from models.schemas import AcceptanceCriterion, UnitInterval, utcnow

# ─── Coverage check (pipeline/agents/coverage_check.py) ────────────────────────


class MissedItem(BaseModel):
    """A concrete actionable deliverable the LLM thinks was dropped."""
    description: str                              # The missed deliverable, 1-2 sentences
    # CONF-1: clamped into [0,1] by the UnitInterval BeforeValidator; no Field
    # ge/le (emitting minimum/maximum breaks Anthropic strict structured output).
    confidence: UnitInterval                       # Checker confidence this IS a miss
    reason: str                                   # Why it's an actionable miss


class SectionCoverageReport(BaseModel):
    """Per-node semantic coverage outcome."""
    node_id: str
    extracted_count: int
    missed_items: list[MissedItem] = Field(default_factory=list)
    checker_confidence: UnitInterval = 0.0        # CONF-1: clamped into [0,1]
    checked_at: datetime.datetime = Field(default_factory=utcnow)


class CoverageAudit(BaseModel):
    """Top-level Instructor ``response_model`` (C-5).

    The coverage check emits a JSON array of misses, so the validated payload is
    wrapped in a single object with one ``missed_items`` list of MissedItem.
    Each MissedItem is schema-validated (description/reason present, confidence
    clamped into [0,1]); the agent then applies its own min_confidence filter —
    that's domain gating, not validation, so it stays in the agent.
    """
    missed_items: list[MissedItem] = Field(default_factory=list)


# ─── Critic (pipeline/agents/critic.py) ────────────────────────────────────────


class CritiqueIssue(str, Enum):
    NON_VERB_TITLE = "non_verb_title"
    VAGUE_TITLE = "vague_title"
    UNTESTABLE_AC = "untestable_ac"
    TOO_BROAD = "too_broad"
    LIKELY_DUPLICATE = "likely_duplicate"
    MISSING_AC = "missing_ac"
    NOTHING_TO_FIX = "nothing_to_fix"


class TaskCritique(BaseModel):
    task_id: UUID
    issues: list[CritiqueIssue] = Field(default_factory=list)
    suggested_title: Optional[str] = None
    suggested_acceptance_criteria: Optional[list[AcceptanceCriterion]] = None
    confidence: UnitInterval = 0.0  # CONF-1: clamped into [0,1]
    reason: str = ""


class CritiqueReport(BaseModel):
    section_node_id: str
    reviewed_count: int = 0
    auto_fixed_count: int = 0
    flagged_count: int = 0
    critiques: list[TaskCritique] = Field(default_factory=list)


class RawCritique(BaseModel):
    """One critique exactly as the LLM emits it — the permissive Instructor item.

    Intentionally lenient (``task_id``/``issues`` as plain strings, ACs as the
    same Union the extractor accepts) so the agent's existing per-entry
    resilience is preserved: ``_parse_critique`` still coerces ``task_id`` to a
    UUID (skipping the entry on failure), filters ``issues`` down to known
    :class:`CritiqueIssue` values, and normalizes ACs. A single dirty entry is
    dropped rather than failing the whole batch — matching the regex-path
    behavior this replaces.
    """
    task_id: str = ""
    issues: list[str] = Field(default_factory=list)
    suggested_title: Optional[str] = None
    suggested_acceptance_criteria: Optional[list[Union[AcceptanceCriterion, str]]] = None
    confidence: UnitInterval = 0.0  # CONF-1: clamped into [0,1]
    reason: str = ""


class CritiqueBatch(BaseModel):
    """Top-level Instructor ``response_model`` — the critic returns a JSON array,
    so the validated payload is wrapped in a single object with one list field."""
    critiques: list[RawCritique] = Field(default_factory=list)


# ─── Classifier (pipeline/agents/classifier.py) ────────────────────────────────


class SectionType(str, Enum):
    ACTIONABLE = "actionable"      # Contains real work items
    CONTEXT = "context"            # Background, overview, problem statement
    LEGAL = "legal"                # Terms, warranties, liability
    DEFINITIONS = "definitions"    # Glossary, acronyms
    SIGNATURE = "signature"        # Approvals, sign-off pages
    MIXED = "mixed"                # Looks like a mix — extract conservatively


class RawClassification(BaseModel):
    """Exactly what the classifier LLM returns — the Instructor response_model.

    The domain :class:`ClassificationResult` additionally carries ``node_id``
    (assigned by the agent, not produced by the model), so the LLM-facing schema
    is this narrower triple. ``confidence`` is a clamped UnitInterval so an
    out-of-range model value (e.g. 1.7) coerces into range instead of failing
    validation, matching ClassificationResult's CONF-1 invariant.
    """
    type: SectionType
    # CONF-1: clamped by the UnitInterval BeforeValidator; no Field ge/le so the
    # emitted schema has no minimum/maximum (Anthropic strict structured output).
    confidence: UnitInterval
    reason: str = ""


class ClassificationResult(BaseModel):
    node_id: str
    type: SectionType
    # CONF-1: clamped into [0,1] by the UnitInterval BeforeValidator (no ge/le).
    confidence: UnitInterval
    reason: str


# ─── Hierarchical judge (pipeline/evals/judges.py) ─────────────────────────────


class EvaluationScores(BaseModel):
    alignment: float = Field(description="Epic level alignment (0-1)")
    recall: float = Field(description="Story level recall (0-1)")
    fidelity: float = Field(description="Story level fidelity/precision (0-1)")
    hallucination: float = Field(description="Presence of hallucinations (0: none, 1: high)")
    reasoning: str = Field(description="Detailed reasoning for the scores")
