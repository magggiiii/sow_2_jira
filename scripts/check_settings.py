import pytest
import json
import os
from pathlib import Path
from config.settings import SettingsManager, build_litellm_model

def test_corrupted_settings_throws_error(tmp_path):
    # Setup corrupted settings.json
    settings_file = tmp_path / "settings.json"
    with open(settings_file, "w") as f:
        f.write("{ invalid json }")
    
    manager = SettingsManager(data_dir=str(tmp_path))
    
    with pytest.raises(RuntimeError, match="Corrupted settings.json"):
        manager.load()

def test_build_litellm_model():
    assert build_litellm_model("google", "gemini-1.5", None) == "gemini/gemini-1.5"
    assert build_litellm_model("ollama", "qwen2.5:7b", None) == "ollama/qwen2.5:7b"
    assert build_litellm_model("openai", "gpt-4o", None) == "openai/gpt-4o"
    assert build_litellm_model("anthropic", "claude-3", None) == "anthropic/claude-3"
    assert build_litellm_model("azure", "my-deployment", None) == "azure/my-deployment"
    assert build_litellm_model("azure", "unused-model", "actual-deployment") == "azure/actual-deployment"
    # Test model with slash
    assert build_litellm_model("openai", "custom/model", None) == "custom/model"

def test_settings_manager_encryption(tmp_path):
    manager = SettingsManager(data_dir=str(tmp_path))
    secret = "my-secret-key"
    encrypted = manager.encrypt_secret(secret)
    assert encrypted != secret
    assert manager.decrypt_secret(encrypted) == secret

def test_settings_manager_load_save(tmp_path):
    manager = SettingsManager(data_dir=str(tmp_path))
    data = {"provider": "openai", "model": "gpt-4"}
    manager.save(data)
    
    loaded = manager.load()
    # Check if migration logic applied if "providers" was missing
    assert "providers" in loaded
    assert loaded["providers"]["openai"]["model"] == "gpt-4"
