# app/container.py
"""
Composition root: wire the existing concrete adapters behind ``core.ports``.

``build_container`` is a plain factory function (no DI framework). It returns a
``Container`` dataclass whose attributes are the port implementations the new
harness depends on:

    .llm           → pipeline.llm_client.LLMClient   (satisfies LLMProvider)
    .audit         → audit.logger.AuditLogger         (satisfies AuditSink)
    .jira          → callable -> integrations.jira_client.JiraClient
                     (a factory, because JiraClient needs per-push creds/context)
    .object_store  → LocalObjectStore                 (satisfies ObjectStore)

This module is ADDITIVE. It does not change any existing call site or behavior;
it is an alternative wiring the harness can opt into. The ``llm`` and ``audit``
concretes are constructed exactly the way ``pipeline.orchestrator`` constructs
them today (mode + audit_logger + run_id + stop_event), so behavior matches.

Dependency injection of ``llm``/``audit``/``object_store`` is supported purely
to keep this testable offline; production callers can rely on the defaults.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Optional

from integrations.object_store import LocalObjectStore
from integrations.queue.fake import FakeQueue
from models.schemas import JiraHierarchy, LLMMode

if TYPE_CHECKING:  # imported lazily at runtime to keep import graph minimal
    from audit.logger import AuditLogger
    from config.settings import Settings
    from integrations.jira_client import JiraClient
    from pipeline.llm_client import LLMClient


# ─── Settings-driven adapter selection (ENV-CONFIG.md) ────────────────────────
#
# Only the composition root branches on ``Settings``. The LOCAL branches return
# the offline adapters that already exist; the PRODUCTION branches lazy-import
# the cloud adapters (S3 / arq) so importing this module never drags in boto3 or
# arq, and the local/test path never needs them.


def select_object_store(settings: "Settings", base_dir: str = "data") -> Any:
    """Return the ObjectStore adapter for ``settings`` (local FS vs S3)."""
    if settings.is_production:
        # Lazy: only production pulls boto3.
        from integrations.object_store import S3ObjectStore

        return S3ObjectStore(
            endpoint=settings.s3_endpoint,
            access_key=settings.s3_access_key,
            secret=settings.s3_secret,
            region=settings.s3_region,
            bucket=settings.s3_bucket,
        )
    return LocalObjectStore(base_dir=base_dir)


def select_queue(settings: Optional["Settings"]) -> Any:
    """Return the JobQueue adapter for ``settings`` (in-process vs arq/Redis)."""
    if settings is not None and settings.use_redis_queue:
        # Lazy: only the Redis path pulls arq.
        from integrations.queue.arq_queue import ArqQueue

        return ArqQueue(settings.redis_url)
    return FakeQueue()


# The hardened, path-traversal-safe ``LocalObjectStore`` lives in
# ``integrations.object_store`` (imported above) and is re-exported here for
# back-compat (``from app.container import LocalObjectStore``). It is the default
# blob backend for the harness; a Cloudflare R2 / S3 adapter can later satisfy
# the same ``core.ports.ObjectStore`` port without touching callers.


# ─── Container ────────────────────────────────────────────────────────────────


@dataclass
class Container:
    """
    Holds the wired port implementations for one harness run.

    Attributes are typed loosely (the ports are structural Protocols) so the
    concretes satisfy them without inheritance. ``jira`` is a factory callable
    rather than an instance because ``JiraClient`` needs per-push credentials
    and run/project context.
    """

    llm: Any
    audit: Any
    jira: Callable[..., Any]
    object_store: Any
    queue: Any = None


def build_container(
    *,
    run_id: str = "harness",
    llm_mode: LLMMode = LLMMode.API,
    stop_event: Optional[threading.Event] = None,
    object_store_base_dir: str = "data",
    audit: Optional["AuditLogger"] = None,
    llm: Optional["LLMClient"] = None,
    object_store: Optional[Any] = None,
    queue: Optional[Any] = None,
    settings: Optional["Settings"] = None,
) -> Container:
    """
    Build a wired :class:`Container`.

    All concretes can be overridden via keyword for testing/offline use; when
    omitted they are constructed the same way the orchestrator does today.

    Parameters
    ----------
    run_id:
        Run identifier threaded into the audit/LLM/jira concretes.
    llm_mode:
        ``LLMMode`` used to construct the ``LLMClient`` (mirrors the
        orchestrator, which passes ``config.llm_mode``).
    stop_event:
        Cooperative cancellation event handed to the ``LLMClient``.
    object_store_base_dir:
        Base directory for the default ``LocalObjectStore``.
    audit, llm, object_store:
        Optional pre-built concretes (dependency injection). When provided they
        are used as-is instead of being constructed.
    """
    stop_event = stop_event or threading.Event()

    # ── Audit sink (cheap; SQLite only) ──────────────────────────────────────
    if audit is None:
        from audit.logger import AuditLogger

        audit = AuditLogger()

    # ── LLM provider (constructed exactly as pipeline.orchestrator does) ──────
    if llm is None:
        from pipeline.llm_client import LLMClient

        llm = LLMClient(
            mode=llm_mode,
            audit_logger=audit,
            run_id=run_id,
            stop_event=stop_event,
        )

    # ── Object store: settings-selected (local FS vs S3), else local default ──
    if object_store is None:
        if settings is not None:
            object_store = select_object_store(settings, base_dir=object_store_base_dir)
        else:
            object_store = LocalObjectStore(base_dir=object_store_base_dir)

    # ── Job queue: settings-selected (in-process vs arq/Redis) ────────────────
    if queue is None:
        queue = select_queue(settings)

    # ── Jira gateway factory (per-push creds/context) ────────────────────────
    def jira_factory(
        *,
        project_key: str,
        hierarchy: JiraHierarchy = JiraHierarchy.EPIC_TASK,
        node_index: Optional[dict] = None,
    ) -> "JiraClient":
        from integrations.jira_client import JiraClient

        return JiraClient(
            hierarchy=hierarchy,
            audit=audit,
            run_id=run_id,
            project_key=project_key,
            node_index=node_index,
        )

    return Container(
        llm=llm,
        audit=audit,
        jira=jira_factory,
        object_store=object_store,
        queue=queue,
    )
