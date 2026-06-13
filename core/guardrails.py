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

from typing import Any, NamedTuple, Sequence
from typing import Literal


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
    "assert_work_done",
    "assert_finish_complete",
    "assert_nonempty_when_expected",
]
