# tests/test_orchestrator_object_store.py
"""
WAVE-0 STEP 1.6: PipelineOrchestrator run-artifact persistence goes through an
injectable ``ObjectStore`` (core.ports.ObjectStore) plus optional
``run_repo``/``task_repo`` DB write-through.

Sub-steps proven here:

  1.6a — the constructor accepts OPTIONAL ``object_store``/``run_repo``/``task_repo``
         kwargs. ``object_store=None`` DEFAULTS to a LocalObjectStore rooted at the
         existing data dir so no other call site (main.py / ui/server.py) breaks and
         behavior is preserved.
  1.6b — every run-artifact write/read routes through ``self._object_store`` using
         stable keys; the orchestrator never opens a file under data/sessions itself.
  1.6c — when repos are injected, the run + task records are write-through persisted
         to them, and NO plaintext api_key/token is ever handed to the repo.

The fakes below structurally satisfy the ports so the seam stays honest.
"""

from __future__ import annotations

import builtins
import json

from core.ports import ObjectStore
from integrations.object_store import LocalObjectStore
from models.schemas import JiraHierarchy, LLMMode, ProviderConfig, RunConfig
from pipeline.coverage import CoverageTracker
from pipeline.orchestrator import PipelineOrchestrator

# ─── fakes ────────────────────────────────────────────────────────────────────


class FakeLLM:
    def complete(self, *a, **k):
        return ""

    def complete_json(self, *a, **k):
        return []


class FakeAudit:
    def log(self, **kwargs):
        return None


class FakeIndexer:
    def __init__(self):
        self.last_tree = {"structure": []}

    def get_node_text(self, node):
        return node.get("text", "")

    def flatten_tree(self, tree):
        return tree


class FakeObjectStore:
    """In-memory ObjectStore. Structurally satisfies core.ports.ObjectStore."""

    def __init__(self):
        self.store: dict[str, bytes] = {}
        self.puts: list[str] = []
        self.gets: list[str] = []

    def put(self, key: str, data: bytes) -> str:
        assert isinstance(data, (bytes, bytearray)), "store must receive bytes"
        self.store[key] = bytes(data)
        self.puts.append(key)
        return f"fake://{key}"

    def get(self, key: str) -> bytes:
        self.gets.append(key)
        return self.store[key]

    def exists(self, key: str) -> bool:
        return key in self.store


class FakeRunRepo:
    def __init__(self):
        self.records: dict[str, dict] = {}

    def create(self, run_id, data):
        self.records[run_id] = data
        return data

    def get(self, run_id):
        return self.records.get(run_id)

    def update(self, run_id, data):
        self.records[run_id] = data
        return data

    def list(self):
        return list(self.records.values())


class FakeTaskRepo:
    def __init__(self):
        self.tasks: list[tuple[str, object]] = []

    def add(self, run_id, task):
        self.tasks.append((run_id, task))
        return task

    def get(self, run_id, task_id):
        return None

    def list_for_run(self, run_id):
        return [t for r, t in self.tasks if r == run_id]

    def update(self, run_id, task_id, data):
        return data


def _config(**overrides) -> RunConfig:
    base = dict(
        run_id="obj-store-test",
        sow_pdf_path="/tmp/x.pdf",
        llm_mode=LLMMode.CUSTOM,
        jira_hierarchy=JiraHierarchy.EPIC_TASK,
        jira_project_key="TEST",
        enable_resumption=False,
    )
    base.update(overrides)
    return RunConfig(**base)


def _app_config() -> dict:
    return {"pipeline": {"max_gap_recovery_iterations": 1, "max_section_chars": 16000}}


def _make_orch(object_store=None, run_repo=None, task_repo=None, **cfg_over):
    orch = PipelineOrchestrator(
        config=_config(**cfg_over),
        app_config=_app_config(),
        audit=FakeAudit(),
        llm=FakeLLM(),
        object_store=object_store,
        run_repo=run_repo,
        task_repo=task_repo,
    )
    orch.classifier = None
    orch.critic = None
    orch.coverage_checker = None
    orch.indexer = FakeIndexer()
    return orch


# ─── 1.6a: constructor seam ──────────────────────────────────────────────────


def test_fake_object_store_satisfies_port():
    assert isinstance(FakeObjectStore(), ObjectStore)


def test_object_store_defaults_to_local_when_absent():
    orch = _make_orch()
    assert isinstance(orch._object_store, LocalObjectStore)


def test_injected_object_store_is_used_verbatim():
    fake = FakeObjectStore()
    orch = _make_orch(object_store=fake)
    assert orch._object_store is fake


def test_repos_default_to_none_and_are_stored_when_injected():
    orch = _make_orch()
    assert orch._run_repo is None
    assert orch._task_repo is None

    rr, tr = FakeRunRepo(), FakeTaskRepo()
    orch2 = _make_orch(run_repo=rr, task_repo=tr)
    assert orch2._run_repo is rr
    assert orch2._task_repo is tr


def test_default_construction_still_works_without_new_kwargs():
    # No object_store/run_repo/task_repo kwargs — existing call sites (main.py,
    # ui/server.py) must keep constructing the orchestrator unchanged.
    orch = PipelineOrchestrator(
        config=_config(),
        app_config=_app_config(),
        audit=FakeAudit(),
        llm=FakeLLM(),
    )
    assert isinstance(orch._object_store, LocalObjectStore)
    assert orch._run_repo is None
    assert orch._task_repo is None


# ─── 1.6b: run-artifact I/O routes through the store ─────────────────────────


def _nodes(n):
    return [
        {"node_id": f"n{i}", "title": f"N{i}", "text": "x" * 200,
         "parent_id": None, "parent_chain": [], "depth": 0, "node_index": i,
         "page_start": 1, "page_end": 1}
        for i in range(n)
    ]


def _guard_open_against_sessions(monkeypatch):
    """monkeypatch builtins.open to RAISE if called with any path under
    data/sessions — proving the orchestrator persists ONLY via the store."""
    real_open = builtins.open

    def guarded_open(file, *a, **k):
        if "data/sessions" in str(file):
            raise AssertionError(
                f"orchestrator opened a data/sessions path directly: {file!r}"
            )
        return real_open(file, *a, **k)

    monkeypatch.setattr(builtins, "open", guarded_open)


def test_status_mirror_routes_through_store_no_direct_sessions_open(tmp_path, monkeypatch):
    fake = FakeObjectStore()
    orch = _make_orch(object_store=fake)
    _guard_open_against_sessions(monkeypatch)

    orch._mirror_status(3, "processing", 0.5)

    key = "sessions/obj-store-test/status.json"
    assert key in fake.store
    payload = json.loads(fake.store[key].decode("utf-8"))
    assert payload["step"] == 3 and payload["message"] == "processing"


def test_checkpoint_write_and_read_route_through_store(tmp_path, monkeypatch):
    fake = FakeObjectStore()
    orch = _make_orch(object_store=fake, enable_resumption=True)
    _guard_open_against_sessions(monkeypatch)

    nodes = _nodes(2)
    coverage = CoverageTracker(nodes)
    orch._write_extraction_checkpoint(nodes, 0, [], [], coverage)

    ckpt_key = "sessions/obj-store-test/extraction_checkpoint.json"
    assert ckpt_key in fake.store

    loaded = orch._load_extraction_checkpoint()
    assert loaded is not None
    assert loaded["last_index"] == 0
    assert loaded["node_ids"] == ["n0", "n1"]


def test_tree_cache_write_and_read_route_through_store(tmp_path, monkeypatch):
    fake = FakeObjectStore()
    orch = _make_orch(object_store=fake, skip_indexing=True)

    tree = [{"node_id": "n0", "title": "N0", "text": "hello"}]
    orch.indexer.last_tree = tree

    # No cached tree yet → build path runs; stub build_tree to return nodes and
    # set last_tree so the cache write happens through the store.
    def fake_build(*a, **k):
        orch.indexer.last_tree = tree
        return tree

    orch.indexer.build_tree = fake_build
    _guard_open_against_sessions(monkeypatch)

    orch._build_or_load_tree("/tmp/x.pdf")
    cache_key = "sessions/obj-store-test/document_tree.json"
    assert cache_key in fake.store

    # Now a skip-indexing run must LOAD from the store (no direct sessions open).
    nodes = orch._build_or_load_tree("/tmp/x.pdf")
    assert nodes == tree
    assert cache_key in fake.gets


def test_local_store_round_trip_is_byte_identical(tmp_path):
    store = LocalObjectStore(base_dir=str(tmp_path))
    orch = _make_orch(object_store=store)
    payload = json.dumps({"run_id": "obj-store-test", "tasks": [1, 2, 3]},
                         indent=2).encode("utf-8")
    orch._put_artifact("sessions/obj-store-test/pipeline_output.json", payload)
    got = orch._get_artifact("sessions/obj-store-test/pipeline_output.json")
    assert got == payload


# ─── helpers for stage-driven gates ──────────────────────────────────────────


def _managed_task(title="T"):
    from models.schemas import ManagedTask
    return ManagedTask(title=title, short_description="d", confidence=0.9)


def _ctx_for_save(orch, tasks):
    from core.pipeline.context import PipelineContext
    ctx = PipelineContext(orch=orch)
    ctx.deduplicated = tasks
    ctx.report = {"coverage_pct": 100.0, "covered_nodes": 1, "total_nodes": 1}
    ctx.run_start = 0.0
    return ctx


# ─── GATE 1: full persistence path uses ONLY the store (no data/sessions open) ─


def test_full_save_path_uses_only_store_no_direct_sessions_open(monkeypatch):
    fake = FakeObjectStore()
    orch = _make_orch(object_store=fake)
    orch.section_coverage_reports = {"n0": {"missed_items": []}}
    _guard_open_against_sessions(monkeypatch)

    tasks = [_managed_task("A"), _managed_task("B")]
    orch._stage_save(_ctx_for_save(orch, tasks))

    # The whole save phase completed WITHOUT opening any data/sessions path — it
    # persisted purely through the fake store.
    assert f"sessions/{orch.config.run_id}/pipeline_output.json" in fake.store
    assert f"sessions/{orch.config.run_id}/coverage_reports.json" in fake.store
    body = json.loads(
        fake.store[f"sessions/{orch.config.run_id}/pipeline_output.json"].decode("utf-8")
    )
    assert len(body["tasks"]) == 2


def test_real_local_store_full_save_round_trip_byte_identical(tmp_path):
    store = LocalObjectStore(base_dir=str(tmp_path))
    orch = _make_orch(object_store=store)
    tasks = [_managed_task("A")]
    orch._stage_save(_ctx_for_save(orch, tasks))

    key = f"sessions/{orch.config.run_id}/pipeline_output.json"
    on_disk = (tmp_path / "sessions" / orch.config.run_id / "pipeline_output.json").read_bytes()
    via_get = store.get(key)
    assert via_get == on_disk  # put→get is byte-identical for the same key


# ─── GATE 2 (1.6c): DB write-through + secret stripping ───────────────────────


def _config_with_secret():
    return _config(
        provider_config=ProviderConfig(
            provider="openai",
            model="gpt-4o",
            api_key="sk-SUPER-SECRET-abc123",
            api_base="https://api.openai.com/v1",
        )
    )


def test_repos_receive_run_and_tasks_on_save():
    rr, tr = FakeRunRepo(), FakeTaskRepo()
    orch = _make_orch(object_store=FakeObjectStore(), run_repo=rr, task_repo=tr)
    tasks = [_managed_task("A"), _managed_task("B"), _managed_task("C")]
    orch._stage_save(_ctx_for_save(orch, tasks))

    assert orch.config.run_id in rr.records
    assert len(tr.list_for_run(orch.config.run_id)) == 3
    assert {t.title for _, t in tr.tasks} == {"A", "B", "C"}


def test_repo_write_through_is_noop_without_repos():
    orch = _make_orch(object_store=FakeObjectStore())  # no repos
    # Must not raise and must persist artifacts as usual.
    orch._stage_save(_ctx_for_save(orch, [_managed_task("A")]))


def test_no_plaintext_api_key_reaches_the_run_repo():
    rr = FakeRunRepo()
    orch = PipelineOrchestrator(
        config=_config_with_secret(),
        app_config=_app_config(),
        audit=FakeAudit(),
        llm=FakeLLM(),
        object_store=FakeObjectStore(),
        run_repo=rr,
    )
    orch.classifier = orch.critic = orch.coverage_checker = None
    orch.indexer = FakeIndexer()

    orch._stage_save(_ctx_for_save(orch, [_managed_task("A")]))

    # The secret must NOT appear anywhere in what was handed to the repo.
    blob = json.dumps(rr.records, default=str)
    assert "sk-SUPER-SECRET-abc123" not in blob
    # But the non-secret provider identity is preserved.
    assert "openai" in blob and "gpt-4o" in blob


def test_sanitized_config_dict_strips_api_key():
    orch = PipelineOrchestrator(
        config=_config_with_secret(),
        app_config=_app_config(),
        audit=FakeAudit(),
        llm=FakeLLM(),
        object_store=FakeObjectStore(),
    )
    sanitized = orch._sanitized_config_dict()
    assert sanitized["provider_config"]["api_key"] == ""
    assert sanitized["provider_config"]["provider"] == "openai"
    # Full serialization contains no plaintext secret.
    assert "sk-SUPER-SECRET-abc123" not in json.dumps(sanitized, default=str)
