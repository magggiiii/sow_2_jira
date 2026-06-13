# tests/test_agent_runner.py
"""
Tests for the AgentRunner / AgentSpec seam (core/agent_runner.py,
core/agent_spec.py).

These use a FAKE LLMProvider that returns canned JSON — no network, no litellm.
The fake mirrors the real ``LLMClient.complete_json`` surface (it structurally
satisfies ``core.ports.LLMProvider``) and records the kwargs it was called with
so we can prove the runner forwards the SAME call the agents make today.

Coverage:
- run() returns StageResult.ok carrying the parsed output (list and dict).
- run() with a response_model validates the parsed JSON into the model.
- A malformed payload (fails response_model validation) yields StageResult.failed.
- A provider error (unparseable JSON / call failure) yields StageResult.failed.
- The complete_json passthrough returns the identical shape complete_json
  returns today, and forwards every kwarg unchanged.
"""

from __future__ import annotations

import pathlib
import sys

import pytest
from pydantic import BaseModel

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from core.agent_runner import AgentRunner
from core.agent_spec import AgentSpec
from core.ports import LLMProvider
from core.results import StageResult, StageStatus


# ─── Fake LLM provider ────────────────────────────────────────────────────────


class FakeLLM:
    """
    Canned-JSON stand-in for ``pipeline.llm_client.LLMClient``.

    Records each call's kwargs in ``self.calls`` and returns ``self.canned``.
    If ``raises`` is set it is raised instead (to simulate a call failure /
    unparseable JSON, both of which the real client surfaces as exceptions).
    """

    def __init__(self, canned=None, raises: Exception | None = None):
        self.canned = canned
        self.raises = raises
        self.calls: list[dict] = []

    def complete(
        self,
        prompt: str,
        system: str = "You are a helpful assistant.",
        temperature: float = 0.0,
        max_tokens: int = 4096,
        agent_name: str = "unknown",
        node_id: str = "",
    ) -> str:  # present so the fake satisfies the full LLMProvider port
        raise NotImplementedError

    def complete_json(
        self,
        prompt: str,
        system: str = "You are a precise JSON extraction assistant.",
        agent_name: str = "unknown",
        node_id: str = "",
        max_tokens: int = 8192,
    ):
        self.calls.append(
            {
                "prompt": prompt,
                "system": system,
                "agent_name": agent_name,
                "node_id": node_id,
                "max_tokens": max_tokens,
            }
        )
        if self.raises is not None:
            raise self.raises
        return self.canned


# A Pydantic model used as a response_model target.
class _Classification(BaseModel):
    type: str
    confidence: float
    reason: str


# ─── Fixtures / helpers ─────────────────────────────────────────────────────


def _spec(**kw) -> AgentSpec:
    base = dict(
        name="FakeAgent",
        system_prompt="SYS",
        prompt_template="Classify: {section_title}",
    )
    base.update(kw)
    return AgentSpec(**base)


# ─── The fake satisfies the port ─────────────────────────────────────────────


def test_fake_llm_satisfies_llm_provider_port():
    assert isinstance(FakeLLM(canned=[]), LLMProvider)


# ─── run(): success carries parsed output ─────────────────────────────────────


def test_run_returns_ok_with_parsed_list_output():
    canned = [{"task_id_a": "x", "task_id_b": "y", "decision": "keep_both"}]
    llm = FakeLLM(canned=canned)
    runner = AgentRunner(llm)

    result = runner.run(_spec(), payload={"section_title": "Scope"})

    assert isinstance(result, StageResult)
    assert result.status is StageStatus.OK
    assert result.output == canned
    assert result.agent == "FakeAgent"


def test_run_returns_ok_with_parsed_dict_output():
    canned = {"scratchpad": "thinking", "tasks": []}
    llm = FakeLLM(canned=canned)
    runner = AgentRunner(llm)

    result = runner.run(_spec(), payload={"section_title": "Reqs"})

    assert result.status is StageStatus.OK
    assert result.output == canned


# ─── run(): forwards exactly the agents' current kwargs ───────────────────────


def test_run_forwards_same_kwargs_as_agents_use_today():
    """system=spec.system_prompt, agent_name=spec.name, node_id forwarded.
    max_tokens is left at the provider default (spec.max_tokens is None),
    matching every agent today (none of them pass max_tokens)."""
    llm = FakeLLM(canned={"ok": True})
    runner = AgentRunner(llm)

    runner.run(_spec(), payload={"section_title": "Scope"}, node_id="n1")

    assert len(llm.calls) == 1
    call = llm.calls[0]
    assert call["prompt"] == "Classify: Scope"
    assert call["system"] == "SYS"
    assert call["agent_name"] == "FakeAgent"
    assert call["node_id"] == "n1"
    # No max_tokens override → provider default (8192), unchanged behavior.
    assert call["max_tokens"] == 8192


def test_run_applies_max_tokens_override_only_when_set():
    llm = FakeLLM(canned={"ok": True})
    runner = AgentRunner(llm)

    runner.run(_spec(max_tokens=2048), payload={"section_title": "Scope"})

    assert llm.calls[0]["max_tokens"] == 2048


def test_run_uses_provider_default_system_when_spec_has_none():
    """spec.system_prompt=None must NOT override the provider's default system."""
    llm = FakeLLM(canned={"ok": True})
    runner = AgentRunner(llm)

    runner.run(
        _spec(system_prompt=None),
        payload={"section_title": "Scope"},
    )

    # Provider default system string is preserved (runner didn't pass system).
    assert llm.calls[0]["system"] == "You are a precise JSON extraction assistant."


# ─── run(): response_model validation ─────────────────────────────────────────


def test_run_validates_into_response_model():
    canned = {"type": "actionable", "confidence": 0.9, "reason": "has work items"}
    llm = FakeLLM(canned=canned)
    runner = AgentRunner(llm)

    result = runner.run(
        _spec(response_model=_Classification),
        payload={"section_title": "Scope"},
    )

    assert result.status is StageStatus.OK
    assert isinstance(result.output, _Classification)
    assert result.output.type == "actionable"
    assert result.output.confidence == 0.9


def test_run_malformed_payload_for_response_model_yields_failed():
    """Parsed JSON that doesn't fit the response_model → StageResult.failed."""
    canned = {"type": "actionable"}  # missing required confidence + reason
    llm = FakeLLM(canned=canned)
    runner = AgentRunner(llm)

    result = runner.run(
        _spec(response_model=_Classification),
        payload={"section_title": "Scope"},
    )

    assert result.status is StageStatus.FAILED
    assert result.output is None
    assert "validation" in result.reason.lower()
    assert result.agent == "FakeAgent"


# ─── run(): provider error paths ──────────────────────────────────────────────


def test_run_llm_call_failure_yields_failed():
    """A provider exception (e.g. unparseable JSON → ValueError) → failed."""
    llm = FakeLLM(raises=ValueError("LLM returned unparseable JSON"))
    runner = AgentRunner(llm)

    result = runner.run(_spec(), payload={"section_title": "Scope"})

    assert result.status is StageStatus.FAILED
    assert "llm call failed" in result.reason.lower()
    assert result.agent == "FakeAgent"


def test_run_prompt_render_error_yields_failed():
    """A template needing a key the payload lacks → failed, no LLM call."""
    llm = FakeLLM(canned={"ok": True})
    runner = AgentRunner(llm)

    # Template references {missing} which the payload doesn't provide.
    spec = _spec(prompt_template="needs {missing}")
    result = runner.run(spec, payload={"section_title": "Scope"})

    assert result.status is StageStatus.FAILED
    assert "render" in result.reason.lower()
    assert llm.calls == []  # never reached the provider


# ─── complete_json passthrough: identical shape & forwarding ──────────────────


def test_complete_json_passthrough_returns_same_shape():
    """The runner passthrough returns the EXACT object the client returns."""
    canned = [{"a": 1}, {"b": 2}]
    llm = FakeLLM(canned=canned)
    runner = AgentRunner(llm)

    via_runner = runner.complete_json(
        prompt="P",
        system="S",
        agent_name="ExtractionAgent",
        node_id="n9",
    )
    # Identical value to calling the client directly.
    assert via_runner == canned
    assert via_runner is llm.canned


def test_complete_json_passthrough_forwards_all_kwargs():
    llm = FakeLLM(canned={"ok": True})
    runner = AgentRunner(llm)

    runner.complete_json(
        prompt="P",
        system="S",
        agent_name="DeduplicationAgent",
        node_id="",
        max_tokens=4096,
    )

    call = llm.calls[0]
    assert call == {
        "prompt": "P",
        "system": "S",
        "agent_name": "DeduplicationAgent",
        "node_id": "",
        "max_tokens": 4096,
    }


def test_complete_json_passthrough_defaults_match_port():
    """Calling with only prompt uses the same defaults as LLMProvider.complete_json."""
    llm = FakeLLM(canned=[])
    runner = AgentRunner(llm)

    runner.complete_json(prompt="only-prompt")

    call = llm.calls[0]
    assert call["system"] == "You are a precise JSON extraction assistant."
    assert call["agent_name"] == "unknown"
    assert call["node_id"] == ""
    assert call["max_tokens"] == 8192


def test_complete_json_passthrough_propagates_exceptions():
    """Exceptions from the client propagate unchanged (no swallowing)."""
    llm = FakeLLM(raises=ValueError("unparseable"))
    runner = AgentRunner(llm)

    with pytest.raises(ValueError, match="unparseable"):
        runner.complete_json(prompt="P")
