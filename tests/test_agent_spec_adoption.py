# tests/test_agent_spec_adoption.py
"""
STEP 3.6b: each of the six agents declares its single structured call as an
``AgentSpec`` (on ``self.spec``) and routes it through ``runner.run_structured``.

These pin the new ``.spec`` surface: name, the registry-sourced system prompt,
the response_model, and whether the prompt is a flat ``prompt_template`` or an
instance-bound ``prompt_builder``. The agents' existing behavior tests
(test_extraction.py, test_critic.py, …) already prove the rendered prompt and
the resulting mutations are unchanged; this file documents and guards the wiring.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from pipeline.agents.classifier import (
    CLASSIFIER_PROMPT_TEMPLATE,
    CLASSIFIER_SYSTEM_PROMPT,
    RawClassification,
    SectionClassifier,
)
from pipeline.agents.coverage_check import (
    COVERAGE_SYSTEM_PROMPT,
    CoverageAudit,
    CoverageChecker,
)
from pipeline.agents.critic import CRITIC_SYSTEM_PROMPT, CritiqueBatch, TaskCritic
from pipeline.agents.deduplication import (
    DEDUP_PROMPT_TEMPLATE,
    DEDUP_SYSTEM_PROMPT,
    DedupDecisionList,
    DeduplicationAgent,
)
from pipeline.agents.extraction import (
    EXTRACTION_PROMPT_TEMPLATE,
    EXTRACTION_SYSTEM_PROMPT,
    ExtractionResult,
    TaskExtractionAgent,
)
from pipeline.agents.gap_recovery import (
    GAP_SYSTEM_PROMPT,
    GapRecoveryAgent,
    GapRecoveryResult,
)
from prompts import registry


def _audit():
    return MagicMock()


def _agents():
    llm = MagicMock()
    return {
        "extraction": TaskExtractionAgent(llm, _audit(), "r1"),
        "classifier": SectionClassifier(llm, _audit(), "r1"),
        "dedup": DeduplicationAgent(llm, _audit(), "r1"),
        "critic": TaskCritic(llm, _audit(), "r1"),
        "coverage": CoverageChecker(llm, _audit(), "r1"),
        "gap": GapRecoveryAgent(llm, _audit(), "r1"),
    }


EXPECTED = {
    "extraction": (
        "ExtractionAgent", EXTRACTION_SYSTEM_PROMPT, ExtractionResult, "extraction.system.v1",
    ),
    "classifier": (
        "SectionClassifier", CLASSIFIER_SYSTEM_PROMPT, RawClassification, "classifier.system.v1",
    ),
    "dedup": ("DeduplicationAgent", DEDUP_SYSTEM_PROMPT, DedupDecisionList, "dedup.system.v1"),
    "critic": ("TaskCritic", CRITIC_SYSTEM_PROMPT, CritiqueBatch, "critic.system.v1"),
    "coverage": ("CoverageChecker", COVERAGE_SYSTEM_PROMPT, CoverageAudit, "coverage.system.v1"),
    "gap": ("GapRecoveryAgent", GAP_SYSTEM_PROMPT, GapRecoveryResult, "gap.system.v1"),
}


@pytest.mark.parametrize("key", sorted(EXPECTED))
def test_agent_exposes_spec_with_expected_wiring(key):
    name, system_const, model, system_registry_key = EXPECTED[key]
    spec = _agents()[key].spec

    assert spec.name == name
    # System prompt is the registry-sourced constant (same object).
    assert spec.system_prompt is system_const
    assert spec.system_prompt is registry.load(system_registry_key)
    assert spec.response_model is model


def test_critic_name_matches_class_agent_name():
    # The critic uses self.AGENT_NAME for both audit and the spec; keep them coupled.
    agent = _agents()["critic"]
    assert agent.spec.name == agent.AGENT_NAME


# ─── render parity: template agents render exactly TEMPLATE.format(**payload) ───


def test_extraction_spec_renders_like_inline_template():
    payload = {
        "section_title": "S", "page_start": 1, "page_end": 2,
        "section_text": "body", "hierarchy_context": "CTX",
    }
    spec = _agents()["extraction"].spec
    assert spec.render(payload) == EXTRACTION_PROMPT_TEMPLATE.format(**payload)


def test_classifier_spec_renders_like_inline_template():
    payload = {"section_title": "S", "section_snippet": "snippet"}
    spec = _agents()["classifier"].spec
    assert spec.render(payload) == CLASSIFIER_PROMPT_TEMPLATE.format(**payload)


def test_dedup_spec_renders_like_inline_template():
    payload = {"pairs_json": "[]"}
    spec = _agents()["dedup"].spec
    assert spec.render(payload) == DEDUP_PROMPT_TEMPLATE.format(**payload)


# ─── render parity: builder agents render exactly _build_prompt(*payload) ──────


def test_gap_spec_renders_like_build_prompt():
    agent = _agents()["gap"]
    node = {"node_id": "n1", "title": "T", "page_start": 1, "page_end": 2}
    text = "some section text " * 5
    assert agent.spec.render((node, text)) == agent._build_prompt(node, text)
