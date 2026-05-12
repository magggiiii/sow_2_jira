"""Regression tests for config.settings.build_litellm_model.

The OpenRouter (and Together) case is the load-bearing one: their models
are published under vendor/model IDs like ``qwen/qwen3.5-flash``. Without
the aggregator's own ``openrouter/`` prefix, LiteLLM treats the vendor
chunk as the provider and either fails (unknown providers like ``qwen``,
``moonshotai``, ``deepseek``) or silently routes to the wrong endpoint
(e.g. an ``openai/`` model would hit OpenAI directly with our OpenRouter
key, which is wrong).
"""

import pytest

from config.settings import build_litellm_model


@pytest.mark.parametrize(
    "model, expected",
    [
        ("qwen/qwen3.5-flash-02-23", "openrouter/qwen/qwen3.5-flash-02-23"),
        ("qwen/qwen3-235b-a22b-2507", "openrouter/qwen/qwen3-235b-a22b-2507"),
        ("google/gemini-2.5-flash", "openrouter/google/gemini-2.5-flash"),
        ("anthropic/claude-haiku-4.5", "openrouter/anthropic/claude-haiku-4.5"),
        ("deepseek/deepseek-chat-v3.1", "openrouter/deepseek/deepseek-chat-v3.1"),
        ("moonshotai/kimi-k2-0905", "openrouter/moonshotai/kimi-k2-0905"),
        ("meta-llama/llama-3.3-70b-instruct", "openrouter/meta-llama/llama-3.3-70b-instruct"),
        ("z-ai/glm-4.6", "openrouter/z-ai/glm-4.6"),
    ],
)
def test_openrouter_always_prepends_its_prefix(model, expected):
    """Every OpenRouter model id contains a vendor/model slash; LiteLLM still
    needs the outer openrouter/ prefix to pick the right client."""
    assert build_litellm_model("openrouter", model, None) == expected


def test_openrouter_strips_existing_prefix():
    """If something upstream already prepended openrouter/, don't double-prefix."""
    assert build_litellm_model(
        "openrouter", "openrouter/qwen/qwen3.5-flash", None
    ) == "openrouter/qwen/qwen3.5-flash"


def test_openrouter_empty_model():
    assert build_litellm_model("openrouter", "", None) == "openrouter/"
    assert build_litellm_model("openrouter", None, None) == "openrouter/"


def test_together_also_prefixed():
    """Together publishes vendor/model IDs too — same bug class."""
    assert build_litellm_model(
        "together", "meta-llama/Llama-3-70b", None
    ) == "together/meta-llama/Llama-3-70b"


def test_native_providers_unchanged():
    """Non-aggregator providers keep their existing behaviour."""
    assert build_litellm_model("anthropic", "claude-3-5-sonnet", None) == "anthropic/claude-3-5-sonnet"
    assert build_litellm_model("openai", "gpt-4o", None) == "openai/gpt-4o"
    assert build_litellm_model("google", "gemini-1.5-pro", None) == "gemini/gemini-1.5-pro"
    assert build_litellm_model("ollama", "qwen2.5:7b", None) == "ollama/qwen2.5:7b"
    assert build_litellm_model("ollama", "hf.co/foo/bar", None) == "ollama/hf.co/foo/bar"
