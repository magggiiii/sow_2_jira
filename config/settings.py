import json
import os
import base64
import secrets
from pathlib import Path
from typing import Optional, Dict, Any
from cryptography.fernet import Fernet

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

def build_litellm_model(provider: str, model: Optional[str], azure_deployment: Optional[str]) -> str:
    if not model:
        model = ""
    if "/" in model:
        return model
    if provider == "ollama":
        return f"ollama/{model}" if model else "ollama/"
    if provider == "google":
        return f"gemini/{model}" if model else "gemini/"
    if provider in {"anthropic", "cohere"}:
        return f"{provider}/{model}" if model else f"{provider}/"
    if provider == "azure":
        deployment = azure_deployment or model
        return f"azure/{deployment}" if deployment else "azure/"
    if provider in {"openai", "openrouter", "groq", "mistral", "together", "zai"}:
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
