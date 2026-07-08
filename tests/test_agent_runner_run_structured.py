# tests/test_agent_runner_run_structured.py
"""
STEP 3.6b: AgentRunner.run_structured — the spec-driven complement to
``complete_structured``.

``run_structured(spec, payload, node_id=...)`` renders the spec's prompt and
forwards EXACTLY the kwargs the six agents pass to ``complete_structured`` today
(prompt, response_model, system, agent_name, node_id; max_tokens only when the
spec sets it), returning the validated ``response_model`` instance. So an agent
that builds an AgentSpec and calls this is behavior-identical to the inline
``complete_structured`` call it replaces.

These tests stub ``runner.complete_structured`` (run_structured calls
``self.complete_structured``) — no instructor, no network.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from core.agent_runner import AgentRunner, InstructorError
from core.agent_spec import AgentSpec


class _Out(BaseModel):
    x: int = 0


class _FakeLLM:
    """run_structured never touches the llm directly — it calls
    self.complete_structured, which we replace with a capture."""


def _runner_with_capture(return_value):
    runner = AgentRunner(_FakeLLM())
    captured: dict = {}

    def fake_cs(**kwargs):
        captured.clear()
        captured.update(kwargs)
        return return_value

    runner.complete_structured = fake_cs
    return runner, captured


def test_run_structured_renders_template_and_forwards_kwargs():
    spec = AgentSpec(
        name="X", system_prompt="SYS", prompt_template="Hello {who}", response_model=_Out
    )
    out = _Out(x=5)
    runner, captured = _runner_with_capture(out)

    result = runner.run_structured(spec, {"who": "world"}, node_id="n1")

    assert result is out
    assert captured["prompt"] == "Hello world"
    assert captured["response_model"] is _Out
    assert captured["system"] == "SYS"
    assert captured["agent_name"] == "X"
    assert captured["node_id"] == "n1"


def test_run_structured_uses_prompt_builder_and_defaults_node_id():
    spec = AgentSpec(
        name="B",
        system_prompt="S",
        prompt_builder=lambda p: f"built:{p[0]}:{p[1]}",
        response_model=_Out,
    )
    runner, captured = _runner_with_capture(_Out())

    runner.run_structured(spec, ("a", "b"))

    assert captured["prompt"] == "built:a:b"
    assert captured["node_id"] == ""  # defaulted


def test_run_structured_forwards_max_tokens_only_when_set():
    spec = AgentSpec(name="M", prompt_template="x", response_model=_Out, max_tokens=1234)
    runner, captured = _runner_with_capture(_Out())
    runner.run_structured(spec, {})
    assert captured["max_tokens"] == 1234


def test_run_structured_omits_max_tokens_when_unset():
    # The six agents never pass max_tokens — run_structured must not inject it,
    # so the call stays byte-identical to today's inline call.
    spec = AgentSpec(name="M", prompt_template="x", response_model=_Out)
    runner, captured = _runner_with_capture(_Out())
    runner.run_structured(spec, {})
    assert "max_tokens" not in captured


def test_run_structured_propagates_instructor_error():
    spec = AgentSpec(name="E", prompt_template="x", response_model=_Out, system_prompt="S")
    runner = AgentRunner(_FakeLLM())

    def boom(**k):
        raise InstructorError("nope")

    runner.complete_structured = boom
    with pytest.raises(InstructorError):
        runner.run_structured(spec, {})
