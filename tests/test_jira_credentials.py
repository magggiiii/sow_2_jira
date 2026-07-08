# tests/test_jira_credentials.py
"""WAVE 2 STEP 2.4 (env/shared scope) — JiraCredentials model + injection.

JiraClient historically read JIRA_SERVER / JIRA_EMAIL / JIRA_API_TOKEN directly
from os.environ inside __init__. This centralizes those reads into a
``JiraCredentials`` model (with a ``from_env()`` shared-account helper) and lets a
caller inject credentials, so the client body itself reads no environment. Per
the handoff, this is the env/shared-account scope; per-user credential rows are
deferred.
"""

from __future__ import annotations

import os
from unittest import mock

from models.schemas import JiraCredentials, JiraHierarchy


class _NoOpAudit:
    def log(self, **kwargs):
        return None


class _RaisingEnviron(dict):
    def __getitem__(self, key):  # noqa: ANN001
        raise AssertionError(f"JiraClient read os.environ[{key!r}]")

    def get(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("JiraClient read os.environ via .get()")


def test_jira_credentials_explicit_fields():
    creds = JiraCredentials(
        server_url="https://x.atlassian.net",
        email="a@b.test",
        api_token="tok",
        project_key="SOW",
    )
    assert creds.server_url == "https://x.atlassian.net"
    assert creds.email == "a@b.test"
    assert creds.api_token == "tok"
    assert creds.project_key == "SOW"


def test_jira_credentials_from_env(monkeypatch):
    monkeypatch.setenv("JIRA_SERVER", "https://env.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "env@b.test")
    monkeypatch.setenv("JIRA_API_TOKEN", "env-tok")
    monkeypatch.setenv("JIRA_PROJECT_KEY", "ENVK")
    creds = JiraCredentials.from_env()
    assert creds.server_url == "https://env.atlassian.net"
    assert creds.email == "env@b.test"
    assert creds.api_token == "env-tok"
    assert creds.project_key == "ENVK"


def test_jira_credentials_from_env_missing_is_empty(monkeypatch):
    for var in ("JIRA_SERVER", "JIRA_EMAIL", "JIRA_API_TOKEN", "JIRA_PROJECT_KEY"):
        monkeypatch.delenv(var, raising=False)
    creds = JiraCredentials.from_env()
    assert creds.server_url == ""
    assert creds.api_token == ""


def test_jira_client_uses_injected_credentials_without_env(monkeypatch):
    """With credentials injected, JiraClient reads NO os.environ."""
    from integrations.jira_client import JiraClient

    creds = JiraCredentials(
        server_url="https://inj.atlassian.net",
        email="inj@b.test",
        api_token="inj-tok",
        project_key="INJ",
    )
    with mock.patch("integrations.jira_client.JIRA") as JiraSDK:
        # Fail loudly if the client touches the environment at all.
        monkeypatch.setattr(os, "environ", _RaisingEnviron())
        client = JiraClient(
            hierarchy=JiraHierarchy.EPIC_TASK,
            audit=_NoOpAudit(),
            run_id="jc-cred-test",
            project_key="INJ",
            credentials=creds,
        )
    assert client.server == "https://inj.atlassian.net"
    # The SDK was constructed with the injected basic_auth, not env values.
    _, kwargs = JiraSDK.call_args
    assert kwargs["basic_auth"] == ("inj@b.test", "inj-tok")


def test_jira_client_falls_back_to_env_when_no_credentials(monkeypatch):
    """Back-compat: existing callers that set JIRA_* env still work."""
    from integrations.jira_client import JiraClient

    monkeypatch.setenv("JIRA_SERVER", "https://fallback.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "fb@b.test")
    monkeypatch.setenv("JIRA_API_TOKEN", "fb-tok")
    with mock.patch("integrations.jira_client.JIRA") as JiraSDK:
        client = JiraClient(
            hierarchy=JiraHierarchy.EPIC_TASK,
            audit=_NoOpAudit(),
            run_id="jc-fallback-test",
            project_key="FB",
        )
    assert client.server == "https://fallback.atlassian.net"
    _, kwargs = JiraSDK.call_args
    assert kwargs["basic_auth"] == ("fb@b.test", "fb-tok")
