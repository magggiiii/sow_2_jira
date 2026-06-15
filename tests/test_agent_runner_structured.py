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
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import core.agent_runner as agent_runner_mod
from core.agent_runner import AgentRunner, InstructorError

# Import the real llm_client up-front so it is cached in sys.modules with the
# real ``litellm`` BEFORE any test injects a fake ``litellm`` via monkeypatch.
# complete_structured lazily ``from pipeline.llm_client import`` the retry
# helpers; if that module were first imported while a fake litellm is installed,
# its top-level ``from litellm import RateLimitError`` would explode.
import pipeline.llm_client  # noqa: E402,F401


# ─── Response model under test ────────────────────────────────────────────────


class _Classification(BaseModel):
    type: str
    confidence: float
    reason: str


# ─── Fake LLM provider (mirrors LLMClient's model resolution surface) ─────────


class _FakeProviderConfig:
    def __init__(self, model: str, api_key: str = "", api_base: str = "", provider: str = ""):
        self.model = model
        self.api_key = api_key
        self.api_base = api_base
        self.provider = provider


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
    client.from_litellm_kwargs = {}

    # complete_structured does ``import instructor`` / ``import litellm`` inside
    # the method. We inject fakes into sys.modules so those imports resolve to
    # ours without touching the real packages or the network.
    fake_instructor = type(sys)("instructor")
    # Minimal Mode stand-in exposing the members complete_structured selects.
    fake_instructor.Mode = SimpleNamespace(
        JSON="json",
        TOOLS="tool_call",
        JSON_SCHEMA="json_schema",
        OPENROUTER_STRUCTURED_OUTPUTS="openrouter_structured_outputs",
    )

    def _from_litellm(completion, **kw):
        client.from_litellm_kwargs = kw  # capture the mode= kwarg for assertions
        return client

    fake_instructor.from_litellm = _from_litellm

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


# ─── Instructor mode selection (avoids the TOOLS nested-stringify failure) ────


def test_complete_structured_defaults_to_json_mode(monkeypatch):
    """Mode.TOOLS (instructor's from_litellm default) makes several providers
    serialize nested list[Model] fields as stringified JSON, which fails Pydantic
    validation. complete_structured defaults non-OpenAI/Anthropic providers to
    JSON mode so nested objects round-trip."""
    canned = _Classification(type="info", confidence=0.5, reason="x")
    client = _install_instructor_stub(monkeypatch, lambda kw: canned)
    runner = AgentRunner(FakeLLM())  # provider "" → JSON
    runner.complete_structured(prompt="p", response_model=_Classification)
    assert client.from_litellm_kwargs.get("mode") == "json"


def test_complete_structured_uses_tools_mode_for_openai(monkeypatch):
    """OpenAI/Anthropic tool-calling handles nested arguments reliably, so those
    providers keep Mode.TOOLS."""
    canned = _Classification(type="info", confidence=0.5, reason="x")
    client = _install_instructor_stub(monkeypatch, lambda kw: canned)
    llm = FakeLLM()
    llm.provider_config = _FakeProviderConfig("gpt-4o", provider="openai")
    runner = AgentRunner(llm)
    runner.complete_structured(prompt="p", response_model=_Classification)
    assert client.from_litellm_kwargs.get("mode") == "tool_call"


def test_complete_structured_mode_env_override(monkeypatch):
    """S2J_INSTRUCTOR_MODE overrides the chosen mode for experimentation."""
    canned = _Classification(type="info", confidence=0.5, reason="x")
    client = _install_instructor_stub(monkeypatch, lambda kw: canned)
    monkeypatch.setenv("S2J_INSTRUCTOR_MODE", "JSON_SCHEMA")
    runner = AgentRunner(FakeLLM())
    runner.complete_structured(prompt="p", response_model=_Classification)
    assert client.from_litellm_kwargs.get("mode") == "json_schema"


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


# ─── A1: per-call timeout + bounded retry/backoff resilience ──────────────────
#
# complete_structured must survive transient provider errors (429 / 5xx / network
# timeouts) the same way the legacy complete_json path did, reusing
# pipeline.llm_client's extract_retry_hint / compute_wait_seconds /
# is_retryable_remote_error helpers. Over 300-600 calls in a big-SOW run, a single
# un-retried 429 silently drops an agent to its safe default (lost tasks), and a
# hung call with no timeout blocks the whole run indefinitely.


class _RemoteError(Exception):
    """A litellm-style provider error carrying a status code and/or headers,
    shaped so pipeline.llm_client's retry helpers classify it the same way they
    classify a real RateLimitError / Timeout."""

    def __init__(self, message, status_code=None, headers=None):
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code
        if headers is not None:
            self.headers = headers


def _sequence_behavior(*results):
    """Build a create() behavior that yields ``results`` in order: an Exception
    instance is raised; anything else is returned. Lets a test script a run like
    'fail, fail, then succeed'."""
    box = {"i": 0}

    def behavior(kwargs):
        i = box["i"]
        box["i"] = i + 1
        item = results[min(i, len(results) - 1)]
        if isinstance(item, BaseException):
            raise item
        return item

    return behavior


@pytest.fixture
def recorded_sleeps(monkeypatch):
    """Patch the backoff sleep so tests never actually wait; record wait values."""
    import time as _time

    waits: list[float] = []
    monkeypatch.setattr(_time, "sleep", lambda s: waits.append(s))
    return waits


def test_complete_structured_retries_transient_then_succeeds(monkeypatch, recorded_sleeps):
    """Two transient 429s, then success → returns the validated instance after 3
    attempts, sleeping between each retry."""
    canned = _Classification(type="actionable", confidence=0.9, reason="ok")
    behavior = _sequence_behavior(
        _RemoteError("rate limit: too many requests (429)", status_code=429),
        _RemoteError("rate limit: too many requests (429)", status_code=429),
        canned,
    )
    client = _install_instructor_stub(monkeypatch, behavior)
    runner = AgentRunner(FakeLLM())

    result = runner.complete_structured(prompt="p", response_model=_Classification)

    assert result is canned
    assert len(client.completions.calls) == 3       # 2 failures + 1 success
    assert len(recorded_sleeps) == 2                 # one backoff per retry


def test_complete_structured_gives_up_after_budget_then_raises(monkeypatch, recorded_sleeps):
    """A persistently failing transient error exhausts the attempt budget and then
    surfaces InstructorError (never a silent None), with the cause chained."""
    monkeypatch.setenv("LLM_STRUCTURED_MAX_ATTEMPTS", "3")
    err = _RemoteError("service unavailable (503)", status_code=503)
    client = _install_instructor_stub(monkeypatch, _sequence_behavior(err))
    runner = AgentRunner(FakeLLM())

    with pytest.raises(InstructorError) as exc:
        runner.complete_structured(prompt="p", response_model=_Classification)

    assert len(client.completions.calls) == 3        # capped at the budget
    assert len(recorded_sleeps) == 2                  # no sleep after the final attempt
    assert isinstance(exc.value.__cause__, _RemoteError)


def test_complete_structured_non_retryable_fails_immediately(monkeypatch, recorded_sleeps):
    """A 401 (auth) is non-retryable → exactly one call, no backoff, InstructorError."""
    err = _RemoteError("Unauthorized (401): invalid api key", status_code=401)
    client = _install_instructor_stub(monkeypatch, _sequence_behavior(err))
    runner = AgentRunner(FakeLLM())

    with pytest.raises(InstructorError):
        runner.complete_structured(prompt="p", response_model=_Classification)

    assert len(client.completions.calls) == 1
    assert recorded_sleeps == []


def test_complete_structured_respects_retry_after_header(monkeypatch, recorded_sleeps):
    """A Retry-After header dictates the backoff wait (not the jittered fallback)."""
    canned = _Classification(type="info", confidence=0.3, reason="x")
    behavior = _sequence_behavior(
        _RemoteError("rate limited (429)", status_code=429, headers={"Retry-After": "7"}),
        canned,
    )
    client = _install_instructor_stub(monkeypatch, behavior)
    runner = AgentRunner(FakeLLM())

    result = runner.complete_structured(prompt="p", response_model=_Classification)

    assert result is canned
    assert len(client.completions.calls) == 2
    assert recorded_sleeps == [7.0]                  # honored the header, exactly


def test_complete_structured_passes_default_timeout(monkeypatch):
    """A per-call timeout (default 60s) is forwarded to litellm so a hung call
    can never block the run forever."""
    canned = _Classification(type="info", confidence=0.5, reason="x")
    client = _install_instructor_stub(monkeypatch, lambda kw: canned)
    runner = AgentRunner(FakeLLM())

    runner.complete_structured(prompt="p", response_model=_Classification)

    assert client.completions.calls[0]["timeout"] == 60


def test_complete_structured_timeout_env_override(monkeypatch):
    """S2J_STRUCTURED_TIMEOUT overrides the default per-call timeout."""
    monkeypatch.setenv("S2J_STRUCTURED_TIMEOUT", "120")
    canned = _Classification(type="info", confidence=0.5, reason="x")
    client = _install_instructor_stub(monkeypatch, lambda kw: canned)
    runner = AgentRunner(FakeLLM())

    runner.complete_structured(prompt="p", response_model=_Classification)

    assert client.completions.calls[0]["timeout"] == 120


def test_complete_structured_ollama_gets_long_timeout(monkeypatch):
    """Local Ollama models get the long (3600s) timeout — local generation is slow
    and must not be killed by the remote 60s default."""
    canned = _Classification(type="info", confidence=0.5, reason="x")
    client = _install_instructor_stub(monkeypatch, lambda kw: canned)
    runner = AgentRunner(FakeProviderConfigOnly(model="ollama/qwen2.5:7b"))

    runner.complete_structured(prompt="p", response_model=_Classification)

    assert client.completions.calls[0]["timeout"] == 3600
