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
import os
import time
from typing import Any, Optional, Type, TypeVar, Union

from pydantic import BaseModel, ValidationError

from core.agent_spec import AgentSpec
from core.ports import LLMProvider
from core.results import StageResult

T = TypeVar("T", bound=BaseModel)


def _env_int(name: str, default: int) -> int:
    """Read an int from the environment, falling back to ``default`` when unset
    or malformed (a bad value must never crash an agent mid-run)."""
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


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

    def _resolve_instructor_mode(self, instructor_mod):
        """
        Pick the instructor ``Mode`` for ``from_litellm``.

        ``from_litellm`` defaults to ``Mode.TOOLS`` (tool-calling). Under tool
        calling several providers — notably gemini via OpenRouter, confirmed on
        both flash and pro by the live smoke-gate — serialize nested
        ``list[Model]`` fields as STRINGIFIED JSON (``"{\\"title\\": …}"``),
        which then fails Pydantic with ``Input should be an object``. ``Mode.JSON``
        has the model emit one natural nested JSON object that round-trips, so we
        default to it. OpenAI/Anthropic/Azure tool-calling handles nested
        arguments reliably, so those keep ``Mode.TOOLS``.

        ``S2J_INSTRUCTOR_MODE`` (e.g. ``JSON``/``JSON_SCHEMA``/
        ``OPENROUTER_STRUCTURED_OUTPUTS``) overrides the choice for experiments;
        an unknown value is ignored. This is a serialization tuning knob, not a
        credential/router path.
        """
        Mode = instructor_mod.Mode
        override = (os.environ.get("S2J_INSTRUCTOR_MODE") or "").strip()
        if override and hasattr(Mode, override):
            return getattr(Mode, override)

        provider_config = getattr(self.llm, "provider_config", None)
        provider = (getattr(provider_config, "provider", "") or "").lower()
        if provider in ("openai", "anthropic", "azure"):
            return Mode.TOOLS
        return Mode.JSON

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

        Resilience
        ----------
        The provider call carries a per-call ``timeout`` (60s remote / 3600s
        local Ollama, override ``S2J_STRUCTURED_TIMEOUT``) and is wrapped in a
        bounded retry/backoff loop that REUSES the legacy ``complete_json``
        helpers (``is_retryable_remote_error`` / ``extract_retry_hint`` /
        ``compute_wait_seconds``). Transient remote errors (429/408/5xx/network)
        are retried with ``Retry-After``-aware jittered exponential backoff up to
        ``LLM_STRUCTURED_MAX_ATTEMPTS`` (8) attempts / ``LLM_STRUCTURED_MAX_ELAPSED_S``
        (300) seconds, each wait capped at ``LLM_STRUCTURED_MAX_WAIT_S`` (300).
        Instructor's own validation-retry (for unsatisfiable *schemas*) is
        orthogonal to this provider-error retry and the two compose.

        Raises
        ------
        InstructorError
            On a non-retryable instructor/litellm call failure (auth/4xx), on a
            validation failure instructor could not satisfy, after the transient
            retry budget is exhausted, or if the model cannot be resolved. Never
            returns ``None``.
        """
        model = self._resolve_model()

        # Lazy imports: keep litellm/instructor out of module import time so the
        # complete_json seam the agents use stays free of network deps. The
        # retry/backoff helpers are REUSED from the legacy ``complete_json`` path
        # (import-only, never modified) so structured output regains the exact
        # resilience semantics ``_execute_call`` already proved in production.
        try:
            import instructor
            import litellm

            from pipeline.llm_client import (
                compute_wait_seconds,
                extract_retry_hint,
                is_retryable_remote_error,
            )
        except Exception as e:  # pragma: no cover - import wiring guard
            raise InstructorError(
                f"complete_structured: structured-output dependencies unavailable: {e}"
            ) from e

        messages: list[dict[str, str]] = []
        if system is not None:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        # Per-call timeout so a hung provider call can never block the run
        # indefinitely. Mirrors ``_execute_call``: 60s remote, 3600s for local
        # Ollama (slow generation), overridable via ``S2J_STRUCTURED_TIMEOUT``.
        mode = getattr(self.llm, "mode", None)
        is_local_ollama = (
            str(model).startswith("ollama/") or getattr(mode, "value", None) == "local"
        )
        timeout = _env_int("S2J_STRUCTURED_TIMEOUT", 3600 if is_local_ollama else 60)

        create_kwargs: dict[str, Any] = {
            "model": model,
            "response_model": response_model,
            "max_tokens": max_tokens if max_tokens is not None else 8192,
            "messages": messages,
            "timeout": timeout,
        }

        # Forward the per-run provider credentials the SAME way ``complete_json``
        # does in ``LLMClient._execute_call``: the model string alone is not
        # enough. ``configure_litellm_for_mode`` resolves api_key/api_base onto
        # ``provider_config`` (e.g. a decrypted OpenRouter key from settings),
        # and ``LLMClient`` may build ``extra_headers`` (OpenRouter attribution,
        # Bifrost/Ollama routing). Instructor forwards unknown kwargs straight to
        # ``litellm.completion``, so we pass them through here. Each is set ONLY
        # when present — an empty value would clobber litellm's own env-based
        # resolution. Without this, litellm finds no credentials and returns 401
        # ("No cookie auth credentials found"). We do NOT resolve credentials
        # here (that stays in the llm_router/LLMClient layer); we only pass the
        # already-resolved values through.
        provider_config = getattr(self.llm, "provider_config", None)
        api_key = getattr(provider_config, "api_key", "") or ""
        api_base = getattr(provider_config, "api_base", "") or ""
        extra_headers = getattr(self.llm, "extra_headers", None)
        if api_key:
            create_kwargs["api_key"] = api_key
        if api_base:
            create_kwargs["api_base"] = api_base
        if extra_headers:
            create_kwargs["extra_headers"] = extra_headers

        client = instructor.from_litellm(
            litellm.completion, mode=self._resolve_instructor_mode(instructor)
        )

        # Bounded retry/backoff around the provider call. Only transient remote
        # errors (429 / 408 / 5xx / network timeouts) are retried — auth/4xx and
        # instructor's unsatisfiable-schema failures are non-retryable and fail
        # fast. Budget via env (attempts / total elapsed / per-wait ceiling); on
        # exhaustion we re-raise a typed InstructorError (callers already catch
        # it and degrade), never a silent None. Instructor's own validation-retry
        # is orthogonal — it retries unsatisfiable *schemas*, this retries
        # provider *errors* — and the two compose.
        max_attempts = _env_int("LLM_STRUCTURED_MAX_ATTEMPTS", 8)
        max_elapsed_s = _env_int("LLM_STRUCTURED_MAX_ELAPSED_S", 300)
        max_wait_s = _env_int("LLM_STRUCTURED_MAX_WAIT_S", 300)

        start_total = time.monotonic()
        attempt = 0
        while True:
            attempt += 1
            try:
                result = client.chat.completions.create(**create_kwargs)
                break
            except Exception as e:
                # instructor raises its own ValidationError-wrapping/InstructorRetry
                # error on unsatisfiable schemas, and litellm raises on call
                # failures. A non-retryable error (auth/4xx/validation) fails fast.
                if not is_retryable_remote_error(e):
                    raise InstructorError(
                        f"complete_structured failed for agent "
                        f"{agent_name or 'unknown'} (model={model}): {e}"
                    ) from e

                elapsed = time.monotonic() - start_total
                if attempt >= max_attempts or elapsed >= max_elapsed_s:
                    raise InstructorError(
                        f"complete_structured exhausted retry budget for agent "
                        f"{agent_name or 'unknown'} (model={model}) after "
                        f"{attempt} attempts and {int(elapsed)}s: {e}"
                    ) from e

                retry_hint = extract_retry_hint(e)
                wait_s = compute_wait_seconds(retry_hint, attempt, max_wait_s)
                time.sleep(wait_s)

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

    # ─── Spec-driven structured run (validated Pydantic instance) ─────────────

    def run_structured(
        self,
        spec: AgentSpec,
        payload: Any,
        *,
        node_id: str = "",
    ) -> Any:
        """
        Render ``spec`` against ``payload`` and return a VALIDATED
        ``spec.response_model`` instance via :meth:`complete_structured`.

        The structured-output complement to :meth:`run`: where ``run`` uses the
        bare ``complete_json`` seam and returns a :class:`StageResult`,
        ``run_structured`` uses the instructor-validated ``complete_structured``
        path the six extraction-pipeline agents rely on and returns the model
        instance directly.

        It forwards EXACTLY the kwargs those agents pass today — ``prompt`` (from
        ``spec.render(payload)``), ``response_model``, ``system``, ``agent_name``,
        ``node_id``, and ``max_tokens`` only when ``spec.max_tokens`` is set — so
        an agent that builds a spec and calls this is behavior-identical to the
        inline ``complete_structured`` call it replaces. Prompt-render errors and
        any :class:`InstructorError` from the provider propagate unchanged (the
        agents keep their own ``try/except InstructorError`` around the call).
        """
        kwargs: dict[str, Any] = {
            "prompt": spec.render(payload),
            "response_model": spec.response_model,
            "system": spec.system_prompt,
            "agent_name": spec.name,
            "node_id": node_id,
        }
        if spec.max_tokens is not None:
            kwargs["max_tokens"] = spec.max_tokens
        return self.complete_structured(**kwargs)


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
