# pipeline/llm_router.py

"""
Resolves the correct litellm-compatible model string and configuration
based on the pipeline's LLMMode. Returns a ProviderConfig instance
instead of mutating global os.environ.
"""

import os
from pipeline.observability import logger
from models.schemas import LLMMode, ProviderConfig
from config.settings import (
    SettingsManager,
    _ensure_docker_host,
    build_litellm_model,
    resolve_provider_base,
)

def resolve_provider_config(
    provider: str,
    model: str,
    api_key: str = "",
    api_base: str = "",
    azure_api_version: str = "",
    azure_deployment_name: str = "",
) -> ProviderConfig:
    """Build a ProviderConfig from explicit, caller-supplied values.

    This is the per-run provider seam (WAVE 2 STEP 2.1). Unlike
    ``configure_litellm_for_mode``, it reads NO global ``SettingsManager`` and NO
    ``os.environ`` — every value is passed in by the caller (a per-user
    credential row or ``RunConfig``). Two concurrent runs can therefore hold
    different providers/keys without any shared process-global state.

    Only pure, module-constant helpers are used: ``build_litellm_model`` (litellm
    model-string prefixing) and ``resolve_provider_base`` (PROVIDER_REGISTRY
    default base URL). Docker-host translation (``_ensure_docker_host``, which
    reads the environment) is deliberately NOT applied here — it is a caller
    concern for the deploy environment, kept out of the pure builder.
    """
    provider = provider or "openai"
    return ProviderConfig(
        provider=provider,
        model=build_litellm_model(provider, model, azure_deployment_name),
        api_key=api_key or "",
        api_base=resolve_provider_base(provider, api_base) or "",
        azure_api_version=azure_api_version or "",
        azure_deployment_name=azure_deployment_name or "",
    )


def configure_litellm_for_mode(mode: LLMMode) -> ProviderConfig:
    """
    Returns a ProviderConfig for the given mode without mutating global os.environ.
    Prioritizes UI-set settings (from data/settings.json) if available.
    """
    sm = SettingsManager()
    try:
        settings = sm.load()
    except Exception:
        settings = {}
    
    provider = settings.get("provider")
    providers = settings.get("providers", {})
    pset = providers.get(provider, {}) if provider else {}

    api_key = ""
    if pset.get("api_key"):
        try:
            api_key = sm.decrypt_secret(pset.get("api_key"))
        except Exception:
            logger.warning("Failed to decrypt API key from settings")

    # Build initial config from settings
    config = ProviderConfig(
        provider=provider or "openai",
        model=build_litellm_model(provider or "openai", pset.get("model"), pset.get("azure_deployment_name")),
        api_key=api_key,
        api_base=_ensure_docker_host(pset.get("base_url")) or "",
        azure_api_version=pset.get("azure_api_version") or "",
        azure_deployment_name=pset.get("azure_deployment_name") or ""
    )

    if mode == LLMMode.API:
        # Default to Bifrost configuration if settings don't provide explicit values
        if not config.api_key:
            config.api_key = os.environ.get("BIFROST_API_KEY", "")
        if not config.api_base:
            config.api_base = _ensure_docker_host(os.environ.get("BIFROST_BASE_URL", "")) or ""
        if not pset.get("model"):
            config.model = os.environ.get("ZAI_MODEL", "glm-4")

    elif mode == LLMMode.LOCAL:
        # Local mode defaults to Ollama
        ollama_model = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")
        ollama_base = _ensure_docker_host(os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"))

        # Override with Ollama defaults if not explicitly set in settings
        config.provider = "ollama"
        if not pset.get("model"):
            config.model = f"ollama/{ollama_model}"
        if not pset.get("base_url"):
            config.api_base = ollama_base

        # Ensure base URL is clean for LiteLLM
        if config.api_base:
            config.api_base = config.api_base.rstrip("/v1").rstrip("/")

    
    else:
        # CUSTOM mode: sensible default if nothing in settings
        if not pset.get("model"):
            config.model = "gpt-4o"

    logger.info(f"› Resolved LLM config | provider: {config.provider} | model: {config.model} | base: {config.api_base}")
    return config
