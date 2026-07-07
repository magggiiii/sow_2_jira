# core/results.py
"""
Typed result contracts for the pipeline harness.

These are intentionally dependency-free beyond pydantic v2. ``StageResult``
is the contract every harness stage returns; ``LLMResult`` is the contract
returned by the LLM layer. Both are Pydantic v2 models so they round-trip
cleanly via ``model_dump`` / ``model_validate``.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel


class StageStatus(str, Enum):
    """Outcome of a single harness stage."""

    OK = "OK"              # Stage completed successfully
    DEGRADED = "DEGRADED"  # Stage produced output but with a recoverable issue
    FAILED = "FAILED"      # Stage could not produce usable output
    SKIPPED = "SKIPPED"    # Stage was intentionally not run


class StageResult(BaseModel):
    """
    Uniform result wrapper returned by every harness stage.

    ``output`` is intentionally typed ``Any`` so a stage can carry whatever
    payload it produces (a list of tasks, an index tree, a dict, etc.).
    Use the ``ok`` / ``degraded`` / ``failed`` classmethods to construct
    results with the right status instead of setting it by hand.
    """

    status: StageStatus
    output: Optional[Any] = None
    reason: str = ""
    agent: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    finish_reason: str = ""

    @classmethod
    def ok(cls, output: Any = None, **kw: Any) -> "StageResult":
        """Construct a successful result carrying ``output``."""
        return cls(status=StageStatus.OK, output=output, **kw)

    @classmethod
    def degraded(cls, reason: str, **kw: Any) -> "StageResult":
        """Construct a degraded result (usable output, but flagged)."""
        return cls(status=StageStatus.DEGRADED, reason=reason, **kw)

    @classmethod
    def failed(cls, reason: str, **kw: Any) -> "StageResult":
        """Construct a failed result (no usable output)."""
        return cls(status=StageStatus.FAILED, reason=reason, **kw)


class LLMResult(BaseModel):
    """Normalized result returned by the LLM layer."""

    content: str
    finish_reason: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    usd_cost: float = 0.0
