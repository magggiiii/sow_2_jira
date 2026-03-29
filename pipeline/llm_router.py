# pipeline/llm_router.py

"""
Resolves the correct litellm-compatible model string based on the pipeline's LLMMode.
PageIndex uses litellm internally, so we just need to set the right env vars
and return the model string it should use.
"""

import os
from models.schemas import LLMMode


def configure_litellm_for_mode(mode: LLMMode) -> str:
    """
    Configure environment variables for litellm and return the model string.
    Prioritizes UI-set LITELLM_* variables if available.
    """
    # 1. Global Override from UI Settings (LITELLM_MODEL)
    ui_model = os.environ.get("LITELLM_MODEL")
    ui_key = os.environ.get("LITELLM_API_KEY")
    ui_base = os.environ.get("LITELLM_API_BASE")

    if mode == LLMMode.API:
        # Default to Bifrost configuration if UI hasn't explicitly set a model
        os.environ["OPENAI_API_KEY"] = ui_key or os.environ.get("BIFROST_API_KEY", "")
        os.environ["OPENAI_API_BASE"] = ui_base or os.environ.get("BIFROST_BASE_URL", "")
        model = ui_model or os.environ.get("ZAI_MODEL", "glm-4")
        return model
    elif mode == LLMMode.LOCAL:
        # Local mode: litellm's ollama/ prefix routes to Ollama API
        ollama_model = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")
        ollama_base = ui_base or os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        os.environ["OLLAMA_API_BASE"] = ollama_base.rstrip("/v1").rstrip("/")
        return f"ollama/{ollama_model}"
    else:
        # Custom mode: Always prefer UI-set model, fallback to a sensible default
        return ui_model or "gpt-4o"
