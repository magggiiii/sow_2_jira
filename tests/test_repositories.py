"""Tests for the concrete SQLAlchemy-backed repositories (STREAM DB-REPOS).

These prove the three repos in ``integrations/repositories.py`` structurally
satisfy the Protocols in ``core/ports.py`` (RunRepository / TaskRepository /
CredentialRepository) and enforce the two invariants that matter for the
multi-tenant pivot:

1. TENANT ISOLATION — every query is scoped by ``user_id``; tenant B never sees
   tenant A's rows (get/list return None/[] across tenants).
2. ENCRYPTED AT REST — credential secrets are Fernet-encrypted into
   ``UserCredential.secret_ciphertext`` (BYTEA); the stored bytes never contain
   the plaintext; ``get()`` round-trips the decrypted secret. No plaintext
   fallback.

Everything runs on ``sqlite+aiosqlite:///:memory:`` — no Postgres, no network.
Task.id is asserted BYTE-IDENTICAL to the ManagedTask.id passed in (never
regenerated), matching the ORM contract in pipeline/db.py.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from config import crypto
from core import ports
from integrations.repositories import (
    CredentialRepository,
    RunRepository,
    TaskRepository,
)
from models.schemas import ManagedTask, TaskStatus
from pipeline.db import Base, User, UserCredential


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def sessionmaker_and_users():
    """Fresh in-memory sqlite DB with two tenant users (A and B) inserted.

    Yields ``(sessionmaker, user_a_id, user_b_id)``.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    sm = async_sessionmaker(engine, expire_on_commit=False)
    user_a = str(uuid.uuid4())
    user_b = str(uuid.uuid4())
    async with sm() as sess:
        sess.add(User(id=user_a, email="a@calibraint.com", google_sub="sub-a"))
        sess.add(User(id=user_b, email="b@calibraint.com", google_sub="sub-b"))
        await sess.commit()

    yield sm, user_a, user_b

    await engine.dispose()


@pytest.fixture(autouse=True)
def _crypto_env(monkeypatch):
    """Set a known APP_ENC_KEY (same mechanism as tests/test_crypto.py)."""
    for var in ("APP_ENC_KEY", "SOW_FERNET_KEY", "APP_ENC_KEY_OLD"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("APP_ENC_KEY", Fernet.generate_key().decode())
    crypto.reset_fernet_cache()
    yield
    crypto.reset_fernet_cache()


def _run_data(filename="sow.pdf", status="queued", **extra):
    """A minimal run payload (dict) accepted by RunRepository.create."""
    data = {"filename": filename, "status": status, "config": {"k": "v"}}
    data.update(extra)
    return data


# ── GATE 1: structural satisfaction (isinstance vs core.ports Protocols) ──────


@pytest.mark.asyncio
async def test_repos_satisfy_ports_protocols(sessionmaker_and_users):
    sm, _user_a, _user_b = sessionmaker_and_users
    run_repo = RunRepository(sm)
    task_repo = TaskRepository(sm)
    cred_repo = CredentialRepository(sm)

    assert isinstance(run_repo, ports.RunRepository)
    assert isinstance(task_repo, ports.TaskRepository)
    assert isinstance(cred_repo, ports.CredentialRepository)


# ── GATE 2: RunRepository CRUD + tenant-scoped list ──────────────────────────


@pytest.mark.asyncio
async def test_run_repository_create_get_list_update(sessionmaker_and_users):
    sm, user_a, user_b = sessionmaker_and_users
    repo = RunRepository(sm)
    run_id = str(uuid.uuid4())

    created = await repo.create(user_a, run_id, _run_data(status="queued"))
    assert created is not None

    got = await repo.get(user_a, run_id)
    assert got is not None
    assert got["id"] == run_id
    assert got["status"] == "queued"
    assert got["filename"] == "sow.pdf"
    assert got["config"] == {"k": "v"}

    # list scoped to user A includes it; user B's list does NOT.
    ids_a = [r["id"] for r in await repo.list(user_a)]
    ids_b = [r["id"] for r in await repo.list(user_b)]
    assert run_id in ids_a
    assert run_id not in ids_b

    # update persists a status change.
    await repo.update(user_a, run_id, {"status": "completed", "progress": 1.0})
    reread = await repo.get(user_a, run_id)
    assert reread["status"] == "completed"
    assert reread["progress"] == 1.0


# ── GATE 3: TaskRepository add/get/list with BYTE-IDENTICAL id parity ─────────


@pytest.mark.asyncio
async def test_task_repository_id_parity_and_roundtrip(sessionmaker_and_users):
    sm, user_a, _user_b = sessionmaker_and_users
    run_repo = RunRepository(sm)
    task_repo = TaskRepository(sm)

    run_id = str(uuid.uuid4())
    await run_repo.create(user_a, run_id, _run_data())

    task = ManagedTask(
        id=uuid.UUID("3f2504e0-4f89-41d3-9a0c-0305e82c3301"),
        title="Build the widget",
        short_description="A widget that does things",
        confidence=0.87,
        status=TaskStatus.CLOSED,
    )
    expected_id = str(task.id)

    await task_repo.add(user_a, run_id, task)

    got = await task_repo.get(user_a, run_id, expected_id)
    assert got is not None
    # BYTE-IDENTICAL parity with ManagedTask.id — never regenerated.
    assert str(got.id) == expected_id
    assert got.title == "Build the widget"
    assert got.confidence == pytest.approx(0.87)
    assert got.status == TaskStatus.CLOSED

    listed = await task_repo.list_for_run(user_a, run_id)
    assert [str(t.id) for t in listed] == [expected_id]

    # update persists a status change and round-trips as a ManagedTask.
    await task_repo.update(
        user_a, run_id, expected_id, {"status": TaskStatus.APPROVED}
    )
    reread = await task_repo.get(user_a, run_id, expected_id)
    assert reread.status == TaskStatus.APPROVED
    assert str(reread.id) == expected_id


# ── GATE 4: explicit tenant isolation on runs AND tasks ──────────────────────


@pytest.mark.asyncio
async def test_tenant_isolation_runs_and_tasks(sessionmaker_and_users):
    sm, user_a, user_b = sessionmaker_and_users
    run_repo = RunRepository(sm)
    task_repo = TaskRepository(sm)

    run_id = str(uuid.uuid4())
    await run_repo.create(user_a, run_id, _run_data())

    task = ManagedTask(
        id=uuid.uuid4(),
        title="A's private task",
        short_description="secret",
        confidence=0.5,
    )
    task_id = str(task.id)
    await task_repo.add(user_a, run_id, task)

    # User B must NOT be able to read A's run or task by id.
    assert await run_repo.get(user_b, run_id) is None
    assert await task_repo.get(user_b, run_id, task_id) is None
    # Nor list them under B's tenant.
    assert await run_repo.list(user_b) == []
    assert await task_repo.list_for_run(user_b, run_id) == []

    # User B's update on A's run/task must not mutate A's row (returns None /
    # no-op) — A's data is unchanged.
    await run_repo.update(user_b, run_id, {"status": "hijacked"})
    assert (await run_repo.get(user_a, run_id))["status"] == "queued"

    await task_repo.update(user_b, run_id, task_id, {"status": TaskStatus.REJECTED})
    assert (await task_repo.get(user_a, run_id, task_id)).status != TaskStatus.REJECTED


# ── GATE 5: CredentialRepository — encrypted at rest + round-trip + delete ────


@pytest.mark.asyncio
async def test_credential_repository_encrypted_at_rest(sessionmaker_and_users):
    sm, user_a, user_b = sessionmaker_and_users
    repo = CredentialRepository(sm)

    plaintext_secret = "super-secret-jira-token-42"
    value = {
        "kind": "jira",
        "provider": "atlassian",
        "config": {"jira_server": "https://x.atlassian.net", "jira_email": "a@x"},
        "secret": plaintext_secret,
    }

    await repo.set(user_a, "jira", value)

    # Inspect the raw row: secret_ciphertext must NOT contain the plaintext.
    async with sm() as sess:
        row = (
            await sess.execute(
                select(UserCredential).where(UserCredential.user_id == user_a)
            )
        ).scalar_one()
        assert row.secret_ciphertext is not None
        assert isinstance(row.secret_ciphertext, (bytes, bytearray))
        assert plaintext_secret.encode("utf-8") not in bytes(row.secret_ciphertext)
        assert plaintext_secret not in bytes(row.secret_ciphertext).decode(
            "latin-1"
        )

    # get() round-trips the decrypted secret + non-secret config.
    got = await repo.get(user_a, "jira")
    assert got is not None
    assert got["secret"] == plaintext_secret
    assert got["provider"] == "atlassian"
    assert got["config"]["jira_server"] == "https://x.atlassian.net"

    # Tenant isolation: user B cannot read A's credential.
    assert await repo.get(user_b, "jira") is None

    # delete() removes it.
    await repo.delete(user_a, "jira")
    assert await repo.get(user_a, "jira") is None
