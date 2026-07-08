# tests/test_run_config_max_run_cost.py
"""R2: RunConfig.max_run_cost budget field + <=0 validator.

The orchestrator's WAVE-7 SC-ORCH cost kill-switch reads ``max_run_cost`` via
getattr with a None fallback (pipeline/orchestrator.py). This exercises the real
field and its validator: None = no budget (allowed), a positive float sets the
hard USD ceiling, and <=0 is a nonsensical budget that must be rejected.
"""

import pytest
from pydantic import ValidationError

from models.schemas import JiraHierarchy, LLMMode, RunConfig


def _base_kwargs(**overrides):
    kwargs = dict(
        sow_pdf_path="/tmp/x.pdf",
        llm_mode=LLMMode.API,
        jira_hierarchy=JiraHierarchy.FLAT,
        jira_project_key="PROJ",
    )
    kwargs.update(overrides)
    return kwargs


def test_max_run_cost_defaults_to_none():
    cfg = RunConfig(**_base_kwargs())
    assert cfg.max_run_cost is None


def test_max_run_cost_positive_is_set():
    cfg = RunConfig(**_base_kwargs(max_run_cost=12.5))
    assert cfg.max_run_cost == 12.5


def test_max_run_cost_zero_raises():
    with pytest.raises(ValidationError):
        RunConfig(**_base_kwargs(max_run_cost=0))


def test_max_run_cost_negative_raises():
    with pytest.raises(ValidationError):
        RunConfig(**_base_kwargs(max_run_cost=-1))
