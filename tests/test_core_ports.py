# tests/test_core_ports.py
"""
Tests for the hexagonal-seam Protocol ports in core/ports.py.

Two verification strategies are used, and which one applies to each port is
noted inline:

  (a) RUNTIME isinstance — for ports where we can cheaply build a tiny
      dependency-free in-memory fake, we assert isinstance(fake, Port) so the
      @runtime_checkable behaviour is exercised end-to-end.

  (b) METHOD-PRESENCE (hasattr) — for the real LLMClient, constructing an
      instance would resolve provider config from encrypted settings / env and
      isn't worth the side effects in a no-network test. So we verify the
      class structurally lines up by checking the methods exist (hasattr) and
      that the abstract LLMProvider protocol "covers" no more than what the
      class offers. This is the documented fallback the task allows.
"""

import inspect

from core.ports import (
    AuditSink,
    CredentialRepository,
    EmbeddingIndex,
    JiraGateway,
    LLMProvider,
    ObjectStore,
    RunRepository,
    TaskRepository,
)


# ─── (a) In-memory fakes + runtime isinstance ─────────────────────────────────


class FakeLLM:
    """Minimal in-memory LLMProvider implementation."""

    def complete(
        self,
        prompt,
        system="You are a helpful assistant.",
        temperature=0.0,
        max_tokens=4096,
        agent_name="unknown",
        node_id="",
    ):
        return "fake response"

    def complete_json(
        self,
        prompt,
        system="You are a precise JSON extraction assistant.",
        agent_name="unknown",
        node_id="",
        max_tokens=8192,
    ):
        return []


class FakeAudit:
    """Minimal in-memory AuditSink implementation that records calls."""

    def __init__(self):
        self.records = []

    def log(
        self,
        run_id,
        agent,
        action,
        detail,
        node_id=None,
        task_id=None,
        llm_tokens_used=0,
        llm_model="",
    ):
        self.records.append((run_id, agent, action, detail))


def test_fake_llm_is_llmprovider_runtime_checkable():
    fake = FakeLLM()
    assert isinstance(fake, LLMProvider)
    # And it actually works as one.
    assert fake.complete("hi") == "fake response"
    assert fake.complete_json("hi") == []


def test_fake_audit_is_auditsink_runtime_checkable():
    fake = FakeAudit()
    assert isinstance(fake, AuditSink)
    fake.log(run_id="r1", agent="a", action="ACT", detail="d")
    assert fake.records == [("r1", "a", "ACT", "d")]


def test_runtime_checkable_rejects_missing_methods():
    class NotAnLLM:
        def complete(self, prompt):  # missing complete_json
            return ""

    assert not isinstance(NotAnLLM(), LLMProvider)


# ─── (b) Real LLMClient: method-presence (hasattr) check ──────────────────────


def test_real_llmclient_class_structurally_matches_llmprovider():
    """
    Verify the real LLMClient lines up with LLMProvider WITHOUT instantiating
    it (construction resolves provider config from settings/env). Strategy used:
    METHOD-PRESENCE via hasattr on the class, plus a signature parameter-name
    check so the structural match is meaningful, not just name-presence.
    """
    from pipeline.llm_client import LLMClient

    for method in ("complete", "complete_json"):
        assert hasattr(LLMClient, method), f"LLMClient missing {method}"

    # Spot-check that the public parameter names the core relies on exist on
    # the real methods (sanity that the protocol mirrors reality).
    complete_params = set(inspect.signature(LLMClient.complete).parameters)
    assert {"prompt", "system", "temperature", "max_tokens", "agent_name", "node_id"} <= complete_params

    complete_json_params = set(inspect.signature(LLMClient.complete_json).parameters)
    assert {"prompt", "system", "agent_name", "node_id", "max_tokens"} <= complete_json_params


# ─── Real adapters: method-presence on the class (no instantiation) ───────────


def test_real_jira_client_class_has_push_tasks():
    from integrations.jira_client import JiraClient

    assert hasattr(JiraClient, "push_tasks")
    params = set(inspect.signature(JiraClient.push_tasks).parameters)
    assert "tasks" in params


def test_real_audit_logger_instance_is_auditsink(tmp_path, monkeypatch):
    """
    AuditLogger is cheap to construct (SQLite only), so use the (a) runtime
    isinstance strategy on a real instance. Point its DB at a temp path so we
    don't touch data/audit.db.
    """
    from audit.logger import AuditLogger

    monkeypatch.setattr(AuditLogger, "DB_PATH", tmp_path / "audit.db")
    real = AuditLogger()
    try:
        assert isinstance(real, AuditSink)
    finally:
        real.close()


def test_real_embedding_index_instance_is_embeddingindex(tmp_path):
    """ProjectEmbeddingIndex is cheap to construct (no IO until used)."""
    from pipeline.agents.cross_run_index import ProjectEmbeddingIndex

    idx = ProjectEmbeddingIndex(project_key="TEST", base_dir=str(tmp_path))
    assert isinstance(idx, EmbeddingIndex)


# ─── Forward-looking ports: importable + runtime_checkable, not yet implemented


def test_forward_looking_ports_are_runtime_checkable():
    class FakeStore:
        def put(self, key, data):
            return key

        def get(self, key):
            return b""

        def exists(self, key):
            return False

    assert isinstance(FakeStore(), ObjectStore)


def test_repository_ports_exist_and_runtime_checkable():
    # These have no concrete implementation yet; just confirm the protocols are
    # importable and behave as runtime_checkable structural checks.
    class FakeRunRepo:
        def create(self, run_id, data):
            return data

        def get(self, run_id):
            return None

        def update(self, run_id, data):
            return data

        def list(self):
            return []

    assert isinstance(FakeRunRepo(), RunRepository)
    # An empty object satisfies none of them.
    assert not isinstance(object(), TaskRepository)
    assert not isinstance(object(), CredentialRepository)
