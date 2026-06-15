# tests/test_agent_runner_structured.py
"""
Tests for AgentRunner.complete_structured (core/agent_runner.py).

These STUB the instructor call boundary — no network, no real litellm call.
We monkeypatch ``instructor.from_litellm`` so it returns a fake client whose
``chat.completions.create`` either returns a canned validated ``response_model``
instance or raises (to simulate an instructor validation failure). That proves:

- complete_structured returns a validated instance of the given response_model.
- A stubbed validation/call failure raises the typed InstructorError (never
  silently returns None).
- The litellm model is resolved off the provider exactly as LLMClient uses it
  (``provider.model``), and is forwarded to instructor's create() call.
- system / max_tokens / messages are shaped as expected.

The fake LLM provider mirrors the bits of ``pipeline.llm_client.LLMClient`` that
``complete_structured`` reads (``.model`` / ``.provider_config.model``).
"""

from __future__ import annotations

import pathlib
import sys

import pytest
from pydantic import BaseModel

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import core.agent_runner as agent_runner_mod
from core.agent_runner import AgentRunner, InstructorError


# ─── Response model under test ────────────────────────────────────────────────


class _Classification(BaseModel):
    type: str
    confidence: float
    reason: str


# ─── Fake LLM provider (mirrors LLMClient's model resolution surface) ─────────


class _FakeProviderConfig:
    def __init__(self, model: str, api_key: str = "", api_base: str = ""):
        self.model = model
        self.api_key = api_key
        self.api_base = api_base


class FakeLLM:
    """Stand-in exposing the same ``.model`` / ``.provider_config`` LLMClient does."""

    def __init__(self, model: str = "openrouter/zhipuai/glm-4"):
        self.model = model
        self.provider_config = _FakeProviderConfig(model)


class FakeProviderConfigOnly:
    """A provider that only exposes ``.provider_config.model`` (no ``.model``)."""

    def __init__(self, model: str):
        self.provider_config = _FakeProviderConfig(model)
        self.model = None  # forces the provider_config fallback path


# ─── Instructor boundary stub ─────────────────────────────────────────────────


class _FakeCompletions:
    def __init__(self, behavior):
        self._behavior = behavior
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._behavior(kwargs)


class _FakeChat:
    def __init__(self, completions):
        self.completions = completions


class _FakeInstructorClient:
    def __init__(self, behavior):
        self.completions = _FakeCompletions(behavior)
        self.chat = _FakeChat(self.completions)


def _install_instructor_stub(monkeypatch, behavior):
    """
    Patch the lazily-imported ``instructor`` module inside complete_structured
    so ``instructor.from_litellm(litellm.completion)`` returns our fake client.

    Returns the fake client so the test can inspect ``client.completions.calls``.
    """
    client = _FakeInstructorClient(behavior)

    # complete_structured does ``import instructor`` / ``import litellm`` inside
    # the method. We inject fakes into sys.modules so those imports resolve to
    # ours without touching the real packages or the network.
    fake_instructor = type(sys)("instructor")
    fake_instructor.from_litellm = lambda completion, **kw: client

    fake_litellm = type(sys)("litellm")
    fake_litellm.completion = lambda *a, **k: None  # never actually invoked

    monkeypatch.setitem(sys.modules, "instructor", fake_instructor)
    monkeypatch.setitem(sys.modules, "litellm", fake_litellm)
    return client


# ─── Happy path: returns a validated response_model instance ──────────────────


def test_complete_structured_returns_validated_instance(monkeypatch):
    canned = _Classification(type="actionable", confidence=0.9, reason="has work")

    def behavior(kwargs):
        return canned

    client = _install_instructor_stub(monkeypatch, behavior)
    runner = AgentRunner(FakeLLM(model="openrouter/zhipuai/glm-4"))

    result = runner.complete_structured(
        prompt="Classify this section",
        response_model=_Classification,
        system="You are a classifier.",
        agent_name="ClassifierAgent",
        node_id="n1",
    )

    assert isinstance(result, _Classification)
    assert result is canned
    assert result.type == "actionable"

    # The model was resolved off the provider and forwarded to instructor.
    call = client.completions.calls[0]
    assert call["model"] == "openrouter/zhipuai/glm-4"
    assert call["response_model"] is _Classification
    assert call["max_tokens"] == 8192  # default
    assert call["messages"] == [
        {"role": "system", "content": "You are a classifier."},
        {"role": "user", "content": "Classify this section"},
    ]


def test_complete_structured_omits_system_when_none(monkeypatch):
    canned = _Classification(type="info", confidence=0.1, reason="no work")
    client = _install_instructor_stub(monkeypatch, lambda kw: canned)
    runner = AgentRunner(FakeLLM())

    result = runner.complete_structured(
        prompt="just a user turn",
        response_model=_Classification,
    )

    assert isinstance(result, _Classification)
    # No system message when system is None — only the user turn.
    assert client.completions.calls[0]["messages"] == [
        {"role": "user", "content": "just a user turn"},
    ]


def test_complete_structured_forwards_max_tokens_override(monkeypatch):
    canned = _Classification(type="info", confidence=0.5, reason="x")
    client = _install_instructor_stub(monkeypatch, lambda kw: canned)
    runner = AgentRunner(FakeLLM())

    runner.complete_structured(
        prompt="p",
        response_model=_Classification,
        max_tokens=2048,
    )

    assert client.completions.calls[0]["max_tokens"] == 2048


def test_complete_structured_forwards_provider_credentials(monkeypatch):
    """The resolved provider api_key/api_base MUST be threaded into the litellm
    call. Otherwise litellm falls back to env vars and 401s — the exact live
    smoke-gate failure ('No cookie auth credentials found'). Mirrors how
    complete_json forwards provider_config.api_key/api_base in _execute_call.
    """
    canned = _Classification(type="actionable", confidence=0.8, reason="creds")
    client = _install_instructor_stub(monkeypatch, lambda kw: canned)

    llm = FakeLLM(model="openrouter/google/gemini-2.5-flash")
    llm.provider_config = _FakeProviderConfig(
        "openrouter/google/gemini-2.5-flash",
        api_key="sk-or-test-123",
        api_base="https://openrouter.ai/api/v1",
    )
    runner = AgentRunner(llm)
    runner.complete_structured(prompt="p", response_model=_Classification)

    call = client.completions.calls[0]
    assert call["api_key"] == "sk-or-test-123"
    assert call["api_base"] == "https://openrouter.ai/api/v1"


def test_complete_structured_omits_empty_credentials(monkeypatch):
    """When the provider exposes no api_key/api_base (env-based auth), they are
    NOT passed — so litellm's own env resolution still applies and we don't
    clobber it with empty strings."""
    canned = _Classification(type="info", confidence=0.2, reason="x")
    client = _install_instructor_stub(monkeypatch, lambda kw: canned)
    runner = AgentRunner(FakeLLM())  # provider_config api_key/api_base default ""
    runner.complete_structured(prompt="p", response_model=_Classification)
    call = client.completions.calls[0]
    assert "api_key" not in call
    assert "api_base" not in call


def test_complete_structured_resolves_model_from_provider_config_fallback(monkeypatch):
    """When the provider exposes no ``.model``, fall back to provider_config.model."""
    canned = _Classification(type="info", confidence=0.5, reason="x")
    client = _install_instructor_stub(monkeypatch, lambda kw: canned)
    runner = AgentRunner(FakeProviderConfigOnly(model="ollama/qwen2.5:7b"))

    runner.complete_structured(prompt="p", response_model=_Classification)

    assert client.completions.calls[0]["model"] == "ollama/qwen2.5:7b"


# ─── Failure paths: raise the typed error, never return None ──────────────────


def test_complete_structured_validation_failure_raises_typed_error(monkeypatch):
    """A stubbed validation/instructor failure surfaces as InstructorError."""

    def behavior(kwargs):
        # instructor raises (e.g. retries exhausted on an unsatisfiable schema).
        raise ValueError("response failed validation after retries")

    _install_instructor_stub(monkeypatch, behavior)
    runner = AgentRunner(FakeLLM())

    with pytest.raises(InstructorError) as exc:
        runner.complete_structured(prompt="p", response_model=_Classification)

    # The original cause is chained, and the message is explicit.
    assert isinstance(exc.value.__cause__, ValueError)
    assert "validation" in str(exc.value).lower()


def test_complete_structured_wrong_type_result_raises_typed_error(monkeypatch):
    """If instructor hands back a non-response_model object, fail loudly."""

    def behavior(kwargs):
        return {"type": "actionable"}  # a dict, NOT a _Classification instance

    _install_instructor_stub(monkeypatch, behavior)
    runner = AgentRunner(FakeLLM())

    with pytest.raises(InstructorError):
        runner.complete_structured(prompt="p", response_model=_Classification)


def test_complete_structured_unresolvable_model_raises_typed_error(monkeypatch):
    """No resolvable model on the provider → typed error, no instructor call."""
    # Provider with no model at all.
    class _NoModel:
        model = None
        provider_config = None

    runner = AgentRunner(_NoModel())

    with pytest.raises(InstructorError) as exc:
        runner.complete_structured(prompt="p", response_model=_Classification)

    assert "model" in str(exc.value).lower()


# ─── complete_json / run are untouched (additive guarantee) ───────────────────


def test_complete_structured_does_not_exist_on_old_call_paths():
    """The new method is additive: the class still has complete_json and run."""
    assert hasattr(AgentRunner, "complete_json")
    assert hasattr(AgentRunner, "run")
    assert hasattr(AgentRunner, "complete_structured")
    # InstructorError is exported for callers to catch.
    assert "InstructorError" in agent_runner_mod.__all__
