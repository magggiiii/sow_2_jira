# models/eval_schemas.py

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

from models.schemas import AcceptanceCriterion, normalize_acceptance_criteria

# ─── Evaluation Schemas ──────────────────────────────────────────────────────

class GoldenTicket(BaseModel):
    """Matches RawTask but represents the ground-truth expected values."""
    title: str
    short_description: str
    # WAVE 7 GATE D: acceptance_criteria is now the structured
    # list[AcceptanceCriterion] contract (still Optional). Plain-string criteria
    # from existing seeds/tests are coerced via normalize_acceptance_criteria so
    # pre-existing callers do not regress.
    acceptance_criteria: Optional[List[AcceptanceCriterion]] = None
    use_case: Optional[str] = None
    considerations_constraints: Optional[List[str]] = None
    deliverables: Optional[List[str]] = None
    mockup_prototype: Optional[str] = None
    # No confidence field in ground truth as it's 100% by definition

    @field_validator("acceptance_criteria", mode="before")
    @classmethod
    def _coerce_acceptance_criteria(cls, value):
        # Backward-compat seam: strings/dicts/AcceptanceCriterion all normalize
        # to list[AcceptanceCriterion]; None/empty stays None.
        return normalize_acceptance_criteria(value)

class GoldenEpic(BaseModel):
    """Represents a logical Epic (SOW section) and its associated tickets."""
    title: str
    short_description: str
    tickets: List[GoldenTicket] = Field(default_factory=list)

class HierarchicalDatasetItem(BaseModel):
    """The structure for Langfuse expected_output containing the Epic and Stories."""
    epic: GoldenEpic
    # This matches what we expect the extraction engine to produce for a section
