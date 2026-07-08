# tests/test_app_settings.py
"""WAVE 2 config layer — the APP_ENV flip (ENV-CONFIG.md).

``Settings`` is the single composition-root switch: ``APP_ENV=local|production``
plus the per-dependency env vars from the ENV-CONFIG matrix. It reads purely from
the process environment (the app loads .env.local / .env via load_dotenv first),
so these tests drive it with monkeypatch.setenv and stay deterministic.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from config.settings import Settings, get_settings

# Env vars this module touches — cleared before each test for isolation from a
# developer .env already loaded into the session.
_MATRIX_VARS = [
    "APP_ENV", "DATABASE_URL", "S3_ENDPOINT", "S3_ACCESS_KEY", "S3_SECRET",
    "S3_REGION", "S3_BUCKET", "REDIS_URL", "APP_ENC_KEY", "APP_ENC_KEY_OLD",
    "AUTH_MODE", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "AUTH_CALLBACK_URL",
    "BIFROST_BASE_URL", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY",
    "LANGFUSE_HOST", "JIRA_SERVER", "JIRA_EMAIL", "JIRA_API_TOKEN",
    "JIRA_PROJECT_KEY", "APP_ALLOWED_ORIGINS",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in _MATRIX_VARS:
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_default_app_env_is_local():
    s = Settings()
    assert s.app_env == "local"
    assert s.is_local is True
    assert s.is_production is False


def test_app_env_flip_to_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    s = Settings()
    assert s.is_production is True
    assert s.is_local is False


def test_invalid_app_env_is_rejected(monkeypatch):
    monkeypatch.setenv("APP_ENV", "staging")
    with pytest.raises(ValidationError):
        Settings()


def test_use_redis_queue_reflects_redis_url(monkeypatch):
    assert Settings().use_redis_queue is False
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    assert Settings().use_redis_queue is True


def test_reads_matrix_env_vars(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:54322/postgres")
    monkeypatch.setenv("S3_ENDPOINT", "http://127.0.0.1:54321/storage/v1/s3")
    monkeypatch.setenv("S3_REGION", "local")
    monkeypatch.setenv("AUTH_MODE", "oauth")
    monkeypatch.setenv("BIFROST_BASE_URL", "http://65.1.177.144:8080/")
    monkeypatch.setenv("JIRA_PROJECT_KEY", "SOW")
    s = Settings()
    assert s.database_url.endswith("/postgres")
    assert s.s3_endpoint.endswith("/storage/v1/s3")
    assert s.s3_region == "local"
    assert s.auth_mode == "oauth"
    assert s.bifrost_base_url == "http://65.1.177.144:8080/"
    assert s.jira_project_key == "SOW"


def test_auth_mode_default_is_local():
    assert Settings().auth_mode == "local"


def test_invalid_auth_mode_is_rejected(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "basic")
    with pytest.raises(ValidationError):
        Settings()


def test_assert_safe_for_raises_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    s = Settings()
    with pytest.raises(RuntimeError, match="production"):
        s.assert_safe_for("seed dev users")


def test_assert_safe_for_ok_in_local():
    Settings().assert_safe_for("seed dev users")  # no raise


def test_get_settings_is_cached_singleton():
    a = get_settings()
    b = get_settings()
    assert a is b
