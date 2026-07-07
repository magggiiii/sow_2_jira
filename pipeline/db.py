"""
Async DB engine, session factory, and ORM models (SQLAlchemy 2.0 style).

This is the single source of truth for the relational schema that backs the
hosted/multi-user elevation (change-ids ``infr-6`` + ``doma-1``). It mirrors the
DDL in ``RENDER-MIGRATION.md §3.1``:

    users, sessions, user_credentials, runs, tasks, coverage_reports, audit_log

Every *tenant-owned* table carries a ``user_id`` FK (→ ``users.id``) plus an
index for row-level isolation. JSONB columns round-trip Pydantic payloads via
``model_dump(mode="json")`` / ``model_validate``. Timestamps use TIMESTAMPTZ
(``DateTime(timezone=True)``), encrypted/opaque bytes use BYTEA
(``LargeBinary``), and IP addresses use ``postgresql.INET``.

IMPORTANT: importing this module is side-effect-free and connection-free. No
engine is created and no network I/O happens at import time. Callers build an
engine explicitly via :func:`make_engine` and a session factory via
:func:`get_sessionmaker`. ``Task.id`` is the *same* id as
``models.schemas.ManagedTask.id`` — it is never auto-generated here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON, DateTime, Float

# ── SQLite compatibility shims (sqlite-only; Postgres DDL untouched) ──────────
#
# The ORM below is authored against Postgres types (``INET``, ``JSONB``,
# ``UUID(as_uuid=False)``) so production DDL is unchanged. Offline tests run on
# SQLite, which cannot render those types. We register ``@compiles(..., 'sqlite')``
# hooks so the SAME ORM compiles on SQLite by substituting compatible column
# types. These hooks fire ONLY for the sqlite dialect — the postgresql dialect
# keeps emitting INET/JSONB/gen_random_uuid() verbatim.


@compiles(INET, "sqlite")
def _compile_inet_sqlite(type_, compiler, **kw):  # noqa: ANN001
    """Render ``postgresql.INET`` as ``VARCHAR`` on SQLite."""
    return "VARCHAR"


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(type_, compiler, **kw):  # noqa: ANN001
    """Render ``postgresql.JSONB`` as SQLite's ``JSON`` for DDL."""
    return compiler.visit_JSON(type_, **kw)


@compiles(UUID, "sqlite")
def _compile_uuid_sqlite(type_, compiler, **kw):  # noqa: ANN001
    """Render ``postgresql.UUID`` as ``CHAR(36)`` on SQLite (canonical uuid str)."""
    return "CHAR(36)"


# ``@compiles`` only rewrites DDL type rendering — it does NOT give JSONB the
# JSON (de)serialization behavior on SQLite, so dict/list columns would come
# back as raw strings. We patch JSONB's bind/result processors so that, on the
# sqlite dialect, it delegates to SQLAlchemy's generic ``JSON`` type (json.dumps
# on write, json.loads on read). On any other dialect it defers to the real
# postgresql implementation. This is a pure Python-side shim; it emits no DDL
# and never touches Postgres behavior.
_JSON_SQLITE = JSON()


def _jsonb_bind_processor(self, dialect):  # noqa: ANN001
    if dialect.name == "sqlite":
        return _JSON_SQLITE.bind_processor(dialect)
    return super(JSONB, self).bind_processor(dialect)


def _jsonb_result_processor(self, dialect, coltype):  # noqa: ANN001
    if dialect.name == "sqlite":
        return _JSON_SQLITE.result_processor(dialect, coltype)
    return super(JSONB, self).result_processor(dialect, coltype)


JSONB.bind_processor = _jsonb_bind_processor
JSONB.result_processor = _jsonb_result_processor


# ── URL normalization ────────────────────────────────────────────────────────


def normalize_db_url(url: str) -> str:
    """Return an asyncpg-compatible SQLAlchemy URL.

    Rewrites the *scheme* of a Postgres URL to use the asyncpg driver so
    ``create_async_engine`` can consume it:

    - ``postgresql://...``          -> ``postgresql+asyncpg://...``
    - ``postgres://...``            -> ``postgresql+asyncpg://...``  (Render/Heroku style)
    - ``postgresql+asyncpg://...``  -> unchanged (idempotent)
    - any other scheme              -> unchanged

    This is a pure string transform: it does NOT import asyncpg, validate
    connectivity, or open a connection. Safe to call at import/config time.
    """
    if not url:
        return url

    scheme_sep = "://"
    idx = url.find(scheme_sep)
    if idx == -1:
        return url

    scheme = url[:idx]
    rest = url[idx + len(scheme_sep):]

    # Already asyncpg (with or without an existing driver marker) — leave alone.
    if scheme in ("postgresql+asyncpg", "postgres+asyncpg"):
        return url

    # Base Postgres schemes (no driver, or a different sync driver) → asyncpg.
    if scheme in ("postgresql", "postgres"):
        return f"postgresql+asyncpg{scheme_sep}{rest}"

    # Non-Postgres scheme: untouched.
    return url


def normalize_sync_db_url(url: str) -> str:
    """Return a *synchronous* (psycopg2) SQLAlchemy URL.

    Alembic migrations run against a blocking driver, so we strip any asyncpg
    marker and target ``postgresql+psycopg2``. Pure string transform; opens no
    connection. Non-Postgres schemes pass through untouched.
    """
    if not url:
        return url

    scheme_sep = "://"
    idx = url.find(scheme_sep)
    if idx == -1:
        return url

    scheme = url[:idx]
    rest = url[idx + len(scheme_sep):]

    if scheme.startswith(("postgresql", "postgres")):
        return f"postgresql+psycopg2{scheme_sep}{rest}"

    return url


# ── Engine / session factories (explicit, connection-free at import) ─────────


def make_engine(url: str, **kwargs: Any) -> AsyncEngine:
    """Build an :class:`AsyncEngine` with a small pool suited to Render's
    connection-capped Postgres plans.

    Defaults mirror ``RENDER-MIGRATION.md §3.2``: ``pool_size=3``,
    ``max_overflow=2``, ``pool_pre_ping=True``, ``pool_recycle=300``. The URL is
    normalized to asyncpg first. Any keyword overrides the defaults.

    Note: SQLAlchemy does not open a connection until the engine is first used,
    so calling this does not by itself perform network I/O.
    """
    engine_kwargs: dict[str, Any] = {
        "pool_size": 3,
        "max_overflow": 2,
        "pool_pre_ping": True,
        "pool_recycle": 300,
    }
    engine_kwargs.update(kwargs)
    return create_async_engine(normalize_db_url(url), **engine_kwargs)


def get_sessionmaker(engine: AsyncEngine) -> async_sessionmaker:
    """Return an ``async_sessionmaker`` bound to ``engine``.

    ``expire_on_commit=False`` keeps ORM instances usable after commit (common
    for request/response handoff), matching typical async-web patterns.
    """
    return async_sessionmaker(engine, expire_on_commit=False)


# ── ORM base ─────────────────────────────────────────────────────────────────


class Base(DeclarativeBase):
    """Declarative base; ``Base.metadata`` is the Alembic target metadata."""


# Reusable server-side UUID default (pgcrypto extension provides gen_random_uuid()).
_UUID_PK_DEFAULT = func.gen_random_uuid()


# ── Auth-owned tables ────────────────────────────────────────────────────────


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        postgresql.UUID(as_uuid=False),
        primary_key=True,
        server_default=_UUID_PK_DEFAULT,
    )
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    google_sub: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    display_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_login: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Session(Base):
    """Server-side opaque session. Tenant table (carries ``user_id``)."""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(
        postgresql.UUID(as_uuid=False),
        primary_key=True,
        server_default=_UUID_PK_DEFAULT,
    )
    user_id: Mapped[str] = mapped_column(
        postgresql.UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # sha256(raw cookie token); the raw token is never stored → BYTEA.
    token_hash: Mapped[bytes] = mapped_column(
        LargeBinary, unique=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    user_agent: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ip: Mapped[Optional[str]] = mapped_column(postgresql.INET, nullable=True)

    __table_args__ = (Index("ix_sessions_user", "user_id"),)


class UserCredential(Base):
    """Per-user LLM/Jira credentials. Replaces settings.json + .keyfile.

    Tenant table. ``secret_ciphertext`` holds a Fernet token (BYTEA-equivalent
    opaque bytes); non-secret config lives in the JSONB ``config`` blob.
    """

    __tablename__ = "user_credentials"

    id: Mapped[str] = mapped_column(
        postgresql.UUID(as_uuid=False),
        primary_key=True,
        server_default=_UUID_PK_DEFAULT,
    )
    user_id: Mapped[str] = mapped_column(
        postgresql.UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)  # 'llm' | 'jira'
    provider: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    config: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    # Fernet ciphertext of api_key / jira_api_token. Stored as BYTEA.
    secret_ciphertext: Mapped[Optional[bytes]] = mapped_column(
        LargeBinary, nullable=True
    )
    key_version: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="1"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id", "kind", "provider", name="uq_user_credentials_scope"
        ),
        Index("ix_creds_user", "user_id"),
    )


# ── Run / task / coverage / audit tables ─────────────────────────────────────


class Run(Base):
    """One extraction run. Replaces metadata.json + the active_runs dict.

    Tenant table. ``config`` is ``RunConfig.model_dump(mode="json")`` (sans
    secrets) round-tripped through JSONB.
    """

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(
        postgresql.UUID(as_uuid=False),
        primary_key=True,
        server_default=_UUID_PK_DEFAULT,
    )
    user_id: Mapped[str] = mapped_column(
        postgresql.UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    legacy_run_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="queued"
    )
    progress: Mapped[float] = mapped_column(
        Float, nullable=False, server_default="0"
    )
    current_step: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    pdf_object_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tree_object_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    node_index_object_key: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    coverage_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    task_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    worker_heartbeat: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("ix_runs_user", "user_id", created_at.desc()),
    )


class Task(Base):
    """A managed task row. ``id`` == ``ManagedTask.id`` (NEVER regenerated).

    Tenant table. JSONB columns round-trip the corresponding Pydantic
    sub-models (flags, acceptance_criteria, source_refs, dependencies,
    merged_from) plus an ``extra`` catch-all for use_case/deliverables/etc.
    """

    __tablename__ = "tasks"

    # Client-supplied primary key: exactly ManagedTask.id. No server default.
    id: Mapped[str] = mapped_column(
        postgresql.UUID(as_uuid=False), primary_key=True
    )
    run_id: Mapped[str] = mapped_column(
        postgresql.UUID(as_uuid=False),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(
        postgresql.UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    short_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)  # TaskStatus value
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    continues_to_next: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    flags: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    acceptance_criteria: Mapped[Optional[list]] = mapped_column(
        JSONB, nullable=True
    )
    source_refs: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    dependencies: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    merged_from: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    extra: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    jira_issue_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    jira_issue_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_tasks_run", "run_id"),
        Index("ix_tasks_user", "user_id"),
    )


class CoverageReport(Base):
    """Per-node coverage report. First-class so the 100%-INCOMPLETE bug fix is
    queryable/eval-able. Tenant table."""

    __tablename__ = "coverage_reports"

    id: Mapped[str] = mapped_column(
        postgresql.UUID(as_uuid=False),
        primary_key=True,
        server_default=_UUID_PK_DEFAULT,
    )
    run_id: Mapped[str] = mapped_column(
        postgresql.UUID(as_uuid=False),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(
        postgresql.UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    node_id: Mapped[str] = mapped_column(Text, nullable=False)
    report: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_cov_run", "run_id"),
        # Row-level isolation index on the tenant key (additive to §3.1).
        Index("ix_cov_user", "user_id"),
    )


class AuditLog(Base):
    """Append-only audit trail. 1:1 lift of the SQLite audit.db + ``user_id``.

    Tenant table. Uses a ``BIGSERIAL``-equivalent autoincrement primary key.
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    run_id: Mapped[Optional[str]] = mapped_column(
        postgresql.UUID(as_uuid=False),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=True,
    )
    user_id: Mapped[str] = mapped_column(
        postgresql.UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    agent: Mapped[str] = mapped_column(Text, nullable=False)
    node_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    task_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    llm_tokens_used: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    llm_model: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=""
    )

    __table_args__ = (
        Index("ix_audit_run", "run_id", "id"),
        # Row-level isolation: every tenant table carries a user_id index.
        # (Additive to RENDER-MIGRATION §3.1, which indexed only run_id here.)
        Index("ix_audit_user", "user_id"),
    )


__all__ = [
    "Base",
    "normalize_db_url",
    "normalize_sync_db_url",
    "make_engine",
    "get_sessionmaker",
    "User",
    "Session",
    "UserCredential",
    "Run",
    "Task",
    "CoverageReport",
    "AuditLog",
]
