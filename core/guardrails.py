# core/guardrails.py
"""
Reusable, deterministic guardrail primitives.

This module formalizes the small "gate" and "verify" checks that several agents
apply ad hoc today:

* The critic's confidence floor (``critique.confidence >= flag_confidence_floor``
  in ``pipeline/agents/critic.py``) and coverage's inline confidence gate.
* The dedup "work-was-done" degraded signal (``large_input and merges == 0`` in
  ``pipeline/agents/deduplication.py``, with ``DEGRADED_INPUT_THRESHOLD = 100``).
* The ``complete_json`` / ``complete`` truncation check
  (``finish_reason in {"length", "max_tokens", "max_output_tokens"}`` in
  ``pipeline/llm_client.py``).

The primitives here are PURE and dependency-free (no I/O, no logging, no network,
no LLM). They do not yet replace those call sites — this increment only provides
the building blocks. A later behavior-preserving cleanup can refactor the agents
to call into these.

``ConfidenceGate`` is a small gate object; the ``assert_*`` functions return a
:class:`VerifyResult` (a ``(passed, reason)`` named tuple) so callers can both
branch on the boolean and surface a human-readable reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, NamedTuple, Sequence
from typing import Literal


def _read(obj: Any, key: str, default: Any = None) -> Any:
    """Read ``key`` from a dict (``obj[key]``) or an object (``obj.key``).

    Lets the gate consume both the serialized ``model_dump(mode="json")`` report
    dicts the orchestrator persists and the live typed models, without coupling
    to either shape.
    """
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


# ─── VerifyResult ────────────────────────────────────────────────────────────


class VerifyResult(NamedTuple):
    """
    Outcome of a verify primitive.

    ``passed`` is True when the check is satisfied. When ``passed`` is False the
    check is considered *degraded* (the caller decides whether to flag, drop, or
    raise) and ``reason`` carries a human-readable explanation. On success
    ``reason`` is an empty string.

    Being a ``NamedTuple`` it unpacks as ``(passed, reason)`` for callers that
    prefer the tuple form, while still allowing attribute access.
    """

    passed: bool
    reason: str = ""


# ─── ConfidenceGate ──────────────────────────────────────────────────────────


class ConfidenceGate:
    """
    A deterministic confidence floor gate.

    Mirrors the ``>= floor`` semantics the critic and coverage apply inline: an
    object (or a bare value) is admitted only when its confidence is at or above
    ``floor``. This is pure — it performs no I/O and never mutates the object.

    Parameters
    ----------
    field:
        Name of the attribute read off candidate objects in :meth:`admit`
        (e.g. ``"confidence"``). Missing attributes are treated as ``0.0``.
    floor:
        Inclusive minimum confidence required to admit. A value exactly equal to
        ``floor`` is admitted (``>=``), matching the existing call sites.
    on_reject:
        Advisory policy describing what a caller should do with a rejected
        candidate — ``"drop"`` (discard it) or ``"flag_low_confidence"`` (keep it
        but mark it). This field is purely informational here; the gate itself
        only ever returns a boolean and does not act on the policy.
    """

    __slots__ = ("field", "floor", "on_reject")

    def __init__(
        self,
        field: str,
        floor: float,
        on_reject: Literal["drop", "flag_low_confidence"] = "drop",
    ) -> None:
        self.field = field
        self.floor = float(floor)
        self.on_reject = on_reject

    def admit(self, obj: Any) -> bool:
        """
        Return True if ``getattr(obj, self.field, 0.0) >= self.floor``.

        A missing attribute is treated as ``0.0`` confidence (i.e. rejected
        unless the floor is also ``<= 0``). The candidate is never mutated.
        """
        value = getattr(obj, self.field, 0.0)
        return self.admit_value(value)

    def admit_value(self, value: float) -> bool:
        """Return True if a bare confidence ``value`` is at or above the floor."""
        try:
            return float(value) >= self.floor
        except (TypeError, ValueError):
            # Non-numeric confidence is treated as 0.0 — never admitted above a
            # positive floor.
            return self.floor <= 0.0

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return (
            f"ConfidenceGate(field={self.field!r}, floor={self.floor!r}, "
            f"on_reject={self.on_reject!r})"
        )


# ─── CoverageGate (C-4 / STEP 3.3) ────────────────────────────────────────────


@dataclass(frozen=True)
class CoverageReportDecision:
    """The gate's per-report verdict — run-level advisory metadata."""

    node_id: str
    checker_confidence: float
    missed_count: int
    flagged: bool


@dataclass
class CoverageGateResult:
    """Outcome of one run-wide coverage gate pass."""

    decisions: list[CoverageReportDecision] = field(default_factory=list)
    flagged_node_ids: set[str] = field(default_factory=set)
    flagged_task_count: int = 0
    total_task_count: int = 0

    @property
    def incomplete_rate(self) -> float:
        """Fraction of the final task set flagged INCOMPLETE — the INV-4 metric."""
        if not self.total_task_count:
            return 0.0
        return self.flagged_task_count / self.total_task_count


class CoverageGate:
    """
    Run-wide, post-dedup, report-level coverage gate — the structural C-4 fix.

    Replaces the per-section, pre-dedup blanket INCOMPLETE flagging (audit C-4,
    "the 100% INCOMPLETE bomb"). Instead of mutating tasks inside the per-node
    extract loop the instant any ``missed_items`` appears, the gate runs ONCE over
    the final (deduped + gap-recovered) task set:

      * A coverage report flags its section only when it clears the SAME logic the
        per-section :func:`pipeline.agents.coverage_check.should_flag_section_incomplete`
        applied — genuine ``missed_items`` AND ``checker_confidence >= floor``,
        delegated to the shared :class:`ConfidenceGate` so the two paths can never
        drift apart.
      * A surviving task is flagged ``INCOMPLETE`` iff one of its ``source_refs``
        node_ids belongs to a flagged section. Report-level, not blanket: a single
        confident miss can no longer flag the whole corpus, and tasks merged away
        during dedup are gone before the gate runs — so the INCOMPLETE *rate*
        reflects survivors, not the pre-dedup count.

    The embedding-tier corpus filter (dropping misses already covered by a task
    extracted elsewhere) is a later feature-stage refinement layered on top — it
    is deliberately NOT here, to keep this primitive numpy-free and pure.
    """

    DEFAULT_FLOOR = 0.6

    __slots__ = ("floor",)

    def __init__(self, floor: float = DEFAULT_FLOOR) -> None:
        self.floor = float(floor)

    @staticmethod
    def report_flags(*, missed_count: int, checker_confidence: Any, floor: float) -> bool:
        """The report-level predicate: genuine misses AND confidence at/above floor.

        Mirrors ``should_flag_section_incomplete`` by routing the confidence
        comparison through the shared :class:`ConfidenceGate` (same inclusive
        ``>=`` semantics), so the run-wide gate and the legacy per-section gate
        share one definition of "confident enough".
        """
        if missed_count <= 0:
            return False
        return ConfidenceGate(field="checker_confidence", floor=floor).admit_value(
            checker_confidence
        )

    def apply(
        self,
        tasks: Iterable[Any],
        reports: Iterable[Any] | Mapping[str, Any],
    ) -> CoverageGateResult:
        """Flag INCOMPLETE on the final task set, run-wide and report-level.

        ``tasks`` are the final (deduped) ManagedTask-like objects; ``reports`` is
        an iterable — or a ``{node_id: report}`` mapping — of coverage reports in
        either typed or ``model_dump`` form. Task flags are mutated in place
        (idempotent); the returned :class:`CoverageGateResult` carries the
        run-level advisory metadata (per-report decisions + the INCOMPLETE rate).
        """
        report_iter = reports.values() if isinstance(reports, Mapping) else reports

        decisions: list[CoverageReportDecision] = []
        flagged_node_ids: set[str] = set()
        for report in report_iter:
            node_id = _read(report, "node_id", "") or ""
            confidence = _read(report, "checker_confidence", 0.0)
            missed_count = len(_read(report, "missed_items", []) or [])
            flagged = self.report_flags(
                missed_count=missed_count,
                checker_confidence=confidence,
                floor=self.floor,
            )
            decisions.append(
                CoverageReportDecision(
                    node_id=node_id,
                    checker_confidence=float(confidence or 0.0),
                    missed_count=missed_count,
                    flagged=flagged,
                )
            )
            if flagged and node_id:
                flagged_node_ids.add(node_id)

        task_list = list(tasks)
        flagged_task_count = 0
        for task in task_list:
            task_nodes = {
                _read(ref, "node_id", "")
                for ref in (_read(task, "source_refs", []) or [])
            }
            if task_nodes & flagged_node_ids:
                self._flag_incomplete(task)
                flagged_task_count += 1

        return CoverageGateResult(
            decisions=decisions,
            flagged_node_ids=flagged_node_ids,
            flagged_task_count=flagged_task_count,
            total_task_count=len(task_list),
        )

    @staticmethod
    def _flag_incomplete(task: Any) -> bool:
        """Append ``TaskFlag.INCOMPLETE`` if absent. Returns True if newly added."""
        from models.schemas import TaskFlag

        flags = _read(task, "flags", None)
        if flags is None:
            return False
        if TaskFlag.INCOMPLETE in flags:
            return False
        flags.append(TaskFlag.INCOMPLETE)
        return True


# ─── PushGate (STEP 3.5) ──────────────────────────────────────────────────────

# TaskFlag values that BLOCK a Jira push (quality failures). Informational flags
# (NO_MOCKUP / POTENTIAL_DUPLICATE / GAP_RECOVERED / TRUNCATION) do NOT block.
# Imported lazily inside the gate to keep this module import-light; the literal
# names are kept here as the documented contract.
_BLOCKING_FLAG_NAMES = (
    "INCOMPLETE",            # C-4 confident coverage miss — known-partial work item
    "LOW_CONFIDENCE",        # critic/extraction low-confidence — not trustworthy to ship
    "AMBIGUOUS_SCOPE",       # not actionable as written
    "NO_ACCEPTANCE_CRITERIA",  # fails the "actionable Jira-ready task" bar
)


def _build_blocking_flags():
    from models.schemas import TaskFlag

    return frozenset(TaskFlag(n) for n in _BLOCKING_FLAG_NAMES)


# The default blocking set, resolved once. TRUNCATION is intentionally NOT here
# (it's a run-quality breadcrumb, not a per-task verdict) but ``PushGate`` takes
# ``blocking_flags`` so a stricter deployment can add it.
try:  # pragma: no cover - trivial import guard
    BLOCKING_FLAGS = _build_blocking_flags()
except Exception:  # pragma: no cover
    BLOCKING_FLAGS = frozenset()


def _flag_value(flag: Any) -> str:
    """Normalize a flag (TaskFlag enum or bare string) to its string value."""
    return flag.value if hasattr(flag, "value") else str(flag)


@dataclass(frozen=True)
class PushDecision:
    """Per-task push verdict — the audit trail of an ``assert_pushable`` call."""

    task_id: str
    verdict: Literal["push", "skip", "block"]
    reason: str = ""


@dataclass
class PushGateResult:
    """Partition of a push batch. ``pushable`` is in input order; merged-away
    duplicates never reach here (dedup ran upstream)."""

    pushable: list = field(default_factory=list)       # task objects cleared to push
    skipped: list = field(default_factory=list)        # PushDecision — already pushed
    blocked: list = field(default_factory=list)        # PushDecision — flagged / DEGRADED run
    decisions: list = field(default_factory=list)      # PushDecision per input task, in order


class PushBlocked(Exception):
    """Raised by :meth:`PushGate.assert_pushable` when a block exists and
    ``override`` is False. Carries the full :class:`PushGateResult` so the caller
    can still push the cleared subset and surface per-task reasons."""

    def __init__(self, result: "PushGateResult") -> None:
        self.result = result
        self.blocked = result.blocked
        msg = "; ".join(f"{d.task_id}: {d.reason}" for d in result.blocked) or "push blocked"
        super().__init__(msg)


class PushGate:
    """
    Deterministic admission gate for Jira push (STEP 3.5).

    Pure + read-only — it NEVER mutates tasks (the PUSHED status is set by the
    caller after a successful Jira create). Classification is SKIP-first:

      1. ``jira_issue_key`` present -> SKIP (idempotent re-push; survives override).
      2. else, run DEGRADED or task carries a blocking flag -> BLOCK (unless override).
      3. else -> PUSH.

    ``assert_pushable`` returns a :class:`PushGateResult`; with ``override=False``
    a non-empty blocked set raises :class:`PushBlocked` (carrying that result).
    Accepts tasks and ``run_status`` as typed models OR ``model_dump`` dicts.
    """

    __slots__ = ("blocking_values",)

    def __init__(self, blocking_flags=None) -> None:
        flags = BLOCKING_FLAGS if blocking_flags is None else blocking_flags
        self.blocking_values = frozenset(_flag_value(f) for f in flags)

    @staticmethod
    def _is_degraded(run_status: Any) -> bool:
        """Read run-level DEGRADED from a RunHealthReport, its ``to_dict`` form,
        or a bare bool. (FAILED is intentionally not treated as degraded here —
        ``is_degraded`` is DEGRADED-only; a FAILED run blocking push is a
        documented follow-up.)"""
        if run_status is None:
            return False
        if isinstance(run_status, bool):
            return run_status
        return bool(_read(run_status, "is_degraded", False))

    def _blocking_names(self, flags: Any) -> list[str]:
        # Fail closed: a scalar flag (a bare string, or a single str-Enum TaskFlag)
        # is treated as one flag rather than iterated char-by-char — so a malformed
        # task can never let a blocking flag slip through a shape quirk.
        if isinstance(flags, str):
            flags = [flags]
        names = [_flag_value(f) for f in (flags or [])]
        return [n for n in names if n in self.blocking_values]

    def assert_pushable(self, tasks, run_status, *, override: bool = False) -> PushGateResult:
        degraded = self._is_degraded(run_status)
        result = PushGateResult()

        for task in tasks:
            task_id = str(_read(task, "id", "") or "")
            key = _read(task, "jira_issue_key", None)
            if key:
                decision = PushDecision(task_id, "skip", f"already pushed ({key})")
                result.skipped.append(decision)
                result.decisions.append(decision)
                continue

            names = self._blocking_names(_read(task, "flags", []))
            if degraded or names:
                parts = (["DEGRADED run"] if degraded else []) + ([",".join(names)] if names else [])
                reason = "; ".join(parts)
                if override:
                    decision = PushDecision(task_id, "push", f"override ({reason})")
                    result.pushable.append(task)
                else:
                    decision = PushDecision(task_id, "block", reason)
                    result.blocked.append(decision)
                result.decisions.append(decision)
                continue

            decision = PushDecision(task_id, "push", "")
            result.pushable.append(task)
            result.decisions.append(decision)

        if result.blocked and not override:
            raise PushBlocked(result)
        return result


# ─── verify primitives ───────────────────────────────────────────────────────

# finish_reason values that indicate the provider cut the response short by a
# token cap. Mirrors ``_TRUNCATION_FINISH_REASONS`` in pipeline/llm_client.py.
_TRUNCATION_FINISH_REASONS = {"length", "max_tokens", "max_output_tokens"}


def assert_work_done(
    input_count: int,
    change_count: int,
    threshold: int = 100,
) -> VerifyResult:
    """
    Verify that a large input actually produced *some* change.

    Fails (degraded) when ``input_count > threshold`` and ``change_count == 0``.
    This is the fingerprint of a stage that was handed a substantial workload but
    made zero edits — typically a truncated or failed LLM response that still
    parsed cleanly (see the dedup "work-was-done" signal, where
    ``DEGRADED_INPUT_THRESHOLD == 100`` and the comparison is strict ``>``).

    A small input that produces no change is fine (under the threshold there is
    no expectation of work), so it passes.
    """
    if input_count > threshold and change_count == 0:
        return VerifyResult(
            False,
            f"0 changes from {input_count} inputs (threshold {threshold}); "
            "possible truncated or failed response",
        )
    return VerifyResult(True, "")


def assert_finish_complete(finish_reason: Any) -> VerifyResult:
    """
    Verify that a model response was not cut short by a token cap.

    Fails (degraded) when ``finish_reason`` is one of ``length`` /
    ``max_tokens`` / ``max_output_tokens`` (case-insensitive, whitespace
    tolerant). A missing/empty ``finish_reason`` is treated as acceptable, exactly
    as ``pipeline/llm_client.py`` does.
    """
    if finish_reason is None:
        return VerifyResult(True, "")
    normalized = str(finish_reason).strip().lower()
    if normalized in _TRUNCATION_FINISH_REASONS:
        return VerifyResult(
            False,
            f"response truncated by token limit (finish_reason={finish_reason})",
        )
    return VerifyResult(True, "")


def assert_nonempty_when_expected(
    items: Sequence[Any] | Any,
    expected: bool,
) -> VerifyResult:
    """
    Verify that a result is non-empty whenever output was expected.

    Fails (degraded) when ``expected`` is True but ``items`` is falsy (empty list,
    empty dict, ``None``, etc.). When ``expected`` is False the check always
    passes — an empty result is legitimate.
    """
    if expected and not items:
        return VerifyResult(False, "expected non-empty output but got none")
    return VerifyResult(True, "")


__all__ = [
    "VerifyResult",
    "ConfidenceGate",
    "CoverageGate",
    "CoverageGateResult",
    "CoverageReportDecision",
    "PushGate",
    "PushGateResult",
    "PushDecision",
    "PushBlocked",
    "BLOCKING_FLAGS",
    "assert_work_done",
    "assert_finish_complete",
    "assert_nonempty_when_expected",
]
