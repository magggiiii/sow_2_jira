# tests/test_orchestrator_seam.py
"""
HARNESS-4 seam test: PipelineOrchestrator accepts an injected LLM provider.

These tests are intentionally lightweight — they construct the orchestrator
only (no pipeline run, no network, no real LLM calls) and assert the
constructor honors the injection seam:

  - When ``llm`` is provided, the orchestrator uses that exact instance.
  - When ``llm`` is omitted, today's default LLMClient is constructed.

The fake provider structurally satisfies ``core.ports.LLMProvider`` so the
seam stays honest about the contract it accepts.
"""

from typing import Union

from core.ports import LLMProvider
from models.schemas import JiraHierarchy, LLMMode, RunConfig
from pipeline.llm_client import LLMClient
from pipeline.orchestrator import PipelineOrchestrator


class FakeLLMProvider:
    """Minimal in-memory LLM that satisfies the LLMProvider port.

    Records nothing useful for a run — it exists solely to prove the
    orchestrator stores and would use the injected instance.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def complete(
        self,
        prompt: str,
        system: str = "You are a helpful assistant.",
        temperature: float = 0.0,
        max_tokens: int = 4096,
        agent_name: str = "unknown",
        node_id: str = "",
    ) -> str:
        self.calls.append(("complete", prompt))
        return ""

    def complete_json(
        self,
        prompt: str,
        system: str = "You are a precise JSON extraction assistant.",
        agent_name: str = "unknown",
        node_id: str = "",
        max_tokens: int = 8192,
    ) -> Union[list, dict]:
        self.calls.append(("complete_json", prompt))
        return []


class FakeAudit:
    """No-op audit sink so construction does not touch SQLite."""

    def log(self, *args, **kwargs):
        return None


def _run_config() -> RunConfig:
    return RunConfig(
        sow_pdf_path="/tmp/does-not-exist.pdf",
        llm_mode=LLMMode.CUSTOM,
        jira_hierarchy=JiraHierarchy.EPIC_TASK,
        jira_project_key="TEST",
    )


def _app_config() -> dict:
    return {
        "pipeline": {
            "max_gap_recovery_iterations": 1,
            "max_section_chars": 16000,
        }
    }


def test_fake_provider_satisfies_llm_port():
    # The fake structurally satisfies the runtime-checkable port.
    assert isinstance(FakeLLMProvider(), LLMProvider)


def test_orchestrator_uses_injected_llm():
    fake = FakeLLMProvider()
    orch = PipelineOrchestrator(
        config=_run_config(),
        app_config=_app_config(),
        audit=FakeAudit(),
        llm=fake,
    )
    # The orchestrator must use the exact injected instance...
    assert orch.llm is fake
    assert not isinstance(orch.llm, LLMClient)
    # ...and wire it into the agents that depend on the LLM.
    assert orch.extraction_agent.llm is fake
    assert orch.dedup_agent.llm is fake
    assert orch.gap_agent.llm is fake


def test_orchestrator_defaults_to_llm_client_when_not_injected():
    # No `llm` kwarg => preserve today's behavior (real LLMClient built).
    orch = PipelineOrchestrator(
        config=_run_config(),
        app_config=_app_config(),
        audit=FakeAudit(),
    )
    assert isinstance(orch.llm, LLMClient)
