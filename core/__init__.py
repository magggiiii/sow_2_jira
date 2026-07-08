# core/__init__.py
"""
Core typed result contracts for the strangler-fig harness.

This package holds dependency-free (beyond pydantic) contracts that the
new pipeline harness rests on. New code is additive; existing call sites
are untouched.
"""

from core.results import LLMResult, StageResult, StageStatus

__all__ = ["LLMResult", "StageResult", "StageStatus"]
