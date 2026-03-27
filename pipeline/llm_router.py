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

    For API mode:  Routes through Bifrost → z.ai. Sets OPENAI_API_KEY/BASE
                   so litellm picks them up automatically.
    For LOCAL mode: Uses Ollama directly via litellm's ollama provider.
    """
    if mode == LLMMode.API:
        # litellm reads OPENAI_API_KEY and OPENAI_API_BASE automatically
        os.environ["OPENAI_API_KEY"] = os.environ.get("BIFROST_API_KEY", "")
        os.environ["OPENAI_API_BASE"] = os.environ.get("BIFROST_BASE_URL", "")
        model = os.environ.get("ZAI_MODEL", "glm-4")
        return model
    elif mode == LLMMode.LOCAL:
        # Local mode: litellm's ollama/ prefix routes to Ollama API
        ollama_model = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")
        ollama_base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        # litellm uses OLLAMA_API_BASE to find the Ollama server
        os.environ["OLLAMA_API_BASE"] = ollama_base.rstrip("/v1").rstrip("/")
        return f"ollama/{ollama_model}"
    else:
        # Custom mode: User provides the full litellm string in LITELLM_MODEL
        model = os.environ.get("LITELLM_MODEL", "gpt-4o")
        return model
