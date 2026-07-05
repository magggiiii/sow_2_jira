"""Offline tests for scripts/migrate_fs_to_pg.py — the one-shot filesystem→DB
migrator (STREAM C).

These prove the migrator's behaviour WITHOUT a real Postgres: the run/task/
credential repositories are injected as in-memory async FAKES that mirror the
signatures of the concrete adapters in ``integrations/repositories.py``
(``create(user_id, run_id, data)`` / ``add(user_id, run_id, task)`` /
``set(user_id, key, value)``).

Gates pinned here:

1. A fabricated fs session (tmp dir with a ``pipeline_output.json`` holding N
   tasks) migrates to exactly 1 run + N tasks. ``run.task_count == N`` and each
   ``Task.id`` is preserved byte-identically from ``ManagedTask.id``. Coverage
   is reproduced the same way the app does (from ``coverage_report.coverage_pct``).
2. Settings secrets are re-encrypted: a fabricated settings blob with an
   ``api_key`` lands in the credential repo as CIPHERTEXT that does NOT contain
   the plaintext (APP_ENC_KEY monkeypatched like tests/test_crypto.py).
3. Importing the module is clean offline and opens NO DB connection.

Everything is in-memory — no Postgres, no network, no ``.keyfile`` writes.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import uuid

import pytest
from cryptography.fernet import Fernet

from config import crypto
from models.schemas import ManagedTask, TaskStatus

import scripts.migrate_fs_to_pg as migrator


# ── crypto env (same mechanism as tests/test_crypto.py) ───────────────────────


@pytest.fixture(autouse=True)
def _crypto_env(monkeypatch):
    for var in ("APP_ENC_KEY", "SOW_FERNET_KEY", "APP_ENC_KEY_OLD"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("APP_ENC_KEY", Fernet.generate_key().decode())
    crypto.reset_fernet_cache()
    yield
    crypto.reset_fernet_cache()


# ── in-memory async fakes (mirror integrations/repositories.py signatures) ─────


class FakeRunRepo:
    def __init__(self):
        self.runs: dict[tuple[str, str], dict] = {}

    async def create(self, user_id: str, run_id: str, data):
        row = {"id": run_id, "user_id": user_id, **dict(data or {})}
        self.runs[(user_id, run_id)] = row
        return row


class FakeTaskRepo:
    def __init__(self):
        self.tasks: list[tuple[str, str, ManagedTask]] = []

    async def add(self, user_id: str, run_id: str, task: ManagedTask):
        self.tasks.append((user_id, run_id, task))
        return task


class FakeCredentialRepo:
    def __init__(self):
        # key -> the value dict handed to set(); we capture what got stored so
        # the test can assert the secret was encrypted before it landed here.
        self.stored: dict[tuple[str, str], dict] = {}

    async def set(self, user_id: str, key: str, value) -> None:
        # Emulate the real CredentialRepository: encrypt the plaintext secret at
        # rest via config.crypto, and NEVER keep the plaintext around.
        value = dict(value or {})
        secret = value.pop("secret", None)
        if secret is not None:
            value["secret_ciphertext"] = crypto.encrypt_secret(str(secret)).encode(
                "utf-8"
            )
        self.stored[(user_id, key)] = value


# ── fs fabrication helpers ────────────────────────────────────────────────────


def _make_task(title: str) -> ManagedTask:
    return ManagedTask(
        id=uuid.uuid4(),
        title=title,
        short_description=f"desc for {title}",
        confidence=0.9,
        status=TaskStatus.CLOSED,
    )


def _write_session(fs_root, run_id: str, tasks: list[ManagedTask], coverage_pct=0.87):
    session_dir = fs_root / "sessions" / run_id
    session_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run_id,
        "config": {"sow_pdf_path": "x.pdf", "jira_project_key": "PROJ"},
        "tasks": [t.model_dump(mode="json") for t in tasks],
        "coverage_report": {"coverage_pct": coverage_pct},
    }
    (session_dir / "pipeline_output.json").write_text(json.dumps(payload))
    (session_dir / "metadata.json").write_text(
        json.dumps({"run_id": run_id, "filename": "x.pdf", "llm_mode": "api"})
    )
    return session_dir


# ── GATE 1: run + N tasks, task_count parity, id preserved, coverage ──────────


def test_migrates_run_and_tasks_with_id_and_count_parity(tmp_path):
    run_id = str(uuid.uuid4())
    tasks = [_make_task("Alpha"), _make_task("Beta"), _make_task("Gamma")]
    expected_ids = [str(t.id) for t in tasks]
    _write_session(tmp_path, run_id, tasks, coverage_pct=0.87)

    run_repo = FakeRunRepo()
    task_repo = FakeTaskRepo()
    cred_repo = FakeCredentialRepo()
    user_id = str(uuid.uuid4())

    asyncio.run(
        migrator.migrate(
            tmp_path,
            run_repo,
            task_repo,
            cred_repo,
            user_id=user_id,
        )
    )

    # Exactly one run written, under the migrating user.
    assert len(run_repo.runs) == 1
    run_row = run_repo.runs[(user_id, run_id)]
    assert run_row["task_count"] == len(tasks) == 3
    # Coverage reproduced the same way the app does (coverage_report.coverage_pct).
    assert run_row["coverage_pct"] == pytest.approx(0.87)

    # Exactly N tasks written, all under the same (user, run), ids byte-identical.
    assert len(task_repo.tasks) == 3
    written_ids = [str(t.id) for (u, r, t) in task_repo.tasks]
    assert written_ids == expected_ids
    for (u, r, t) in task_repo.tasks:
        assert u == user_id
        assert r == run_id
        assert isinstance(t, ManagedTask)


def test_migrates_multiple_sessions(tmp_path):
    run_a, run_b = str(uuid.uuid4()), str(uuid.uuid4())
    _write_session(tmp_path, run_a, [_make_task("A1"), _make_task("A2")])
    _write_session(tmp_path, run_b, [_make_task("B1")])

    run_repo, task_repo, cred_repo = FakeRunRepo(), FakeTaskRepo(), FakeCredentialRepo()
    user_id = str(uuid.uuid4())

    asyncio.run(
        migrator.migrate(tmp_path, run_repo, task_repo, cred_repo, user_id=user_id)
    )

    assert len(run_repo.runs) == 2
    assert run_repo.runs[(user_id, run_a)]["task_count"] == 2
    assert run_repo.runs[(user_id, run_b)]["task_count"] == 1
    assert len(task_repo.tasks) == 3


def test_no_sessions_dir_is_a_noop(tmp_path):
    """A fs root with no sessions/ dir migrates nothing (and never crashes)."""
    run_repo, task_repo, cred_repo = FakeRunRepo(), FakeTaskRepo(), FakeCredentialRepo()
    asyncio.run(
        migrator.migrate(
            tmp_path, run_repo, task_repo, cred_repo, user_id=str(uuid.uuid4())
        )
    )
    assert run_repo.runs == {}
    assert task_repo.tasks == []


# ── GATE 2: settings secrets re-encrypted (no plaintext at rest) ──────────────


def test_settings_secrets_reencrypted_never_plaintext(tmp_path):
    api_key_plaintext = "sk-super-secret-key-9000"
    jira_token_plaintext = "jira-token-abc-123"

    # A fabricated settings blob. The on-disk secrets are provided already-decrypted
    # here (an injected identity decryptor stands in for SettingsManager so the
    # test needs no .keyfile); the migrator must RE-encrypt them under APP_ENC_KEY.
    settings_blob = {
        "provider": "openai",
        "providers": {
            "openai": {
                "model": "gpt-4o",
                "api_key": api_key_plaintext,
                "base_url": "https://api.openai.com/v1",
            }
        },
        "jira_server_url": "https://x.atlassian.net",
        "jira_api_token": jira_token_plaintext,
    }
    (tmp_path / "settings.json").write_text(json.dumps(settings_blob))

    run_repo, task_repo, cred_repo = FakeRunRepo(), FakeTaskRepo(), FakeCredentialRepo()
    user_id = str(uuid.uuid4())

    asyncio.run(
        migrator.migrate(
            tmp_path,
            run_repo,
            task_repo,
            cred_repo,
            user_id=user_id,
            decrypt_secret=lambda s: s,  # identity: fabricated blob is plaintext
        )
    )

    # The LLM credential landed, encrypted at rest.
    llm = cred_repo.stored[(user_id, "llm")]
    ct = llm["secret_ciphertext"]
    assert isinstance(ct, (bytes, bytearray))
    assert api_key_plaintext.encode("utf-8") not in bytes(ct)
    assert api_key_plaintext not in bytes(ct).decode("latin-1")
    # No plaintext secret key survived into the stored value.
    assert "secret" not in llm
    assert "api_key" not in llm
    # Non-secret config is preserved.
    assert llm["config"]["model"] == "gpt-4o"
    assert llm["provider"] == "openai"
    # And it round-trips back to the original plaintext under APP_ENC_KEY.
    assert crypto.decrypt_secret(bytes(ct).decode("utf-8")) == api_key_plaintext

    # The Jira credential landed, encrypted at rest.
    jira = cred_repo.stored[(user_id, "jira")]
    jct = jira["secret_ciphertext"]
    assert jira_token_plaintext.encode("utf-8") not in bytes(jct)
    assert crypto.decrypt_secret(bytes(jct).decode("utf-8")) == jira_token_plaintext
    assert jira["config"]["jira_server"] == "https://x.atlassian.net"


def test_settings_without_secrets_stores_no_ciphertext(tmp_path):
    """A settings blob with empty/absent secrets stores no ciphertext (and never
    a plaintext secret)."""
    (tmp_path / "settings.json").write_text(
        json.dumps(
            {
                "provider": "ollama",
                "providers": {"ollama": {"model": "llama3", "api_key": ""}},
            }
        )
    )
    run_repo, task_repo, cred_repo = FakeRunRepo(), FakeTaskRepo(), FakeCredentialRepo()
    user_id = str(uuid.uuid4())
    asyncio.run(
        migrator.migrate(
            tmp_path, run_repo, task_repo, cred_repo, user_id=user_id,
            decrypt_secret=lambda s: s,
        )
    )
    llm = cred_repo.stored.get((user_id, "llm"))
    assert llm is not None
    assert "secret_ciphertext" not in llm
    assert "secret" not in llm and "api_key" not in llm


# ── GATE 3: import is clean offline and opens no DB connection ────────────────


def test_import_is_clean_and_connection_free(monkeypatch):
    """Re-importing the module must not touch the network / open a DB engine.

    We poison ``create_async_engine`` so that ANY attempt to open a connection at
    import time blows up; a clean re-import proves import-time is connection-free.
    """
    import sqlalchemy.ext.asyncio as sa_async

    def _boom(*a, **k):  # pragma: no cover - only fires on a regression
        raise AssertionError("import-time opened a DB engine/connection")

    monkeypatch.setattr(sa_async, "create_async_engine", _boom)
    mod = importlib.reload(importlib.import_module("scripts.migrate_fs_to_pg"))
    assert hasattr(mod, "migrate")
    assert callable(mod.migrate)
