"""complete_json must not silently swallow length-truncated responses.

Root cause (audit C-5/H-1): complete_json hard-coded max_tokens=4096 and only
trimmed the last bracket on cleanup, so a response cut off mid-JSON by the
provider's token cap would yield invalid/partial JSON that got swallowed.

Post-fix behaviour:
  - A response whose finish_reason indicates length/truncation is an explicit
    failure (raises a clear error), NOT a silently-returned partial parse.
  - A response with finish_reason='stop' (or a missing finish_reason) parses
    normally — the happy path is unchanged.
  - max_tokens is a parameter of complete_json with a larger default than the
    old hard-coded 4096.
"""

import inspect
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


def _fake_response(content="ok", finish_reason="stop", prompt_tokens=4, completion_tokens=3):
    usage = SimpleNamespace(
        total_tokens=prompt_tokens + completion_tokens,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=usage)


def _build_client(model="openai/gpt-4o-mini"):
    return LLMClient(
        mode=LLMMode.API,
        audit_logger=DummyAuditLogger(),
        run_id="test-run",
        provider_config=ProviderConfig(provider="openai", model=model),
    )


def test_length_truncated_response_raises(monkeypatch):
    """finish_reason='length' on a partial JSON body must surface as an error."""
    client = _build_client()
    # A body that *looks* parseable after last-bracket trim but was cut short.
    partial = '[{"title": "Implement auth"}, {"title": "Add logging"'
    monkeypatch.setattr(
        llm_mod.litellm,
        "completion",
        lambda **kwargs: _fake_response(content=partial, finish_reason="length"),
    )

    with pytest.raises(Exception) as excinfo:
        client.complete_json(prompt="x", agent_name="test")
    # The error must clearly be about truncation, not a generic JSON parse error
    # that hides the real root cause.
    assert "truncat" in str(excinfo.value).lower() or "length" in str(excinfo.value).lower()


def test_finish_reason_stop_parses_normally(monkeypatch):
    """A complete (stop) response returns parsed JSON — happy path unchanged."""
    client = _build_client()
    monkeypatch.setattr(
        llm_mod.litellm,
        "completion",
        lambda **kwargs: _fake_response(
            content='[{"title": "Implement auth"}]', finish_reason="stop"
        ),
    )
    assert client.complete_json(prompt="x", agent_name="test") == [
        {"title": "Implement auth"}
    ]


def test_missing_finish_reason_is_acceptable(monkeypatch):
    """A response with no finish_reason attribute is treated as acceptable."""
    client = _build_client()
    usage = SimpleNamespace(total_tokens=7, prompt_tokens=4, completion_tokens=3)
    message = SimpleNamespace(content='{"foo": "bar"}')
    # Choice object without a finish_reason attribute at all.
    choice = SimpleNamespace(message=message)
    response = SimpleNamespace(choices=[choice], usage=usage)
    monkeypatch.setattr(llm_mod.litellm, "completion", lambda **kwargs: response)

    assert client.complete_json(prompt="x", agent_name="test") == {"foo": "bar"}


def test_complete_json_max_tokens_param_default_above_4096():
    """max_tokens is now a parameter of complete_json, defaulting above 4096."""
    sig = inspect.signature(LLMClient.complete_json)
    assert "max_tokens" in sig.parameters
    default = sig.parameters["max_tokens"].default
    assert isinstance(default, int) and default > 4096
