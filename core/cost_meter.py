# core/cost_meter.py
"""WAVE-7 CostMeter — a run-scoped LLM spend accumulator + budget kill-switch.

The meter is threaded FROM the :class:`~pipeline.llm_client.LLMClient` (not read
from ``audit.db`` mid-run): after every real completion the client feeds the
response usage (``prompt_tokens``, ``completion_tokens``, ``model``) into the
meter as a side effect. Cumulative USD is computed via
``litellm.cost_per_token`` — deliberately NOT ``litellm.completion_cost`` — so
pricing stays a pure token→dollar function with no network or response object
required.

The orchestrator builds a meter only when a per-run budget is configured, then
gates :meth:`exceeded` inside its existing ``_cancelled()`` checkpoints so an
over-budget run aborts cleanly via the existing cancellation return path.
"""

from __future__ import annotations

import threading

import litellm

from pipeline.observability import logger


class CostMeter:
    """Accumulate (prompt_tokens, completion_tokens) per model and expose a
    cumulative-USD budget kill-switch.

    Args:
        max_run_cost: Per-run budget in USD. Once cumulative cost strictly
            exceeds this value, :meth:`exceeded` returns ``True``.
    """

    def __init__(self, max_run_cost: float):
        self.max_run_cost = float(max_run_cost)
        # Per-model token tallies: model -> [prompt_tokens, completion_tokens].
        self._tokens: dict[str, list[int]] = {}
        self._cost: float = 0.0
        self._lock = threading.Lock()

    def record(self, model: str, prompt_tokens: int, completion_tokens: int) -> None:
        """Record one completion's token usage and fold its cost into the total.

        Best-effort: a pricing failure (unknown model, etc.) is swallowed so the
        meter never breaks a run — an unpriceable call simply adds $0.
        """
        pt = int(prompt_tokens or 0)
        ct = int(completion_tokens or 0)
        if pt == 0 and ct == 0:
            return

        try:
            prompt_cost, completion_cost = litellm.cost_per_token(
                model=model,
                prompt_tokens=pt,
                completion_tokens=ct,
            )
            call_cost = float(prompt_cost) + float(completion_cost)
        except Exception as e:  # pragma: no cover - defensive
            logger.debug(f"CostMeter: could not price model={model}: {e}")
            call_cost = 0.0

        with self._lock:
            tally = self._tokens.setdefault(model, [0, 0])
            tally[0] += pt
            tally[1] += ct
            self._cost += call_cost

    def total_cost(self) -> float:
        """Cumulative USD recorded so far."""
        with self._lock:
            return self._cost

    def exceeded(self) -> bool:
        """True once cumulative cost strictly exceeds the configured budget."""
        with self._lock:
            return self._cost > self.max_run_cost
