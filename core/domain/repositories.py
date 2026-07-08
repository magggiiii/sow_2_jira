# core/domain/repositories.py
"""Pure ORM <-> domain mapping helpers for the persistence seam (STREAM DB-REPOS).

This module is dependency-light on purpose: it knows about the ORM rows in
``pipeline.db`` and the Pydantic domain models in ``models.schemas``, and it
translates between them. It performs NO I/O — every function takes/returns
in-memory objects/dicts, so the concrete async repositories in
``integrations/repositories.py`` can stay thin (just session + query wiring).

Two mappings live here:

* Run row  <-> plain dict  (``Run.config`` is JSONB round-tripping a
  ``RunConfig.model_dump(mode="json")`` blob; the rest are scalar columns).
* Task row <-> ``models.schemas.ManagedTask``  — ``Task.id`` is the SAME id as
  ``ManagedTask.id`` (a UUID str), NEVER regenerated.

Only the columns that both sides care about are mapped. Server-managed columns
(``created_at`` / ``updated_at`` and any DB defaults) are left to the database.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from models.schemas import ManagedTask
from pipeline.db import Run, Task

# ── Run <-> dict ──────────────────────────────────────────────────────────────

# Scalar Run columns the repo persists/updates. ``id`` and ``user_id`` are set
# explicitly by the repository (they are identity/tenant keys), so they are NOT
# in this set of caller-settable fields.
_RUN_SETTABLE_FIELDS = (
    "legacy_run_id",
    "filename",
    "status",
    "progress",
    "current_step",
    "message",
    "config",
    "pdf_object_key",
    "tree_object_key",
    "node_index_object_key",
    "coverage_pct",
    "task_count",
    "error",
)

_RUN_READ_FIELDS = ("id", "user_id") + _RUN_SETTABLE_FIELDS


def run_row_to_dict(row: Run) -> dict:
    """Project a ``Run`` ORM row into a plain, JSON-friendly dict."""
    return {field: getattr(row, field) for field in _RUN_READ_FIELDS}


def apply_run_data(row: Run, data: dict[str, Any]) -> None:
    """Copy caller-supplied ``data`` onto a ``Run`` row (settable fields only).

    Unknown keys and identity/tenant keys (``id`` / ``user_id``) are ignored so
    a caller can never repoint a row at another tenant via an update payload.
    ``filename``/``config`` fall back to safe defaults on create if omitted, so a
    minimal ``{"status": ...}`` payload still satisfies the NOT NULL columns.
    """
    for field in _RUN_SETTABLE_FIELDS:
        if field in data:
            setattr(row, field, data[field])


def new_run_row(user_id: str, run_id: str, data: dict[str, Any]) -> Run:
    """Build a new ``Run`` row for ``user_id``/``run_id`` from ``data``.

    Guarantees the NOT NULL columns (``filename``, ``config``) have a value even
    when the caller omits them.
    """
    row = Run(
        id=run_id,
        user_id=user_id,
        filename=data.get("filename", ""),
        config=data.get("config") if data.get("config") is not None else {},
    )
    apply_run_data(row, data)
    return row


# ── Task <-> ManagedTask ──────────────────────────────────────────────────────


def _dump_list(items: Any) -> list:
    """Serialize a list of Pydantic sub-models / enums / scalars to JSON-safe."""
    out: list = []
    for item in items or []:
        if hasattr(item, "model_dump"):
            out.append(item.model_dump(mode="json"))
        elif isinstance(item, UUID):
            out.append(str(item))
        else:
            # enums (str-Enum) and plain scalars serialize as their value.
            out.append(getattr(item, "value", item))
    return out


def task_to_row(user_id: str, run_id: str, task: ManagedTask) -> Task:
    """Build a ``Task`` ORM row from a ``ManagedTask``.

    ``Task.id`` == ``str(ManagedTask.id)`` — byte-identical, never regenerated.
    Rich sub-models are stored in their JSONB columns; use_case/deliverables/etc.
    go into the ``extra`` catch-all.
    """
    return Task(
        id=str(task.id),
        run_id=run_id,
        user_id=user_id,
        title=task.title,
        short_description=task.short_description,
        # TaskStatus is a plain (str, Enum): its default __str__ is
        # "TaskStatus.CLOSED", so persist the raw .value ("CLOSED") instead.
        status=str(getattr(task.status, "value", task.status)),
        confidence=float(task.confidence),
        continues_to_next=bool(task.continues_to_next),
        flags=_dump_list(task.flags),
        acceptance_criteria=(
            _dump_list(task.acceptance_criteria)
            if task.acceptance_criteria is not None
            else None
        ),
        source_refs=_dump_list(task.source_refs),
        dependencies=_dump_list(task.dependencies),
        merged_from=[str(m) for m in (task.merged_from or [])],
        extra={
            "use_case": task.use_case,
            "considerations_constraints": task.considerations_constraints,
            "deliverables": task.deliverables,
            "mockup_prototype": task.mockup_prototype,
        },
        jira_issue_key=task.jira_issue_key,
    )


def row_to_task(row: Task) -> ManagedTask:
    """Rebuild a ``ManagedTask`` from a ``Task`` ORM row.

    The inverse of :func:`task_to_row`. ``ManagedTask`` validators re-hydrate the
    JSONB blobs into their Pydantic sub-models; the id is parsed back to a UUID
    identical to the one originally stored.
    """
    extra = row.extra or {}
    return ManagedTask(
        id=UUID(row.id),
        title=row.title,
        short_description=row.short_description or "",
        acceptance_criteria=row.acceptance_criteria,
        use_case=extra.get("use_case"),
        considerations_constraints=extra.get("considerations_constraints"),
        deliverables=extra.get("deliverables"),
        mockup_prototype=extra.get("mockup_prototype"),
        confidence=row.confidence,
        flags=list(row.flags or []),
        continues_to_next=bool(row.continues_to_next),
        status=row.status,
        jira_issue_key=row.jira_issue_key,
        source_refs=list(row.source_refs or []),
        merged_from=[UUID(m) for m in (row.merged_from or [])],
        dependencies=list(row.dependencies or []),
    )


def apply_task_data(row: Task, data: dict[str, Any]) -> None:
    """Apply a partial update dict onto a ``Task`` row.

    Only known, non-identity columns are updated. Enum values are coerced to
    their string form; identity/tenant keys (``id`` / ``run_id`` / ``user_id``)
    are ignored so an update can never move a task across tenants or runs.
    """
    scalar_fields = (
        "title",
        "short_description",
        "confidence",
        "continues_to_next",
        "jira_issue_key",
        "jira_issue_url",
    )
    for field in scalar_fields:
        if field in data:
            setattr(row, field, data[field])

    if "status" in data:
        status = data["status"]
        row.status = str(getattr(status, "value", status))

    if "confidence" in data:
        row.confidence = float(data["confidence"])

    # JSONB list/dict columns: accept either already-serialized values or
    # lists of Pydantic sub-models.
    for field in ("flags", "source_refs", "dependencies"):
        if field in data:
            setattr(row, field, _dump_list(data[field]))
    if "acceptance_criteria" in data:
        ac = data["acceptance_criteria"]
        row.acceptance_criteria = _dump_list(ac) if ac is not None else None
    if "merged_from" in data:
        row.merged_from = [str(m) for m in (data["merged_from"] or [])]


__all__ = [
    "run_row_to_dict",
    "apply_run_data",
    "new_run_row",
    "task_to_row",
    "row_to_task",
    "apply_task_data",
]
