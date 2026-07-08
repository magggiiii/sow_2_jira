# tests/test_configure_litellm_once.py
"""WAVE 2 STEP 2.2 — litellm global state is configured exactly once.

Historically ``LLMClient.__init__`` called ``_configure_litellm_logging()`` on
every construction. That mutates litellm globals AND adds a fresh root-logger
filter each time — a per-client leak. ``configure_litellm_once()`` guards the
one-time global mutation behind a module ``_CONFIGURED`` flag so:

  * calling it twice is a no-op, and
  * constructing many ``LLMClient`` instances runs the global config once.

The function is importable so the web lifespan and the arq worker on_startup
(separate processes) can each call it once at boot.
"""

from __future__ import annotations

import pytest

import pipeline.llm_client as llm_client
from audit.logger import AuditLogger
from models.schemas import LLMMode, ProviderConfig


@pytest.fixture
def reset_configured(monkeypatch):
    """Force the one-time guard back to un-configured for the test."""
    monkeypatch.setattr(llm_client, "_CONFIGURED", False)


def test_configure_litellm_once_runs_underlying_work_once(reset_configured, monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(
        llm_client, "_configure_litellm_logging", lambda: calls.__setitem__("n", calls["n"] + 1)
    )
    llm_client.configure_litellm_once()
    llm_client.configure_litellm_once()
    llm_client.configure_litellm_once()
    assert calls["n"] == 1


def test_configure_litellm_once_is_noop_when_already_configured(monkeypatch):
    monkeypatch.setattr(llm_client, "_CONFIGURED", True)
    calls = {"n": 0}
    monkeypatch.setattr(
        llm_client, "_configure_litellm_logging", lambda: calls.__setitem__("n", calls["n"] + 1)
    )
    llm_client.configure_litellm_once()
    assert calls["n"] == 0


def test_constructing_many_clients_configures_globals_once(reset_configured, monkeypatch):
    """The acceptance case: N clients → one global-config run, not N."""
    calls = {"n": 0}
    monkeypatch.setattr(
        llm_client, "_configure_litellm_logging", lambda: calls.__setitem__("n", calls["n"] + 1)
    )
    cfg = ProviderConfig(provider="openai", model="openai/gpt-4o", api_key="k")
    for _ in range(3):
        llm_client.LLMClient(
            mode=LLMMode.API,
            audit_logger=AuditLogger(),
            run_id="once-test",
            provider_config=cfg,
        )
    assert calls["n"] == 1


def test_configure_litellm_once_is_public_symbol():
    assert callable(llm_client.configure_litellm_once)
