"""Stream B: fail-open Langfuse observability wrapper on LLMClient.

Contract:
  - Each successful LLM generation MAY be reported to Langfuse (model,
    prompt/response or token usage, keyed by run_id/agent where possible).
  - langfuse is an OPTIONAL dependency: the import is lazy/guarded.
  - FAIL-OPEN is the hard requirement. If langfuse is missing, unconfigured
    (no LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY), or raises at ANY point,
    the LLM call MUST proceed and return normally. Tracing failures NEVER
    propagate out of the pipeline and NEVER change the returned result.
  - Default OFF when unconfigured.

These tests assert the tracing seam only. They do NOT touch provider/model
routing (configure_litellm_for_mode / llm_router), BIFROST_* handling, or
os.environ credential writes.
"""

import pathlib
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from models.schemas import LLMMode, ProviderConfig
from pipeline import llm_client as llm_mod
from pipeline.llm_client import LLMClient


class DummyAuditLogger:
    def log(self, **kwargs):
        return None


def _fake_response(content="ok", finish_reason="stop", prompt_tokens=11, completion_tokens=7):
    usage = SimpleNamespace(
        total_tokens=prompt_tokens + completion_tokens,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=usage)


def _build_client(model="openai/gpt-4o-mini", run_id="run-langfuse"):
    return LLMClient(
        mode=LLMMode.API,
        audit_logger=DummyAuditLogger(),
        run_id=run_id,
        provider_config=ProviderConfig(provider="openai", model=model),
    )


@pytest.fixture(autouse=True)
def _clear_langfuse_env(monkeypatch):
    """Isolate each test from ambient Langfuse configuration."""
    for var in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST", "LANGFUSE_ENABLED"):
        monkeypatch.delenv(var, raising=False)
    # Reset any cached client between tests so state never leaks.
    if hasattr(llm_mod, "_reset_langfuse_client_cache"):
        llm_mod._reset_langfuse_client_cache()


# ---------------------------------------------------------------------------
# GATE 1 — Fail-open when absent / unconfigured
# ---------------------------------------------------------------------------

def test_seam_helper_exists():
    """The fail-open tracing seam must exist as a module-level helper."""
    assert hasattr(llm_mod, "_report_langfuse_generation"), (
        "expected a _report_langfuse_generation fail-open tracing helper"
    )


def test_returns_normally_when_langfuse_unconfigured(monkeypatch):
    """With no keys set, complete() returns the normal result and raises nothing."""
    client = _build_client()
    monkeypatch.setattr(
        llm_mod.litellm,
        "completion",
        lambda **kwargs: _fake_response(content="hello world"),
    )
    result = client.complete(prompt="x", agent_name="extraction")
    assert result == "hello world"


def test_helper_swallows_client_exceptions(monkeypatch):
    """If resolving/using the langfuse client raises, the helper must NOT raise."""
    # Force keys present so the seam attempts to act.
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    if hasattr(llm_mod, "_reset_langfuse_client_cache"):
        llm_mod._reset_langfuse_client_cache()

    def _boom(*args, **kwargs):
        raise RuntimeError("langfuse exploded")

    # Whatever the resolver is, make it raise — the helper must swallow it.
    monkeypatch.setattr(llm_mod, "_get_langfuse_client", _boom, raising=False)

    # Must not raise.
    llm_mod._report_langfuse_generation(
        run_id="r1",
        agent_name="extraction",
        model="openai/gpt-4o-mini",
        prompt="p",
        system="s",
        response_text="resp",
        prompt_tokens=3,
        completion_tokens=2,
        total_tokens=5,
    )


def test_complete_fail_open_when_tracing_raises(monkeypatch):
    """A raising tracing client must never break complete()'s return value."""
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    if hasattr(llm_mod, "_reset_langfuse_client_cache"):
        llm_mod._reset_langfuse_client_cache()

    client = _build_client()
    monkeypatch.setattr(
        llm_mod.litellm,
        "completion",
        lambda **kwargs: _fake_response(content="safe-result"),
    )

    # Make the whole tracing path blow up.
    def _boom(*args, **kwargs):
        raise RuntimeError("tracing failure")

    monkeypatch.setattr(llm_mod, "_get_langfuse_client", _boom, raising=False)

    # The LLM call must proceed and return normally.
    assert client.complete(prompt="x", agent_name="extraction") == "safe-result"


def test_helper_noop_when_unconfigured_does_not_build_client(monkeypatch):
    """Default OFF: with no keys, the helper must not attempt to build a client."""
    called = {"built": False}

    def _tracker(*args, **kwargs):
        called["built"] = True
        return None

    monkeypatch.setattr(llm_mod, "_get_langfuse_client", _tracker, raising=False)
    llm_mod._report_langfuse_generation(
        run_id="r1",
        agent_name="extraction",
        model="m",
        prompt="p",
        system="s",
        response_text="resp",
        prompt_tokens=1,
        completion_tokens=1,
        total_tokens=2,
    )
    assert called["built"] is False, "must not build a langfuse client when unconfigured"


# ---------------------------------------------------------------------------
# GATE 2 — Traces when configured
# ---------------------------------------------------------------------------

class _FakeLangfuse:
    """Mock langfuse client recording generation calls."""

    def __init__(self):
        self.observations = []
        self.flushed = False

    def start_observation(self, **kwargs):
        self.observations.append(kwargs)
        return SimpleNamespace(
            update=lambda **kw: None,
            end=lambda **kw: None,
        )

    def flush(self):
        self.flushed = True


def test_records_generation_when_configured(monkeypatch):
    """With keys set + a mocked client, a completion records a generation."""
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    if hasattr(llm_mod, "_reset_langfuse_client_cache"):
        llm_mod._reset_langfuse_client_cache()

    fake = _FakeLangfuse()
    monkeypatch.setattr(llm_mod, "_get_langfuse_client", lambda: fake, raising=False)

    client = _build_client(run_id="run-xyz")
    monkeypatch.setattr(
        llm_mod.litellm,
        "completion",
        lambda **kwargs: _fake_response(
            content="generated", prompt_tokens=11, completion_tokens=7
        ),
    )

    result = client.complete(prompt="do it", system="sys", agent_name="extraction")
    assert result == "generated"

    # The mock must have recorded exactly one generation observation.
    assert len(fake.observations) == 1, "expected one recorded generation"
    obs = fake.observations[0]
    assert obs.get("as_type") == "generation"
    assert obs.get("model") == "openai/gpt-4o-mini"
    # Usage must be reported (token counts).
    usage = obs.get("usage_details") or {}
    assert usage.get("input") == 11
    assert usage.get("output") == 7


def test_generation_keyed_by_run_and_agent(monkeypatch):
    """Where possible the generation is keyed by run_id / agent."""
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    if hasattr(llm_mod, "_reset_langfuse_client_cache"):
        llm_mod._reset_langfuse_client_cache()

    fake = _FakeLangfuse()
    monkeypatch.setattr(llm_mod, "_get_langfuse_client", lambda: fake, raising=False)

    client = _build_client(run_id="run-abc")
    monkeypatch.setattr(
        llm_mod.litellm,
        "completion",
        lambda **kwargs: _fake_response(content="ok"),
    )
    client.complete(prompt="p", agent_name="dedup")

    assert fake.observations, "expected a recorded generation"
    obs = fake.observations[0]
    metadata = obs.get("metadata") or {}
    # run_id and agent must be discoverable somewhere in the recorded call.
    blob = repr(obs) + repr(metadata)
    assert "run-abc" in blob
    assert "dedup" in blob
