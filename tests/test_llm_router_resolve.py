# tests/test_llm_router_resolve.py
"""WAVE 2 STEP 2.1 — resolve_provider_config is a pure, per-run config builder.

The router historically resolved LLM config by reading the global
``SettingsManager`` (data/settings.json) and ``os.environ`` inside
``configure_litellm_for_mode``. That couples every run to one process-global
provider and blocks per-user credentials. ``resolve_provider_config`` is the
replacement seam: every value is caller-supplied, so it reads NO global settings
and NO environment, and two concurrent runs can carry different providers/keys.

``configure_litellm_for_mode`` stays as a temporary bridge (removed in 2.4), so
these tests only pin the new pure function.
"""

from __future__ import annotations

import os

import pytest

from models.schemas import ProviderConfig
from pipeline.llm_router import resolve_provider_config


class _RaisingEnviron(dict):
    """A stand-in for os.environ that fails loudly on any read."""

    def __getitem__(self, key):  # noqa: ANN001
        raise AssertionError(f"resolve_provider_config read os.environ[{key!r}]")

    def get(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("resolve_provider_config read os.environ via .get()")


def test_resolve_provider_config_reads_no_env(monkeypatch):
    """The builder never touches os.environ (monkeypatched to raise on access)."""
    monkeypatch.setattr(os, "environ", _RaisingEnviron())
    cfg = resolve_provider_config(
        provider="openai", model="gpt-4o", api_key="sk-test"
    )
    assert isinstance(cfg, ProviderConfig)
    assert cfg.provider == "openai"


def test_resolve_provider_config_builds_litellm_model_string():
    """provider+model are combined into the litellm-prefixed model id."""
    cfg = resolve_provider_config(provider="openai", model="gpt-4o", api_key="k")
    assert cfg.model == "openai/gpt-4o"
    assert cfg.api_key == "k"


def test_resolve_provider_config_azure_deployment():
    """Azure deployment name flows into the model string and the config."""
    cfg = resolve_provider_config(
        provider="azure",
        model="",
        api_key="k",
        api_base="https://x.openai.azure.com",
        azure_api_version="2024-02-01",
        azure_deployment_name="my-deploy",
    )
    assert cfg.model == "azure/my-deploy"
    assert cfg.azure_deployment_name == "my-deploy"
    assert cfg.azure_api_version == "2024-02-01"
    assert cfg.api_base == "https://x.openai.azure.com"


def test_resolve_provider_config_isolation_between_runs():
    """Two builds carry their own api_key — no shared global state."""
    a = resolve_provider_config(provider="openai", model="gpt-4o", api_key="key-A")
    b = resolve_provider_config(provider="anthropic", model="claude-3", api_key="key-B")
    assert a.api_key == "key-A"
    assert b.api_key == "key-B"
    assert a.provider == "openai" and b.provider == "anthropic"


def test_resolve_provider_config_defaults_missing_provider_to_openai():
    """An empty provider falls back to openai (matches the legacy default)."""
    cfg = resolve_provider_config(provider="", model="gpt-4o", api_key="")
    assert cfg.provider == "openai"


def test_resolve_provider_config_is_importable_pure_symbol():
    """It is a top-level function, not a method needing SettingsManager."""
    assert callable(resolve_provider_config)
    with pytest.raises(TypeError):
        resolve_provider_config()  # provider/model are required positional args
