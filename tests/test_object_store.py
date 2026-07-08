"""Tests for integrations/object_store.py — LocalObjectStore (WAVE 1 STEP 1.4).

The ObjectStore Protocol (core/ports.py) is the stable seam; LocalObjectStore is
the offline, on-disk adapter that preserves current filesystem behavior during
the strangler period, before an R2/S3 adapter lands behind the same interface.
"""

import os

import pytest

from core.ports import ObjectStore
from integrations.object_store import LocalObjectStore, key_for


@pytest.fixture
def store(tmp_path):
    return LocalObjectStore(base_dir=tmp_path / "objects")


# ─── protocol conformance ────────────────────────────────────────────────────


def test_satisfies_objectstore_protocol(store):
    assert isinstance(store, ObjectStore)


# ─── round-trip ──────────────────────────────────────────────────────────────


def test_put_get_round_trip(store):
    key = "sessions/run-1/pipeline_output.json"
    payload = b'{"tasks": []}'
    locator = store.put(key, payload)
    assert isinstance(locator, str)
    assert store.get(key) == payload


def test_put_creates_nested_dirs(store, tmp_path):
    store.put("a/b/c/d.bin", b"deep")
    assert store.get("a/b/c/d.bin") == b"deep"


def test_put_overwrites(store):
    store.put("k", b"first")
    store.put("k", b"second")
    assert store.get("k") == b"second"


def test_exists(store):
    assert store.exists("k") is False
    store.put("k", b"x")
    assert store.exists("k") is True


def test_get_missing_raises(store):
    with pytest.raises(KeyError):
        store.get("does/not/exist")


# ─── user-scoped keys ────────────────────────────────────────────────────────


def test_key_for_is_user_scoped():
    assert key_for("u1", "r1", "output.json") == "users/u1/runs/r1/output.json"


def test_key_for_rejects_traversal_in_name():
    with pytest.raises(ValueError):
        key_for("u1", "r1", "../../etc/passwd")


@pytest.mark.parametrize(
    "user_id,run_id,name",
    [
        ("../u", "r1", "out.json"),
        ("u1", "../r", "out.json"),
        ("u1/x", "r1", "out.json"),
        ("u1", "r1/x", "out.json"),
        ("u1", "r1", ""),
    ],
)
def test_key_for_rejects_bad_components(user_id, run_id, name):
    with pytest.raises(ValueError):
        key_for(user_id, run_id, name)


# ─── path-traversal safety ───────────────────────────────────────────────────


@pytest.mark.parametrize("bad", ["../evil", "a/../../evil", "/abs/path", "a/../b", "..\\evil"])
def test_put_rejects_traversal(store, bad):
    with pytest.raises(ValueError):
        store.put(bad, b"x")


def test_get_rejects_traversal(store):
    with pytest.raises(ValueError):
        store.get("../evil")


def test_traversal_cannot_escape_base(store, tmp_path):
    # Even a crafted key must never write outside base_dir.
    sentinel = tmp_path / "outside.txt"
    with pytest.raises(ValueError):
        store.put("../outside.txt", b"pwned")
    assert not sentinel.exists()


def test_null_byte_key_rejected(store):
    with pytest.raises(ValueError):
        store.put("a\x00b", b"x")


def test_symlink_component_cannot_escape_base(tmp_path):
    """A pre-existing symlink at an intermediate component must not let a
    syntactically-valid key read or write outside the store."""
    base = tmp_path / "objects"
    base.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("TOP SECRET")
    os.symlink(outside, base / "link")

    store = LocalObjectStore(base_dir=base)

    # READ escape blocked.
    with pytest.raises(ValueError):
        store.get("link/secret.txt")

    # WRITE escape blocked — and nothing lands outside.
    with pytest.raises(ValueError):
        store.put("link/planted.txt", b"pwned")
    assert not (outside / "planted.txt").exists()


# ─── delete_run_prefix ───────────────────────────────────────────────────────


def test_delete_run_prefix_removes_only_that_prefix(store):
    store.put("users/u1/runs/r1/a.json", b"1")
    store.put("users/u1/runs/r1/b.json", b"2")
    store.put("users/u1/runs/r2/c.json", b"3")
    removed = store.delete_run_prefix("users/u1/runs/r1")
    assert removed == 2
    assert not store.exists("users/u1/runs/r1/a.json")
    assert not store.exists("users/u1/runs/r1/b.json")
    assert store.exists("users/u1/runs/r2/c.json")


def test_delete_run_prefix_rejects_traversal(store):
    with pytest.raises(ValueError):
        store.delete_run_prefix("../..")


def test_delete_run_prefix_absent_is_zero(store):
    assert store.delete_run_prefix("users/u1/runs/nope") == 0


def test_delete_run_prefix_does_not_match_string_prefix_sibling(store):
    """Deleting run 'r1' must not touch 'r10' (path/dir semantics, not string
    prefix matching)."""
    store.put("users/u1/runs/r1/a.json", b"1")
    store.put("users/u1/runs/r10/b.json", b"2")
    removed = store.delete_run_prefix("users/u1/runs/r1")
    assert removed == 1
    assert store.exists("users/u1/runs/r10/b.json")
