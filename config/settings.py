import base64
import json
import os
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from cryptography.fernet import Fernet
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven application settings — the ``APP_ENV`` flip.

    Authoritative spec: ``.planning/elevation/ENV-CONFIG.md``. One switch,
    ``APP_ENV=local|production``, plus the per-dependency env vars in the
    ENV-CONFIG matrix. Read purely from the process environment (the app loads
    ``.env.local`` / ``.env`` via ``load_dotenv`` before constructing this), so
    unit tests are deterministic with ``monkeypatch.setenv``.

    Only the composition root (``app.container.build_container``) branches on
    these values to pick adapters per ``core.ports`` — there is no deep
    per-module env branching.
    """

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    # The one flip.
    app_env: Literal["local", "production"] = "local"

    # Postgres (Supabase local stack / hosted project). Alembic owns the schema.
    database_url: str = ""

    # Object storage — Supabase Storage S3 for BOTH envs (MinIO dropped).
    s3_endpoint: str = ""
    s3_access_key: str = ""
    s3_secret: str = ""
    s3_region: str = "local"
    s3_bucket: str = ""

    # Queue / worker — Redis + arq; unset REDIS_URL falls back to in-process.
    redis_url: str = ""

    # Encryption (Fernet) — dev key locally, real secret in prod.
    app_enc_key: str = ""
    app_enc_key_old: str = ""

    # Auth.
    auth_mode: Literal["local", "oauth", "off"] = "local"
    google_client_id: str = ""
    google_client_secret: str = ""
    auth_callback_url: str = ""

    # LLM gateway (Bifrost) — same base URL both envs (LOCKED code).
    bifrost_base_url: str = ""

    # Observability (Langfuse) — env creds both envs.
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = ""

    # Jira — env creds both envs (single shared account; per-user deferred).
    jira_server: str = ""
    jira_email: str = ""
    jira_api_token: str = ""
    jira_project_key: str = ""

    # CORS allow-list (the Render web origin in prod).
    app_allowed_origins: str = ""

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def is_local(self) -> bool:
        return self.app_env == "local"

    @property
    def use_redis_queue(self) -> bool:
        """True when a Redis-backed arq queue should be used (else in-process)."""
        return bool(self.redis_url)

    def assert_safe_for(self, action: str) -> None:
        """Guard rail: refuse seed/destructive actions when APP_ENV=production.

        Seeds and destructive scripts must never run against production; call
        this at the top of any such script/route so a mis-set env fails loudly
        instead of mutating the wrong database.
        """
        if self.is_production:
            raise RuntimeError(
                f"Refusing to {action}: APP_ENV=production. Seeds and destructive "
                "scripts run only against a non-production environment."
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide cached :class:`Settings`.

    Cached so every caller shares one instance; tests call
    ``get_settings.cache_clear()`` to rebuild after mutating the environment.
    """
    return Settings()

PROVIDER_REGISTRY = {
    "openai": {"base_url": "https://api.openai.com/v1", "show_base_url": False},
    "anthropic": {"base_url": "https://api.anthropic.com", "show_base_url": False},
    "google": {"base_url": "https://generativelanguage.googleapis.com/v1", "show_base_url": False},
    "ollama": {"base_url": "http://localhost:11434", "show_base_url": True},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "show_base_url": False},
    "groq": {"base_url": "https://api.groq.com/openai/v1", "show_base_url": False},
    "mistral": {"base_url": "https://api.mistral.ai/v1", "show_base_url": False},
    "together": {"base_url": "https://api.together.xyz/v1", "show_base_url": False},
    "cohere": {"base_url": "https://api.cohere.ai/v1", "show_base_url": False},
    "azure": {"base_url": None, "show_base_url": True},
    "zai": {"base_url": "https://api.z.ai/v1", "show_base_url": True},
}

def resolve_provider_base(provider: str, base_url: Optional[str]) -> Optional[str]:
    reg = PROVIDER_REGISTRY.get(provider, {})
    return base_url or reg.get("base_url")

def _ensure_docker_host(url: Optional[str]) -> Optional[str]:
    """
    Ensures that local URLs (localhost/127.0.0.1) are translated to the correct
    Docker host DNS/IP so the container can reach the host machine.
    Also ensures a protocol prefix is present.
    """
    if not url:
        return url
    
    # 1. Handle missing protocol
    if not url.startswith(("http://", "https://")):
        url = f"http://{url}"
        
    # 2. Translate localhost to Docker Host
    # Use environment variable from .env, fallback to standard host.docker.internal
    docker_host = os.environ.get("DOCKER_HOST_INTERNAL", "host.docker.internal")
    
    if "localhost" in url:
        return url.replace("localhost", docker_host)
    if "127.0.0.1" in url:
        return url.replace("127.0.0.1", docker_host)
        
    return url

def build_litellm_model(
    provider: str, model: Optional[str], azure_deployment: Optional[str]
) -> str:
    if not model:
        model = ""

    # Aggregator providers always need their own prefix even when the model id
    # itself contains a slash (e.g. OpenRouter's vendor/model IDs like
    # "qwen/qwen3.5-flash" → "openrouter/qwen/qwen3.5-flash"). LiteLLM uses the
    # outermost prefix to pick the provider client, and OpenRouter / Together
    # both publish models under vendor/model names.
    if provider in {"openrouter", "together"}:
        clean = model[len(f"{provider}/"):] if model.startswith(f"{provider}/") else model
        return f"{provider}/{clean}" if clean else f"{provider}/"

    # If the model already starts with the correct provider prefix, return it as is
    if provider == "ollama" and model.startswith("ollama/"):
        return model
    if provider == "google" and (model.startswith("gemini/") or model.startswith("vertex_ai/")):
        return model
    if provider == "azure" and model.startswith("azure/"):
        return model

    # Force provider prefix for Ollama even if name contains slashes (e.g. hf.co/...)
    if provider == "ollama":
        return f"ollama/{model}" if model else "ollama/"

    # For other providers, if it already has a slash, assume it's already prefixed
    if "/" in model:
        return model
    if provider == "google":
        return f"gemini/{model}" if model else "gemini/"
    if provider in {"anthropic", "cohere"}:
        return f"{provider}/{model}" if model else f"{provider}/"
    if provider == "azure":
        deployment = azure_deployment or model
        return f"azure/{deployment}" if deployment else "azure/"
    if provider in {"openai", "groq", "mistral", "zai"}:
        return f"{provider}/{model}" if model else f"{provider}/"
    return model

class SettingsManager:
    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.settings_path = self.data_dir / "settings.json"
        self.keyfile_path = self.data_dir / ".keyfile"
        self.fernet = Fernet(self._load_or_create_fernet_key())

    def _derive_fernet_key(self, raw: bytes) -> bytes:
        if len(raw) == 32:
            return base64.urlsafe_b64encode(raw)
        return raw

    def _load_or_create_fernet_key(self) -> bytes:
        env_key = os.environ.get("SOW_FERNET_KEY")
        if env_key:
            return env_key.encode("utf-8")

        self.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.keyfile_path.exists():
            raw = secrets.token_bytes(32)
            with open(self.keyfile_path, "wb") as f:
                f.write(raw)
            try:
                os.chmod(self.keyfile_path, 0o600)
            except OSError:
                pass
        with open(self.keyfile_path, "rb") as f:
            raw = f.read().strip()
        return self._derive_fernet_key(raw)

    def encrypt_secret(self, value: str) -> str:
        return self.fernet.encrypt(value.encode("utf-8")).decode("utf-8")

    def decrypt_secret(self, value: str) -> str:
        return self.fernet.decrypt(value.encode("utf-8")).decode("utf-8")

    def load(self) -> dict:
        if not self.settings_path.exists():
            return {}
        try:
            with open(self.settings_path, "r") as f:
                data = json.load(f)
                if "providers" not in data:
                    provider = data.get("provider") or "openai"
                    data["providers"] = {
                        provider: {
                            "model": data.get("model", ""),
                            "api_key": data.get("api_key", ""),
                            "base_url": data.get("base_url", ""),
                            "azure_deployment_name": data.get("azure_deployment_name", ""),
                            "azure_api_version": data.get("azure_api_version", ""),
                        }
                    }
                return data
        except (json.JSONDecodeError, ValueError):
            raise RuntimeError("Corrupted settings.json")

    def save(self, data: dict) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with open(self.settings_path, "w") as f:
            json.dump(data, f, indent=2)
