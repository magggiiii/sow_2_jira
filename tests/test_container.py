# tests/test_container.py
"""
Tests for the additive composition root in app/container.py.

These assert the wired ``Container`` exposes port-satisfying concretes:

  * ``.llm``          satisfies ``core.ports.LLMProvider``
  * ``.audit``        satisfies ``core.ports.AuditSink``
  * ``.object_store`` satisfies ``core.ports.ObjectStore`` (round-trip put/get)

No network or LLM calls are made. The LLM is injected as a tiny in-memory fake
(constructing a real ``LLMClient`` resolves provider config from settings/env,
which we don't want in a unit test); the real ``AuditLogger`` is cheap (SQLite)
and pointed at a temp DB so it never touches ``data/audit.db``.
"""

from app.container import Container, LocalObjectStore, build_container
from core.ports import AuditSink, LLMProvider, ObjectStore


class _FakeLLM:
    """Minimal in-memory LLMProvider implementation (no network)."""

    def complete(
        self,
        prompt,
        system="You are a helpful assistant.",
        temperature=0.0,
        max_tokens=4096,
        agent_name="unknown",
        node_id="",
    ):
        return "fake"

    def complete_json(
        self,
        prompt,
        system="You are a precise JSON extraction assistant.",
        agent_name="unknown",
        node_id="",
        max_tokens=8192,
    ):
        return []


def _build(tmp_path, monkeypatch):
    """Build a container with offline-safe concretes."""
    from audit.logger import AuditLogger

    monkeypatch.setattr(AuditLogger, "DB_PATH", tmp_path / "audit.db")
    return build_container(
        run_id="test-run",
        llm=_FakeLLM(),
        object_store_base_dir=str(tmp_path / "store"),
    )


def test_build_container_returns_container(tmp_path, monkeypatch):
    container = _build(tmp_path, monkeypatch)
    assert isinstance(container, Container)
    # jira is a per-push factory callable, not an instance.
    assert callable(container.jira)


def test_container_llm_satisfies_llmprovider(tmp_path, monkeypatch):
    container = _build(tmp_path, monkeypatch)
    assert isinstance(container.llm, LLMProvider)


def test_container_audit_satisfies_auditsink(tmp_path, monkeypatch):
    container = _build(tmp_path, monkeypatch)
    try:
        assert isinstance(container.audit, AuditSink)
        # And it actually works as one.
        container.audit.log(run_id="test-run", agent="a", action="ACT", detail="d")
    finally:
        container.audit.close()


def test_container_object_store_satisfies_objectstore(tmp_path, monkeypatch):
    container = _build(tmp_path, monkeypatch)
    store = container.object_store
    assert isinstance(store, ObjectStore)
    assert isinstance(store, LocalObjectStore)

    key = "sessions/test-run/pipeline_output.json"
    assert store.exists(key) is False
    locator = store.put(key, b'{"hello": "world"}')
    assert isinstance(locator, str)
    assert store.exists(key) is True
    assert store.get(key) == b'{"hello": "world"}'


def test_local_object_store_roundtrip_under_tmp_dir(tmp_path):
    store = LocalObjectStore(base_dir=str(tmp_path))
    store.put("a/b/c.bin", b"\x00\x01\x02")
    assert store.get("a/b/c.bin") == b"\x00\x01\x02"
    # Written under the configured base dir, nowhere else.
    assert (tmp_path / "a" / "b" / "c.bin").read_bytes() == b"\x00\x01\x02"
