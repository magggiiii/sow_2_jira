# core/agent_runner.py
"""
``AgentRunner`` — the behavior-preserving adapter that routes an agent's single
``complete_json`` call through one place.

The runner is constructed with an :class:`core.ports.LLMProvider` (structurally,
``pipeline.llm_client.LLMClient`` satisfies it). It offers two entrypoints:

* :meth:`complete_json` — a THIN passthrough with the exact same signature and
  return value as ``LLMProvider.complete_json`` today. An existing agent can
  swap ``self.llm.complete_json(...)`` for ``self.runner.complete_json(...)``
  with no observable change (same parsed list/dict, same exceptions). This is
  the seam the routing steps use.

* :meth:`run` — a richer wrapper that renders an :class:`~core.agent_spec.AgentSpec`
  prompt, calls the provider, optionally validates the parsed JSON into a
  Pydantic ``response_model``, and returns a uniform
  :class:`core.results.StageResult` (``ok`` on success, ``failed`` on parse or
  validation error). New code that wants the typed-result contract uses this.

Crucially, ``run`` calls ``complete_json`` with the SAME kwargs the agents pass
today (``prompt``, ``system``, ``agent_name``, ``node_id``, and ``max_tokens``
only when the spec sets it). It does not invent temperature or other kwargs, so
routing an agent through it cannot change the underlying request.

Additive module — importing it pulls in only ``core.*`` and ``pydantic`` (for
the ``ValidationError`` type). No litellm/jira/network imports.
"""

from __future__ import annotations

import json
from typing import Any, Optional, Union

from pydantic import ValidationError

from core.agent_spec import AgentSpec
from core.ports import LLMProvider
from core.results import StageResult


class AgentRunner:
    """
    Routes a single model call through the :class:`LLMProvider` port.

    Parameters
    ----------
    llm:
        Anything satisfying :class:`core.ports.LLMProvider` (e.g. an
        ``LLMClient`` instance, or a fake in tests).
    """

    def __init__(self, llm: LLMProvider) -> None:
        self.llm = llm

    # ─── Thin passthrough (behavior-preserving seam) ──────────────────────────

    def complete_json(
        self,
        prompt: str,
        system: str = "You are a precise JSON extraction assistant.",
        agent_name: str = "unknown",
        node_id: str = "",
        max_tokens: int = 8192,
    ) -> Union[list, dict]:
        """
        Identical surface to ``LLMProvider.complete_json``.

        Forwards every argument unchanged and returns the provider's result
        verbatim (a parsed ``list`` or ``dict``). Exceptions raised by the
        provider (``ValueError`` on unparseable JSON, ``LLMTruncationError`` /
        ``RuntimeError`` on call failure) propagate unchanged. An agent can
        route its existing call through this method with no behavior change.
        """
        return self.llm.complete_json(
            prompt=prompt,
            system=system,
            agent_name=agent_name,
            node_id=node_id,
            max_tokens=max_tokens,
        )

    # ─── Spec-driven run (typed StageResult contract) ─────────────────────────

    def run(
        self,
        spec: AgentSpec,
        payload: Any,
        node_id: str = "",
    ) -> StageResult:
        """
        Render ``spec`` against ``payload``, call the LLM, and return a
        :class:`StageResult`.

        Behavior
        --------
        - The prompt is produced by ``spec.render(payload)``.
        - ``complete_json`` is called with ``system=spec.system_prompt`` (only
          when the spec sets one — otherwise the provider default applies),
          ``agent_name=spec.name``, ``node_id=node_id``, and ``max_tokens`` only
          when ``spec.max_tokens`` is set. These are exactly the kwargs the
          agents pass today, so the underlying request is unchanged.
        - On a provider error (call failure / unparseable JSON) → ``failed``.
        - If ``spec.response_model`` is set, the parsed JSON is validated via
          ``model_validate``; a ``ValidationError`` → ``failed``. The validated
          model is placed in ``StageResult.output``.
        - Otherwise the parsed JSON (list/dict) is placed in ``output``.

        The ``agent`` field of the result is set to ``spec.name``. If the
        provider surfaces a normalized ``LLMResult`` (rather than a bare
        list/dict), its ``finish_reason``/token counts are carried onto the
        result; bare list/dict providers (today's ``LLMClient``) simply omit
        those metrics.
        """
        # 1. Render the prompt.
        try:
            prompt = spec.render(payload)
        except Exception as e:  # rendering is caller-data-driven; never crash
            return StageResult.failed(
                f"prompt render error: {e}", agent=spec.name
            )

        # 2. Build the call kwargs to MATCH the agents' current call exactly.
        kwargs: dict[str, Any] = {
            "prompt": prompt,
            "agent_name": spec.name,
            "node_id": node_id,
        }
        if spec.system_prompt is not None:
            kwargs["system"] = spec.system_prompt
        if spec.max_tokens is not None:
            kwargs["max_tokens"] = spec.max_tokens

        # 3. Call the provider.
        try:
            raw = self.llm.complete_json(**kwargs)
        except Exception as e:
            return StageResult.failed(f"llm call failed: {e}", agent=spec.name)

        # 4. Normalize: a provider may hand back either a bare parsed JSON
        #    (today's LLMClient) or a richer LLMResult-like object carrying
        #    finish_reason/tokens. Pull metrics out if present.
        parsed, metrics = _split_result(raw)

        # 5. Optional validation into a response_model.
        if spec.response_model is not None:
            try:
                validated = spec.response_model.model_validate(parsed)
            except ValidationError as e:
                return StageResult.failed(
                    f"response validation failed: {e}",
                    agent=spec.name,
                    **metrics,
                )
            except Exception as e:  # non-Pydantic model or odd payload
                return StageResult.failed(
                    f"response validation error: {e}",
                    agent=spec.name,
                    **metrics,
                )
            return StageResult.ok(validated, agent=spec.name, **metrics)

        return StageResult.ok(parsed, agent=spec.name, **metrics)


def _split_result(raw: Any) -> tuple[Any, dict[str, Any]]:
    """
    Separate the parsed JSON payload from any optional LLM metrics.

    Today's ``LLMClient.complete_json`` returns a bare ``list``/``dict`` — in
    that case there are no metrics and the payload passes through untouched.

    If a provider instead returns an object exposing ``content`` plus
    ``finish_reason`` / ``prompt_tokens`` / ``completion_tokens`` / ``usd_cost``
    (the :class:`core.results.LLMResult` shape), we parse ``content`` as JSON
    and carry the metrics onto the StageResult. This keeps ``run`` forward-
    compatible without changing today's behavior.
    """
    # The common, current case: a bare parsed JSON value.
    if isinstance(raw, (list, dict)):
        return raw, {}

    content = getattr(raw, "content", None)
    if content is not None:
        try:
            parsed = json.loads(content)
        except (TypeError, ValueError):
            # Leave content as-is; let validation/consumer decide.
            parsed = content
        metrics: dict[str, Any] = {}
        fr = getattr(raw, "finish_reason", "")
        if fr:
            metrics["finish_reason"] = fr
        ti = getattr(raw, "prompt_tokens", 0)
        if ti:
            metrics["tokens_in"] = ti
        to = getattr(raw, "completion_tokens", 0)
        if to:
            metrics["tokens_out"] = to
        cost = getattr(raw, "usd_cost", 0.0)
        if cost:
            metrics["cost_usd"] = cost
        return parsed, metrics

    # Unknown shape — pass through unchanged with no metrics.
    return raw, {}


__all__ = ["AgentRunner"]
