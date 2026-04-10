# models/eval_schemas.py

from __future__ import annotations
from typing import List, Optional
from pydantic import BaseModel, Field

# ─── Evaluation Schemas ──────────────────────────────────────────────────────

class GoldenTicket(BaseModel):
    """Matches RawTask but represents the ground-truth expected values."""
    title: str
    short_description: str
    acceptance_criteria: Optional[List[str]] = None
    use_case: Optional[str] = None
    considerations_constraints: Optional[List[str]] = None
    deliverables: Optional[List[str]] = None
    mockup_prototype: Optional[str] = None
    # No confidence field in ground truth as it's 100% by definition

class GoldenEpic(BaseModel):
    """Represents a logical Epic (SOW section) and its associated tickets."""
    title: str
    short_description: str
    tickets: List[GoldenTicket] = Field(default_factory=list)

class HierarchicalDatasetItem(BaseModel):
    """The structure for Langfuse expected_output containing the Epic and Stories."""
    epic: GoldenEpic
    # This matches what we expect the extraction engine to produce for a section
