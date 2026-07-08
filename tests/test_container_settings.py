# tests/test_container_settings.py
"""WAVE 2 config layer — build_container selects adapters from Settings.

ENV-CONFIG.md: "only the composition root chooses adapters." These tests pin the
LOCAL branch of that selection (LocalObjectStore + in-process FakeQueue) and that
the container now exposes a JobQueue port. Production branches (S3 / arq) are
lazy-imported and covered once those adapters land.
"""

from __future__ import annotations

from app.container import (
    Container,
    build_container,
    select_object_store,
    select_queue,
)
from config.settings import Settings
from core.queue_port import JobQueue
from integrations.object_store import LocalObjectStore
from integrations.queue.fake import FakeQueue


class _FakeLLM:
    def complete(self, *a, **k):
        return "fake"

    def complete_json(self, *a, **k):
        return []


def _local_settings() -> Settings:
    return Settings(app_env="local", redis_url="")


def test_select_object_store_local_returns_local(tmp_path):
    store = select_object_store(_local_settings(), base_dir=str(tmp_path))
    assert isinstance(store, LocalObjectStore)


def test_select_queue_without_redis_returns_fake():
    q = select_queue(_local_settings())
    assert isinstance(q, FakeQueue)
    assert isinstance(q, JobQueue)


def test_build_container_with_local_settings_uses_local_adapters(tmp_path, monkeypatch):
    from audit.logger import AuditLogger

    monkeypatch.setattr(AuditLogger, "DB_PATH", tmp_path / "audit.db")
    c = build_container(
        run_id="cfg-test",
        llm=_FakeLLM(),
        object_store_base_dir=str(tmp_path / "store"),
        settings=_local_settings(),
    )
    try:
        assert isinstance(c, Container)
        assert isinstance(c.object_store, LocalObjectStore)
        assert isinstance(c.queue, FakeQueue)
        assert isinstance(c.queue, JobQueue)
    finally:
        c.audit.close()


def test_build_container_without_settings_defaults_to_local(tmp_path, monkeypatch):
    """Back-compat: no settings → local adapters, and a queue is still wired."""
    from audit.logger import AuditLogger

    monkeypatch.setattr(AuditLogger, "DB_PATH", tmp_path / "audit.db")
    c = build_container(
        run_id="cfg-test",
        llm=_FakeLLM(),
        object_store_base_dir=str(tmp_path / "store"),
    )
    try:
        assert isinstance(c.object_store, LocalObjectStore)
        assert isinstance(c.queue, JobQueue)
    finally:
        c.audit.close()
