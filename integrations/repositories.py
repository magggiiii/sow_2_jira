"""Concrete SQLAlchemy-backed repositories (STREAM DB-REPOS).

Async, tenant-scoped adapters that persist the domain to the 7-table ORM in
``pipeline.db`` and structurally satisfy the Protocols in ``core/ports.py``
(``RunRepository`` / ``TaskRepository`` / ``CredentialRepository``).

Design invariants:

* **Tenant isolation (row-level).** Every query filters on ``user_id``. A tenant
  can only read/update/delete its OWN rows — a cross-tenant ``get``/``list``
  returns ``None``/``[]`` and a cross-tenant ``update`` is a no-op. ``user_id``
  is therefore the FIRST argument of every method (the ``core.ports`` Protocols
  are ``@runtime_checkable`` and only verify method *presence*, so these
  richer signatures still satisfy ``isinstance``).

* **Task.id parity.** ``Task.id`` is set to ``str(ManagedTask.id)`` and never
  regenerated (see ``core.domain.repositories.task_to_row``).

* **Encrypted at rest.** ``CredentialRepository`` encrypts the secret via
  ``config.crypto.encrypt_secret`` into ``UserCredential.secret_ciphertext``
  (BYTEA). The stored ciphertext never contains the plaintext; ``get()``
  decrypts. There is NO plaintext fallback.

Each repo takes an ``async_sessionmaker`` (or, for convenience, an
``AsyncSession``) so tests can inject a ``sqlite+aiosqlite`` engine. Nothing here
opens a connection at construction time.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Optional, Union
from uuid import uuid4

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from config.crypto import decrypt_secret, encrypt_secret
from core.domain.repositories import (
    apply_run_data,
    apply_task_data,
    new_run_row,
    row_to_task,
    run_row_to_dict,
    task_to_row,
)
from models.schemas import ManagedTask
from pipeline.db import Run, Task, UserCredential

__all__ = [
    "RunRepository",
    "TaskRepository",
    "CredentialRepository",
]

_SessionSource = Union[async_sessionmaker, AsyncSession]


class _SessionMixin:
    """Shared session handling for the repos.

    Accepts either an ``async_sessionmaker`` (a new session per operation,
    committed at the end) or a bare ``AsyncSession`` (reused, caller controls the
    transaction). This keeps the repos usable both in request handlers (inject a
    sessionmaker) and inside an existing unit-of-work (inject a session).
    """

    def __init__(self, session_source: _SessionSource):
        self._session_source = session_source

    @asynccontextmanager
    async def _session(self):
        source = self._session_source
        if isinstance(source, AsyncSession):
            # Caller owns the transaction; do not commit/close here.
            yield source
        else:
            async with source() as session:
                yield session
                await session.commit()


class RunRepository(_SessionMixin):
    """Tenant-scoped persistence for :class:`pipeline.db.Run` rows."""

    async def create(self, user_id: str, run_id: str, data: Any) -> dict:
        async with self._session() as session:
            row = new_run_row(user_id, run_id, dict(data or {}))
            session.add(row)
            await session.flush()
            return run_row_to_dict(row)

    async def get(self, user_id: str, run_id: str) -> Optional[dict]:
        async with self._session() as session:
            row = await self._fetch(session, user_id, run_id)
            return run_row_to_dict(row) if row is not None else None

    async def update(self, user_id: str, run_id: str, data: Any) -> Optional[dict]:
        async with self._session() as session:
            row = await self._fetch(session, user_id, run_id)
            if row is None:
                # Cross-tenant / unknown run: no-op (never touch another row).
                return None
            apply_run_data(row, dict(data or {}))
            await session.flush()
            return run_row_to_dict(row)

    async def list(self, user_id: str) -> list[dict]:
        async with self._session() as session:
            result = await session.execute(
                select(Run)
                .where(Run.user_id == user_id)
                .order_by(Run.created_at.desc())
            )
            return [run_row_to_dict(r) for r in result.scalars().all()]

    @staticmethod
    async def _fetch(
        session: AsyncSession, user_id: str, run_id: str
    ) -> Optional[Run]:
        result = await session.execute(
            select(Run).where(Run.id == run_id, Run.user_id == user_id)
        )
        return result.scalar_one_or_none()


class TaskRepository(_SessionMixin):
    """Tenant-scoped persistence for :class:`pipeline.db.Task` rows."""

    async def add(self, user_id: str, run_id: str, task: ManagedTask) -> ManagedTask:
        async with self._session() as session:
            row = task_to_row(user_id, run_id, task)
            session.add(row)
            await session.flush()
            return row_to_task(row)

    async def get(
        self, user_id: str, run_id: str, task_id: str
    ) -> Optional[ManagedTask]:
        async with self._session() as session:
            row = await self._fetch(session, user_id, run_id, task_id)
            return row_to_task(row) if row is not None else None

    async def list_for_run(self, user_id: str, run_id: str) -> list[ManagedTask]:
        async with self._session() as session:
            result = await session.execute(
                select(Task)
                .where(Task.run_id == run_id, Task.user_id == user_id)
                .order_by(Task.created_at)
            )
            return [row_to_task(r) for r in result.scalars().all()]

    async def update(
        self, user_id: str, run_id: str, task_id: str, data: Any
    ) -> Optional[ManagedTask]:
        async with self._session() as session:
            row = await self._fetch(session, user_id, run_id, task_id)
            if row is None:
                # Cross-tenant / unknown task: no-op.
                return None
            apply_task_data(row, dict(data or {}))
            await session.flush()
            return row_to_task(row)

    @staticmethod
    async def _fetch(
        session: AsyncSession, user_id: str, run_id: str, task_id: str
    ) -> Optional[Task]:
        result = await session.execute(
            select(Task).where(
                Task.id == task_id,
                Task.run_id == run_id,
                Task.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()


class CredentialRepository(_SessionMixin):
    """Tenant-scoped, encrypted-at-rest credential store.

    Keyed by ``(user_id, kind, provider)`` — matching the unique constraint on
    :class:`pipeline.db.UserCredential`. The secret is Fernet-encrypted into
    ``secret_ciphertext`` (BYTEA); non-secret settings live in ``config``.
    """

    async def get(self, user_id: str, key: str) -> Optional[dict]:
        provider = self._provider(key)
        async with self._session() as session:
            row = await self._fetch(session, user_id, key, provider)
            if row is None:
                return None
            out: dict[str, Any] = {
                "kind": row.kind,
                "provider": row.provider,
                "config": dict(row.config or {}),
            }
            if row.secret_ciphertext is not None:
                token = bytes(row.secret_ciphertext).decode("utf-8")
                out["secret"] = decrypt_secret(token)
            return out

    async def set(self, user_id: str, key: str, value: Any) -> None:
        value = dict(value or {})
        kind = value.get("kind", key)
        provider = value.get("provider")
        config = value.get("config") or {}
        secret = value.get("secret")

        ciphertext: Optional[bytes] = None
        if secret is not None:
            # Encrypt at rest — the plaintext never touches the column.
            ciphertext = encrypt_secret(str(secret)).encode("utf-8")

        async with self._session() as session:
            row = await self._fetch(session, user_id, key, provider, kind=kind)
            if row is None:
                row = UserCredential(
                    # Client-supplied PK: the ORM's server_default is
                    # gen_random_uuid() (Postgres-only); supply a UUID str so
                    # the row also inserts on SQLite without that function.
                    id=str(uuid4()),
                    user_id=user_id,
                    kind=kind,
                    provider=provider,
                    config=config,
                    secret_ciphertext=ciphertext,
                )
                session.add(row)
            else:
                row.config = config
                if ciphertext is not None:
                    row.secret_ciphertext = ciphertext
            await session.flush()

    async def delete(self, user_id: str, key: str) -> None:
        provider = self._provider(key)
        async with self._session() as session:
            await session.execute(
                sa_delete(UserCredential).where(
                    UserCredential.user_id == user_id,
                    UserCredential.kind == key,
                )
            )
            # Also delete when the key was used as a provider (defensive; the
            # primary lookup is by kind above).
            if provider is not None:
                await session.execute(
                    sa_delete(UserCredential).where(
                        UserCredential.user_id == user_id,
                        UserCredential.provider == provider,
                    )
                )
            await session.flush()

    @staticmethod
    def _provider(key: str) -> Optional[str]:
        # ``key`` is the credential ``kind`` (e.g. 'jira' | 'llm'); no provider
        # is encoded in it here.
        return None

    @staticmethod
    async def _fetch(
        session: AsyncSession,
        user_id: str,
        key: str,
        provider: Optional[str],
        *,
        kind: Optional[str] = None,
    ) -> Optional[UserCredential]:
        lookup_kind = kind or key
        stmt = select(UserCredential).where(
            UserCredential.user_id == user_id,
            UserCredential.kind == lookup_kind,
        )
        if provider is not None:
            stmt = stmt.where(UserCredential.provider == provider)
        result = await session.execute(stmt)
        return result.scalars().first()
