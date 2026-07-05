# core/ports.py
"""
Protocol ports for the strangler-fig hexagonal seam.

These ``typing.Protocol`` classes describe the *minimal* surfaces the core
harness depends on, decoupling the new pipeline from the concrete adapters in
``pipeline/``, ``integrations/``, and ``audit/``. Each port mirrors the real
method signatures of an existing implementation, so those classes
*structurally* satisfy the protocol with no inheritance or registration:

    LLMProvider    ← pipeline.llm_client.LLMClient
    JiraGateway    ← integrations.jira_client.JiraClient
    AuditSink      ← audit.logger.AuditLogger
    EmbeddingIndex ← pipeline.agents.cross_run_index.ProjectEmbeddingIndex

All ports are ``@runtime_checkable`` so tests (and, where useful, runtime
composition code) can assert ``isinstance(obj, Port)``. Note that
``runtime_checkable`` only verifies method *presence*, not signatures — the
docstrings here are the contract for argument shapes.

``ObjectStore`` and the three repository ports (``RunRepository``,
``TaskRepository``, ``CredentialRepository``) are FORWARD-LOOKING: there is no
concrete implementation yet. They are defined here so the composition root and
future adapters (local filesystem / R2 / SQLite) have a stable target. Do not
assume any class satisfies them today.

This module is additive and dependency-free beyond ``typing`` plus the
existing domain schemas — importing it must not pull in litellm, jira, or
sentence-transformers.
"""

from __future__ import annotations

from typing import (
    TYPE_CHECKING,
    Any,
    Optional,
    Protocol,
    Union,
    runtime_checkable,
)

if TYPE_CHECKING:
    # Import only for static typing; avoids a hard runtime dependency on the
    # domain schema module from this low-level port definition file. The
    # Protocols themselves use string/forward annotations so they remain
    # importable even if these symbols move.
    from models.schemas import JiraPushResult, ManagedTask


# ─── LLM port ──────────────────────────────────────────────────────────────


@runtime_checkable
class LLMProvider(Protocol):
    """
    The LLM surface the core depends on. Mirrors
    ``pipeline.llm_client.LLMClient`` so that an existing ``LLMClient``
    instance structurally satisfies this protocol.

    Only the two completion entrypoints agents actually call are part of the
    contract; retry/telemetry internals are deliberately excluded.
    """

    def complete(
        self,
        prompt: str,
        system: str = "You are a helpful assistant.",
        temperature: float = 0.0,
        max_tokens: int = 4096,
        agent_name: str = "unknown",
        node_id: str = "",
    ) -> str:
        """Send a completion request and return the raw response text."""
        ...

    def complete_json(
        self,
        prompt: str,
        system: str = "You are a precise JSON extraction assistant.",
        agent_name: str = "unknown",
        node_id: str = "",
        max_tokens: int = 8192,
    ) -> Union[list, dict]:
        """Like ``complete`` but parse and return JSON (list or dict)."""
        ...


# ─── Jira port ───────────────────────────────────────────────────────────────


@runtime_checkable
class JiraGateway(Protocol):
    """
    The Jira push surface. Mirrors the public entrypoint of
    ``integrations.jira_client.JiraClient.push_tasks`` (and the equivalent on
    the MCP client) so either concrete client structurally satisfies it.
    """

    def push_tasks(self, tasks: "list[ManagedTask]") -> "list[JiraPushResult]":
        """Push approved tasks to Jira and return per-task push results."""
        ...


# ─── Audit port ──────────────────────────────────────────────────────────────


@runtime_checkable
class AuditSink(Protocol):
    """
    The append-only audit surface. Mirrors ``audit.logger.AuditLogger.log``
    exactly so an ``AuditLogger`` instance structurally satisfies it.
    """

    def log(
        self,
        run_id: str,
        agent: str,
        action: str,
        detail: str,
        node_id: Optional[str] = None,
        task_id: Optional[str] = None,
        llm_tokens_used: int = 0,
        llm_model: str = "",
    ) -> Any:
        """Append one audit record for a run."""
        ...


# ─── Embedding index port ────────────────────────────────────────────────────


@runtime_checkable
class EmbeddingIndex(Protocol):
    """
    The project-scoped embedding store surface used by the dedup agent.
    Mirrors ``pipeline.agents.cross_run_index.ProjectEmbeddingIndex`` usage
    (``add_run`` / ``search``) so that class structurally satisfies it.

    Embeddings are numpy arrays; they are typed ``Any`` here to keep this port
    free of a hard numpy import.
    """

    def add_run(
        self,
        run_id: str,
        tasks: "list[ManagedTask]",
        embeddings: Any,
    ) -> None:
        """Append a run's tasks and their embeddings to the index."""
        ...

    def search(
        self,
        embeddings: Any,
        task_ids: list[str],
        threshold: float = 0.85,
        exclude_run_id: Optional[str] = None,
    ) -> list:
        """Return prior-run matches for the query embeddings, above threshold."""
        ...


# ─── Object store port (forward-looking) ─────────────────────────────────────


@runtime_checkable
class ObjectStore(Protocol):
    """
    Forward-looking blob storage surface for run artifacts (pipeline output,
    embeddings, node index, uploads).

    NOT YET IMPLEMENTED. Intended to back a local-filesystem adapter first and
    a Cloudflare R2 / S3-compatible adapter later, behind one interface. Keys
    are opaque strings (e.g. ``sessions/<run_id>/pipeline_output.json``).
    """

    def put(self, key: str, data: bytes) -> str:
        """Store ``data`` under ``key``; return a locator (path or URL)."""
        ...

    def get(self, key: str) -> bytes:
        """Fetch the bytes stored under ``key``."""
        ...

    def exists(self, key: str) -> bool:
        """Return True if an object exists at ``key``."""
        ...


# ─── Repository ports (forward-looking) ──────────────────────────────────────
#
# These describe the persistence seam the harness will eventually use instead
# of reading/writing data/sessions/<run_id>/*.json and data/settings.json
# directly. They are CRUD-ish stubs only — NOT YET IMPLEMENTED, and no concrete
# class satisfies them today. They exist so the composition root can name a
# stable target and so future SQLite / Postgres adapters have a signature to
# match. Argument and return types are intentionally loose (Any) until the
# storage decision is made.


# ─── Auth ports (offline authentication core) ─────────────────────────────────
#
# These describe the auth seam used by the ``current_user`` FastAPI dependency
# and the session store. They mirror the shape of ``pipeline.db.User`` and
# ``pipeline.db.Session`` so a future DB-backed ``SessionStore`` satisfies the
# SAME Protocol as the in-memory ``auth.store.FakeSessionStore``. Session tokens
# are opaque; only ``sha256(raw cookie)`` is ever persisted (see
# ``pipeline.db.Session.token_hash``).


@runtime_checkable
class User(Protocol):
    """
    The authenticated principal. Mirrors ``pipeline.db.User`` (id / email /
    is_active) so an ORM ``User`` row structurally satisfies this protocol.
    """

    id: str
    email: str
    is_active: bool


@runtime_checkable
class Session(Protocol):
    """
    A server-side opaque session. Mirrors ``pipeline.db.Session`` (id /
    user_id / expires_at) so an ORM ``Session`` row structurally satisfies it.
    ``expires_at`` is a timezone-aware datetime.
    """

    id: str
    user_id: str
    expires_at: Any


@runtime_checkable
class SessionStore(Protocol):
    """
    The session persistence surface behind ``current_user``. The in-memory
    ``auth.store.FakeSessionStore`` satisfies this today; a DB-backed store
    (over ``pipeline.db.Session``) will satisfy the same shape later.

    The raw cookie token is passed in/out but never stored: implementations
    persist only ``sha256(raw)`` and compare in constant time.
    """

    def create_session(self, user_id: str) -> "tuple[str, Session]":
        """Mint a new session for ``user_id``; return ``(raw_token, session)``."""
        ...

    def get_session(self, raw_token: str) -> "Optional[Session]":
        """Return the live session for ``raw_token``, or None if unknown/expired."""
        ...

    def get_user(self, user_id: str) -> "Optional[User]":
        """Return the ``User`` for ``user_id``, or None if absent.

        Part of the contract because ``current_user`` (``auth.deps``) resolves
        the principal via ``store.get_user(session.user_id)`` after loading the
        session. A DB-backed store must implement this alongside the session
        methods, or isinstance() would give false confidence for the seam.
        """
        ...

    def delete_session(self, raw_token: str) -> None:
        """Revoke the session identified by ``raw_token`` (idempotent)."""
        ...


@runtime_checkable
class RunRepository(Protocol):
    """
    Forward-looking persistence for run records (metadata + status +
    checkpointed output). NOT YET IMPLEMENTED.
    """

    def create(self, run_id: str, data: Any) -> Any:
        """Persist a new run record keyed by ``run_id``."""
        ...

    def get(self, run_id: str) -> Optional[Any]:
        """Fetch a run record by id, or None if absent."""
        ...

    def update(self, run_id: str, data: Any) -> Any:
        """Update an existing run record."""
        ...

    def list(self) -> list:
        """List all known run records."""
        ...


@runtime_checkable
class TaskRepository(Protocol):
    """
    Forward-looking persistence for managed tasks within a run. NOT YET
    IMPLEMENTED.
    """

    def add(self, run_id: str, task: Any) -> Any:
        """Persist a task under a run."""
        ...

    def get(self, run_id: str, task_id: str) -> Optional[Any]:
        """Fetch a single task by id within a run, or None."""
        ...

    def list_for_run(self, run_id: str) -> list:
        """List all tasks belonging to a run."""
        ...

    def update(self, run_id: str, task_id: str, data: Any) -> Any:
        """Update a stored task."""
        ...


@runtime_checkable
class CredentialRepository(Protocol):
    """
    Forward-looking persistence for encrypted-at-rest credentials and provider
    settings (today this lives in the Fernet-encrypted data/settings.json).
    NOT YET IMPLEMENTED. Implementations MUST keep secrets encrypted at rest —
    no plaintext fallback.
    """

    def get(self, key: str) -> Optional[Any]:
        """Fetch a credential/setting by key, or None."""
        ...

    def set(self, key: str, value: Any) -> None:
        """Store a credential/setting (encrypted at rest)."""
        ...

    def delete(self, key: str) -> None:
        """Remove a stored credential/setting."""
        ...


__all__ = [
    "LLMProvider",
    "JiraGateway",
    "AuditSink",
    "ObjectStore",
    "EmbeddingIndex",
    "User",
    "Session",
    "SessionStore",
    "RunRepository",
    "TaskRepository",
    "CredentialRepository",
]
