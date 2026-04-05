import pathlib
import sys
import threading
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from models.schemas import LLMMode, ProviderConfig
from pipeline import llm_client as llm_mod
from pageindex import utils as pageindex_utils


class DummyAuditLogger:
    def log(self, **kwargs):
        return None


class DummyHTTPError(Exception):
    def __init__(self, message, status_code=None, headers=None):
        super().__init__(message)
        self.status_code = status_code
        self.headers = headers or {}


def _fake_response(content="ok", finish_reason="stop", prompt_tokens=4, completion_tokens=3):
    usage = SimpleNamespace(
        total_tokens=prompt_tokens + completion_tokens,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=usage)


def _build_client(mode=LLMMode.API, provider="openai", model="openai/gpt-4o-mini", stop_event=None, status=None):
    return llm_mod.LLMClient(
        mode=mode,
        audit_logger=DummyAuditLogger(),
        run_id="test-run",
        provider_config=ProviderConfig(provider=provider, model=model),
        stop_event=stop_event,
        status_callback=status,
    )


def test_remote_retry_after_header_takes_precedence():
    err = DummyHTTPError(
        "rate limited",
        status_code=429,
        headers={"Retry-After": "17", "x-ratelimit-reset": "9999999999"},
    )
    hint = llm_mod.extract_retry_hint(err)
    assert hint is not None
    assert hint.source == "retry-after-header"
    assert hint.wait_seconds == 17.0
    assert llm_mod.compute_wait_seconds(hint, attempt=3, max_wait_s=300) == 17.0


def test_remote_fallback_uses_exponential_with_jitter(monkeypatch):
    monkeypatch.setattr(llm_mod.random, "uniform", lambda a, b: 0.0)
    wait = llm_mod.compute_wait_seconds(None, attempt=2, max_wait_s=300)
    assert wait == 2.0


def test_retryable_remote_classification():
    assert llm_mod.is_retryable_remote_error(DummyHTTPError("too many requests", status_code=429))
    assert llm_mod.is_retryable_remote_error(DummyHTTPError("service unavailable", status_code=503))
    assert llm_mod.is_retryable_remote_error(Exception("connection reset by peer"))

    assert not llm_mod.is_retryable_remote_error(DummyHTTPError("unauthorized", status_code=401))
    assert not llm_mod.is_retryable_remote_error(DummyHTTPError("forbidden", status_code=403))
    assert not llm_mod.is_retryable_remote_error(Exception("invalid api key"))


def test_remote_budget_enforced(monkeypatch):
    client = _build_client()
    monkeypatch.setenv("LLM_REMOTE_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("LLM_REMOTE_MAX_ELAPSED_S", "300")
    monkeypatch.setenv("LLM_REMOTE_MAX_WAIT_S", "1")
    monkeypatch.setattr(llm_mod, "_sleep_with_cancel", lambda total_s, stop_event: None)
    monkeypatch.setattr(llm_mod.litellm, "completion", lambda **kwargs: (_ for _ in ()).throw(DummyHTTPError("rate limited", status_code=429)))

    with pytest.raises(RuntimeError, match="retry budget exhausted"):
        client.complete("prompt", agent_name="test")


def test_remote_cancellation_interrupts_backoff(monkeypatch):
    stop_event = threading.Event()
    calls = {"count": 0}

    client = _build_client(stop_event=stop_event)
    monkeypatch.setenv("LLM_REMOTE_MAX_ATTEMPTS", "8")
    monkeypatch.setenv("LLM_REMOTE_MAX_ELAPSED_S", "300")
    monkeypatch.setenv("LLM_REMOTE_MAX_WAIT_S", "1")

    def fail_once(**kwargs):
        calls["count"] += 1
        raise DummyHTTPError("rate limited", status_code=429)

    def cancel_sleep(total_s, evt):
        stop_event.set()
        raise RuntimeError("LLM call cancelled by user")

    monkeypatch.setattr(llm_mod.litellm, "completion", fail_once)
    monkeypatch.setattr(llm_mod, "_sleep_with_cancel", cancel_sleep)

    with pytest.raises(RuntimeError, match="cancelled by user"):
        client.complete("prompt", agent_name="test")

    assert calls["count"] == 1


def test_local_remote_isolation(monkeypatch):
    monkeypatch.setenv("LLM_REMOTE_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("LLM_REMOTE_MAX_ELAPSED_S", "300")
    monkeypatch.setenv("LLM_REMOTE_MAX_WAIT_S", "1")
    monkeypatch.setattr(llm_mod, "_sleep_with_cancel", lambda total_s, stop_event: None)

    local_calls = {"count": 0}
    remote_calls = {"count": 0}

    def local_completion(**kwargs):
        local_calls["count"] += 1
        if local_calls["count"] < 3:
            raise DummyHTTPError("timed out", status_code=503)
        return _fake_response(content="local ok")

    def remote_completion(**kwargs):
        remote_calls["count"] += 1
        raise DummyHTTPError("rate limited", status_code=429)

    local_client = _build_client(mode=LLMMode.LOCAL, provider="ollama", model="ollama/llama3")
    monkeypatch.setattr(llm_mod.litellm, "completion", local_completion)
    assert local_client.complete("prompt", agent_name="test") == "local ok"
    assert local_calls["count"] == 3

    remote_client = _build_client(mode=LLMMode.API, provider="openai", model="openai/gpt-4o-mini")
    monkeypatch.setattr(llm_mod.litellm, "completion", remote_completion)
    with pytest.raises(RuntimeError, match="retry budget exhausted"):
        remote_client.complete("prompt", agent_name="test")
    assert remote_calls["count"] == 1


def test_remote_status_message_includes_reason_and_source(monkeypatch):
    statuses = []
    client = _build_client(status=statuses.append)
    monkeypatch.setenv("LLM_REMOTE_MAX_ATTEMPTS", "4")
    monkeypatch.setenv("LLM_REMOTE_MAX_ELAPSED_S", "300")
    monkeypatch.setenv("LLM_REMOTE_MAX_WAIT_S", "30")
    monkeypatch.setattr(llm_mod, "_sleep_with_cancel", lambda total_s, stop_event: None)

    calls = {"count": 0}

    def flaky_completion(**kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise DummyHTTPError("rate limited", status_code=429, headers={"Retry-After": "5"})
        return _fake_response(content="ok")

    monkeypatch.setattr(llm_mod.litellm, "completion", flaky_completion)
    assert client.complete("prompt", agent_name="test") == "ok"
    assert any("Rate-limited by openai" in msg for msg in statuses)
    assert any("retry-after-header" in msg for msg in statuses)


def test_pageindex_header_first_parity():
    err = DummyHTTPError(
        "rate limited",
        status_code=429,
        headers={"Retry-After": "11", "x-ratelimit-reset": "9999999999"},
    )
    hint = pageindex_utils.extract_retry_hint(err)
    assert hint is not None
    assert hint.source == "retry-after-header"
    assert hint.wait_seconds == 11.0
