"""SEAM-A: progress-store port + in-memory fake (offline).

Verifies the ProgressStore Protocol contract and the FakeProgressStore adapter.
This replaces the in-process ``active_runs`` status dict with a port a future
Redis-backed adapter can also satisfy.
"""

from __future__ import annotations

from core.progress_port import ProgressStore
from integrations.progress.fake import FakeProgressStore


def test_fake_progress_store_satisfies_protocol():
    # Gate 1: structural isinstance against the runtime_checkable Protocol.
    assert isinstance(FakeProgressStore(), ProgressStore) is True


def test_set_then_get_round_trips():
    store = FakeProgressStore()
    store.set("run-1", "running", 0.5, 3, "halfway there")
    snap = store.get("run-1")
    assert snap is not None
    assert snap["status"] == "running"
    assert snap["progress"] == 0.5
    assert snap["current_step"] == 3
    assert snap["message"] == "halfway there"


def test_get_unknown_returns_none():
    store = FakeProgressStore()
    assert store.get("nope") is None


def test_set_overwrites_prior_state():
    store = FakeProgressStore()
    store.set("run-1", "queued", 0.0, 0, "queued")
    store.set("run-1", "done", 1.0, 9, "complete")
    snap = store.get("run-1")
    assert snap["status"] == "done"
    assert snap["progress"] == 1.0
    assert snap["current_step"] == 9
    assert snap["message"] == "complete"
