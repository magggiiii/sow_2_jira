# core/health.py
"""
Per-run health reporting for the pipeline harness.

This module is additive: it aggregates the per-stage outcomes a run already
produces (``StageStatus`` from ``core.results``) into a single, dependency-free
Pydantic v2 report. Nothing in the existing pipeline is required to use it — it
is a read-only summary surface.

``StageHealth`` is one stage's distilled health line; ``RunHealthReport``
collects those lines and derives an overall verdict, counts, total cost, and the
list of degradation reasons. Both round-trip cleanly via ``model_dump`` /
``model_validate``.
"""

from __future__ import annotations

from typing import Any, List, Mapping

from pydantic import BaseModel, Field

from core.results import StageStatus


# Severity ordering for picking the worst stage status.
# Higher number == worse. SKIPPED is the least severe and is treated as OK for
# the overall verdict (unless every stage was skipped, handled in code below).
_SEVERITY: dict[StageStatus, int] = {
    StageStatus.SKIPPED: 0,
    StageStatus.OK: 1,
    StageStatus.DEGRADED: 2,
    StageStatus.FAILED: 3,
}


class StageHealth(BaseModel):
    """Distilled health for a single harness stage."""

    name: str
    status: StageStatus
    reason: str = ""
    cost_usd: float = 0.0


class RunHealthReport(BaseModel):
    """
    Aggregated per-run health derived from a list of :class:`StageHealth`.

    The report intentionally stores raw ``stages`` and computes every summary
    on demand so it stays consistent as stages are appended via :meth:`add`.
    """

    stages: List[StageHealth] = Field(default_factory=list)

    # ─── Mutation ─────────────────────────────────────────────────────────────

    def add(self, stage_health: StageHealth) -> "RunHealthReport":
        """Append a stage's health and return self for chaining."""
        self.stages.append(stage_health)
        return self

    # ─── Counts ───────────────────────────────────────────────────────────────

    def _count(self, status: StageStatus) -> int:
        return sum(1 for s in self.stages if s.status is status)

    @property
    def ok(self) -> int:
        return self._count(StageStatus.OK)

    @property
    def degraded(self) -> int:
        return self._count(StageStatus.DEGRADED)

    @property
    def failed(self) -> int:
        return self._count(StageStatus.FAILED)

    @property
    def skipped(self) -> int:
        return self._count(StageStatus.SKIPPED)

    # ─── Aggregates ─────────────────────────────────────────────────────────────

    @property
    def total_cost_usd(self) -> float:
        return sum(s.cost_usd for s in self.stages)

    @property
    def degraded_reasons(self) -> List[str]:
        """Non-empty reasons from stages that are DEGRADED or FAILED."""
        return [
            s.reason
            for s in self.stages
            if s.status in (StageStatus.DEGRADED, StageStatus.FAILED) and s.reason
        ]

    @property
    def overall_status(self) -> StageStatus:
        """
        Worst of the stage statuses.

        Severity order is FAILED > DEGRADED > OK, with SKIPPED treated as OK for
        the overall verdict. With no stages, or when every stage was skipped, the
        overall status is SKIPPED.
        """
        if not self.stages:
            return StageStatus.SKIPPED

        statuses = [s.status for s in self.stages]
        if all(st is StageStatus.SKIPPED for st in statuses):
            return StageStatus.SKIPPED

        worst = max(statuses, key=lambda st: _SEVERITY[st])
        # SKIPPED outranks nothing meaningful here; if the worst is SKIPPED we
        # already returned above, so a SKIPPED winner only happens when mixed
        # with itself. Treat a non-failing/non-degraded mix as OK.
        if worst is StageStatus.SKIPPED:
            return StageStatus.OK
        return worst

    @property
    def is_degraded(self) -> bool:
        """True when the overall verdict is DEGRADED."""
        return self.overall_status is StageStatus.DEGRADED

    # ─── Serialization ───────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """
        Flat, serializable summary of the report.

        Includes the raw stages plus all derived aggregates so callers (UI,
        logs, audit) can consume the verdict without re-deriving it.
        """
        return {
            "overall_status": self.overall_status.value,
            "is_degraded": self.is_degraded,
            "counts": {
                "ok": self.ok,
                "degraded": self.degraded,
                "failed": self.failed,
                "skipped": self.skipped,
            },
            "total_cost_usd": self.total_cost_usd,
            "degraded_reasons": self.degraded_reasons,
            "stages": [s.model_dump(mode="json") for s in self.stages],
        }


def build_health_report(signals: Mapping[str, Any]) -> RunHealthReport:
    """
    Assemble a :class:`RunHealthReport` from loosely-structured run signals.

    This is a *pure* function: it reads only the ``signals`` mapping and returns
    a fresh report, so it can be unit-tested without running a pipeline. It is
    intentionally tolerant — a stage is only added when its signal is present,
    and any missing signal is simply omitted (no stage, no failure).

    Recognized signals (all optional):

    - ``dedup_degraded`` (bool): when truthy, the ``dedup`` stage is DEGRADED;
      otherwise, if the key is present at all, ``dedup`` is OK.
    - ``dedup_degraded_reason`` (str | None): reason text for a degraded dedup.
    - ``extraction_error_count`` (int): when > 0, the ``extraction`` stage is
      DEGRADED with a count-derived reason; when present and 0, ``extraction``
      is OK.
    - ``extraction_failed`` (bool): when truthy, ``extraction`` is FAILED
      (overrides a non-zero error count, since a hard failure is more severe).
    - ``coverage_pct`` (int | float | None): when present, the ``coverage``
      stage is OK (a value being available means coverage was computed). The
      raw value is not used to downgrade status here; that policy is left to a
      later increment.
    """
    report = RunHealthReport()

    # ── Deduplication ─────────────────────────────────────────────────────────
    if "dedup_degraded" in signals:
        if signals.get("dedup_degraded"):
            reason = signals.get("dedup_degraded_reason") or "deduplication degraded"
            report.add(
                StageHealth(
                    name="dedup",
                    status=StageStatus.DEGRADED,
                    reason=str(reason),
                )
            )
        else:
            report.add(StageHealth(name="dedup", status=StageStatus.OK))

    # ── Extraction ────────────────────────────────────────────────────────────
    if signals.get("extraction_failed"):
        report.add(
            StageHealth(
                name="extraction",
                status=StageStatus.FAILED,
                reason=str(signals.get("extraction_failed_reason") or "extraction failed"),
            )
        )
    elif "extraction_error_count" in signals:
        err = int(signals.get("extraction_error_count") or 0)
        if err > 0:
            report.add(
                StageHealth(
                    name="extraction",
                    status=StageStatus.DEGRADED,
                    reason=f"{err} extraction error(s) during run",
                )
            )
        else:
            report.add(StageHealth(name="extraction", status=StageStatus.OK))

    # ── Coverage ──────────────────────────────────────────────────────────────
    if signals.get("coverage_pct") is not None:
        report.add(StageHealth(name="coverage", status=StageStatus.OK))

    return report
