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

A third, OPTIONAL entrypoint :meth:`complete_structured` uses the ``instructor``
library over ``litellm`` to return a *validated* Pydantic instance directly
(instructor handles the JSON-mode request and the ``model_validate`` round-trip,
retrying the model on its own when validation fails). It is purely additive and
is NOT wired into any agent — agents keep calling ``complete_json``. It exists
so future agents can opt into provider-native structured output without
re-implementing the strip/parse/validate dance in ``complete_json``.

Additive module — ``complete_json`` / ``run`` pull in only ``core.*`` and
``pydantic``. ``complete_structured`` lazily imports ``instructor``/``litellm``
*inside the method* so that importing this module (and the seam the agents use)
never drags in litellm/network unless structured output is actually requested.
"""

from __future__ import annotations

import json
from typing import Any, Optional, Type, TypeVar, Union

from pydantic import BaseModel, ValidationError

from core.agent_spec import AgentSpec
from core.ports import LLMProvider
from core.results import StageResult


T = TypeVar("T", bound=BaseModel)


class InstructorError(RuntimeError):
    """Raised when instructor-backed structured output cannot be produced.

    Wraps any failure of :meth:`AgentRunner.complete_structured` — an
    instructor/litellm call error, a validation failure that instructor could
    not satisfy, or an unresolvable model — so callers get one explicit, typed
    failure instead of a silent ``None``. The original cause is chained via
    ``raise ... from`` so the underlying provider/validation error is preserved.
    """


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

    # ─── Instructor-backed structured output (validated Pydantic) ─────────────

    def _resolve_model(self) -> str:
        """
        Resolve the litellm model string the same way ``LLMClient`` does.

        ``LLMClient.__init__`` sets ``self.model = self.provider_config.model``
        (from :func:`pipeline.llm_router.configure_litellm_for_mode`). We read
        the provider's ``model`` attribute first (the value an ``LLMClient``
        instance actually uses), falling back to ``provider_config.model`` if a
        provider only exposes the config. Raises if neither yields a usable
        model string so we never silently call litellm with an empty model.
        """
        model = getattr(self.llm, "model", None)
        if not model:
            provider_config = getattr(self.llm, "provider_config", None)
            model = getattr(provider_config, "model", None)
        if not model:
            raise InstructorError(
                "complete_structured: could not resolve a model from the LLM "
                "provider (no .model or .provider_config.model). The provider "
                "must expose the resolved litellm model string."
            )
        return str(model)

    def complete_structured(
        self,
        *,
        prompt: str,
        response_model: Type[T],
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
        agent_name: Optional[str] = None,
        node_id: Optional[str] = None,
    ) -> T:
        """
        Return a VALIDATED ``response_model`` instance via instructor + litellm.

        Unlike ``complete_json`` (which returns a bare list/dict the caller then
        validates itself), this asks the provider for structured output and lets
        ``instructor`` enforce the schema: it builds a client over
        ``litellm.completion`` — inheriting the SAME global litellm config the
        ``LLMClient`` already configures (api_key/api_base/etc. resolved via
        ``configure_litellm_for_mode``) — and calls
        ``client.chat.completions.create(..., response_model=response_model)``.
        The returned object is an already-``model_validate``-d instance of
        ``response_model``.

        Parameters
        ----------
        prompt:
            The user-turn content.
        response_model:
            A Pydantic v2 model class to validate the model output into.
        system:
            Optional system-turn content. Omitted from the messages when None.
        max_tokens:
            Output token cap. Defaults to 8192 (payload-sized, matching
            ``complete_json``'s default) when None.
        agent_name / node_id:
            Carried for parity with the other entrypoints; unused by the call
            itself but accepted so a future agent's call site matches
            ``complete_json``.

        Returns
        -------
        An instance of ``response_model`` (validated).

        Raises
        ------
        InstructorError
            On any instructor/litellm call failure, on a validation failure
            instructor could not satisfy, or if the model cannot be resolved.
            Never returns ``None``.
        """
        model = self._resolve_model()

        # Lazy imports: keep litellm/instructor out of module import time so the
        # complete_json seam the agents use stays free of network deps.
        try:
            import instructor
            import litellm
        except Exception as e:  # pragma: no cover - import wiring guard
            raise InstructorError(
                f"complete_structured: instructor/litellm unavailable: {e}"
            ) from e

        messages: list[dict[str, str]] = []
        if system is not None:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        try:
            client = instructor.from_litellm(litellm.completion)
            result = client.chat.completions.create(
                model=model,
                response_model=response_model,
                max_tokens=max_tokens if max_tokens is not None else 8192,
                messages=messages,
            )
        except Exception as e:
            # instructor raises its own ValidationError-wrapping/InstructorRetry
            # error on unsatisfiable schemas, and litellm raises on call
            # failures. Collapse both into one typed, non-silent error.
            raise InstructorError(
                f"complete_structured failed for agent "
                f"{agent_name or 'unknown'} (model={model}): {e}"
            ) from e

        # Defensive: instructor should always hand back a validated instance,
        # but never let a None / wrong-type slip through silently.
        if not isinstance(result, response_model):
            raise InstructorError(
                "complete_structured: instructor returned "
                f"{type(result).__name__}, expected {response_model.__name__}"
            )
        return result

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


__all__ = ["AgentRunner", "InstructorError"]
