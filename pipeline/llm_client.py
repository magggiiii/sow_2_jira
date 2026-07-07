# pipeline/llm_client.py

import contextlib
import datetime
import io
import json
import logging
import os
import random
import re
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Callable, Mapping, Optional

import litellm
from litellm import APIConnectionError, RateLimitError, Timeout
from rich.console import Console

from audit.logger import AuditLogger
from models.schemas import LLMMode, ProviderConfig
from pipeline.llm_router import configure_litellm_for_mode
from pipeline.observability import SYNC_ENABLED, llm_operation_duration, llm_token_usage, logger
from pipeline.telemetry import TelemetryEmitter

console = Console()


@dataclass
class RetryHint:
    source: str
    wait_seconds: float
    reason: str


class LLMTruncationError(RuntimeError):
    """Raised when a provider cut a response short at its token limit.

    finish_reason='length' means the model stopped because max_tokens was hit,
    so the body is partial (often invalid/partial JSON). We surface this as an
    explicit, non-retryable failure rather than silently returning truncated
    content. A MISSING finish_reason is treated as acceptable.
    """


# finish_reason values that indicate the response was cut short by a token cap.
_TRUNCATION_FINISH_REASONS = {"length", "max_tokens", "max_output_tokens"}


def _is_truncated_finish_reason(finish_reason) -> bool:
    if finish_reason is None:
        return False
    return str(finish_reason).strip().lower() in _TRUNCATION_FINISH_REASONS


def _is_non_retryable_llm_error(err: Exception) -> bool:
    text = str(err).lower()
    markers = [
        "invalid api key",
        "invalid_api_key",
        "token expired or incorrect",
        "provider not provided",
        "llm provider not provided",
        "insufficient balance",
        "no resource package",
        "unauthorized",
        "authentication",
        "401",
        "403",
    ]
    return any(marker in text for marker in markers)


def _is_cancelled_error(err: Exception) -> bool:
    return "cancelled by user" in str(err).lower()


def _sleep_with_cancel(total_s: float, stop_event) -> None:
    end = time.monotonic() + total_s
    while time.monotonic() < end:
        if stop_event and stop_event.is_set():
            raise RuntimeError("LLM call cancelled by user")
        time.sleep(0.2)


def _extract_headers(err: Exception) -> dict[str, str]:
    sources = []
    response = getattr(err, "response", None)
    if response is not None:
        sources.append(getattr(response, "headers", None))
    sources.append(getattr(err, "headers", None))
    sources.append(getattr(err, "response_headers", None))

    headers: dict[str, str] = {}
    for candidate in sources:
        if isinstance(candidate, Mapping):
            for k, v in candidate.items():
                try:
                    headers[str(k).lower()] = str(v)
                except Exception:
                    continue
    return headers


# Read a 3-digit code from a message ONLY when it is clearly an HTTP status —
# anchored to a "status"/"HTTP" cue. A bare number (token count, JSON column,
# list index) must not be mistaken for a status and drive a wrong retry decision.
# (Same rationale/pattern as core.errors._STATUS_IN_MESSAGE — kept in lockstep.)
_STATUS_IN_MESSAGE = re.compile(
    r"(?:status(?:[ _]?code)?|http)\D{0,4}(4\d\d|5\d\d)\b", re.IGNORECASE
)


def _extract_status_code(err: Exception) -> Optional[int]:
    code = getattr(err, "status_code", None)
    if isinstance(code, int):
        return code
    response = getattr(err, "response", None)
    response_code = getattr(response, "status_code", None)
    if isinstance(response_code, int):
        return response_code
    match = _STATUS_IN_MESSAGE.search(str(err))
    return int(match.group(1)) if match else None


def _parse_retry_after(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    raw = value.strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(raw)
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        delta = dt.timestamp() - time.time()
        return max(0.0, delta)
    except Exception:
        return None


def extract_retry_hint(error_or_response: Exception) -> Optional[RetryHint]:
    headers = _extract_headers(error_or_response)

    retry_after = _parse_retry_after(headers.get("retry-after"))
    if retry_after is not None:
        return RetryHint(
            source="retry-after-header",
            wait_seconds=retry_after,
            reason="Provider returned Retry-After header",
        )

    reset_keys = [
        "x-ratelimit-reset",
        "x-rate-limit-reset",
        "ratelimit-reset",
        "x-ratelimit-reset-requests",
    ]
    for key in reset_keys:
        value = headers.get(key)
        if not value:
            continue
        try:
            parsed = float(value.strip())
            if parsed > time.time() * 0.5:
                wait_s = max(0.0, parsed - time.time())
            else:
                wait_s = max(0.0, parsed)
            return RetryHint(
                source="x-ratelimit-reset",
                wait_seconds=wait_s,
                reason=f"Provider returned {key}",
            )
        except Exception:
            continue

    text = str(error_or_response).lower()
    msg_match = re.search(
        r"(retry after|try again in)\s+(\d+(?:\.\d+)?)\s*(seconds?|secs?|s|minutes?|mins?|m)?",
        text,
    )
    if msg_match:
        base = float(msg_match.group(2))
        unit = (msg_match.group(3) or "s").lower()
        if unit.startswith("m"):
            base *= 60.0
        return RetryHint(
            source="provider-message",
            wait_seconds=max(0.0, base),
            reason="Provider message contained retry delay hint",
        )

    return None


def compute_wait_seconds(retry_hint: Optional[RetryHint], attempt: int, max_wait_s: float) -> float:
    if retry_hint is not None:
        return min(max(retry_hint.wait_seconds, 0.0), max_wait_s)
    base = min(float(2 ** max(attempt - 1, 0)), max_wait_s)
    jitter = random.uniform(0.0, 1.0)
    return min(base + jitter, max_wait_s)


def is_retryable_remote_error(err: Exception) -> bool:
    if _is_non_retryable_llm_error(err) or _is_cancelled_error(err):
        return False

    status_code = _extract_status_code(err)
    if status_code is not None:
        if status_code in (429, 408):
            return True
        if 500 <= status_code <= 599:
            return True
        if status_code in (400, 401, 403, 404, 422):
            return False

    if isinstance(
        err, (RateLimitError, APIConnectionError, Timeout, TimeoutError, ConnectionError)
    ):
        return True

    text = str(err).lower()
    transient_markers = [
        "rate limit",
        "too many requests",
        "timeout",
        "timed out",
        "temporarily unavailable",
        "service unavailable",
        "connection reset",
        "try again",
    ]
    return any(marker in text for marker in transient_markers)

def _configure_litellm_logging():
    logging.getLogger("litellm").setLevel(logging.CRITICAL)
    logging.getLogger("LiteLLM").setLevel(logging.CRITICAL)

    class _LitellmNoiseFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            msg = record.getMessage()
            noisy_tokens = [
                "litellm.BadRequestError",
                "************* Retrying *************",
                "Remote LLM Call Active",
                "Give Feedback / Get Help",
                "LiteLLM.Info",
                "Provider List",
            ]
            return not any(token in msg for token in noisy_tokens)

    logging.getLogger().addFilter(_LitellmNoiseFilter())

    try:
        if hasattr(litellm, "suppress_debug_info"):
            litellm.suppress_debug_info = True
        if hasattr(litellm, "set_verbose"):
            try:
                litellm.set_verbose(False)
            except Exception:
                pass
        if hasattr(litellm, "verbose"):
            litellm.verbose = False
    except Exception:
        pass

@contextlib.contextmanager
def _suppress_litellm_output():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        yield


# ---------------------------------------------------------------------------
# Langfuse observability wrapper (optional dependency, strictly fail-open).
#
# Every generation MAY be reported to Langfuse (model, prompt/response, token
# usage, cost when available), keyed by run_id/agent. The import is lazy and
# guarded so langfuse stays optional, and DEFAULT OFF when unconfigured.
#
# Hard requirement: tracing NEVER affects the pipeline. If langfuse is missing,
# not configured (no LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY), or raises at
# any point, the LLM call proceeds and returns normally — no exception escapes.
#
# This block is intentionally self-contained: it does not touch provider/model
# routing, BIFROST_* handling, or os.environ credential writes.
# ---------------------------------------------------------------------------

# Module-level client cache. A truthy client means "configured + built OK";
# False is a sticky "not configured / build failed — don't retry" marker.
_LANGFUSE_CLIENT = None


def _reset_langfuse_client_cache() -> None:
    """Clear the cached Langfuse client (used by tests and reconfiguration)."""
    global _LANGFUSE_CLIENT
    _LANGFUSE_CLIENT = None


def _langfuse_is_configured() -> bool:
    """True only when both Langfuse keys are present in the environment.

    Default OFF: with either key missing, tracing is disabled entirely.
    """
    return bool(
        os.environ.get("LANGFUSE_PUBLIC_KEY")
        and os.environ.get("LANGFUSE_SECRET_KEY")
    )


def _get_langfuse_client():
    """Lazily import langfuse and build a cached client, or return None.

    Returns None when langfuse is absent, unconfigured, or fails to build.
    Never raises — resolution failures degrade to "tracing off".
    """
    global _LANGFUSE_CLIENT
    if _LANGFUSE_CLIENT is not None:
        # False is a sticky "unavailable" marker; a real client is truthy.
        return _LANGFUSE_CLIENT or None

    if not _langfuse_is_configured():
        _LANGFUSE_CLIENT = False
        return None

    try:
        from langfuse import Langfuse  # lazy/guarded optional import

        client = Langfuse(
            public_key=os.environ.get("LANGFUSE_PUBLIC_KEY"),
            secret_key=os.environ.get("LANGFUSE_SECRET_KEY"),
            host=os.environ.get("LANGFUSE_HOST") or None,
        )
        _LANGFUSE_CLIENT = client
        return client
    except Exception:
        # Missing dep, bad config, network/init failure — stay silent, fail open.
        _LANGFUSE_CLIENT = False
        return None


def _report_langfuse_generation(
    *,
    run_id: str,
    agent_name: str,
    model: str,
    prompt: str,
    system: str,
    response_text: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    node_id: str = "",
    cost: Optional[float] = None,
) -> None:
    """Report one LLM generation to Langfuse. Strictly fail-open.

    No-ops (without building a client) when unconfigured, and swallows every
    exception so tracing failures can never affect the LLM result.
    """
    try:
        # Default OFF: don't even attempt to build a client when unconfigured.
        if not _langfuse_is_configured():
            return

        client = _get_langfuse_client()
        if not client:
            return

        usage_details = {
            "input": int(prompt_tokens or 0),
            "output": int(completion_tokens or 0),
            "total": int(total_tokens or 0),
        }
        cost_details = {"total": float(cost)} if cost is not None else None

        observation = client.start_observation(
            name=f"llm.{agent_name}",
            as_type="generation",
            model=model,
            input={"system": system, "prompt": prompt},
            output=response_text,
            usage_details=usage_details,
            cost_details=cost_details,
            metadata={
                "run_id": run_id,
                "agent": agent_name,
                "node_id": node_id,
            },
        )

        # Close the observation if the client returned a handle for it.
        end = getattr(observation, "end", None)
        if callable(end):
            try:
                end()
            except Exception:
                pass
    except Exception:
        # Tracing must NEVER surface — swallow everything and keep the pipeline
        # result intact. Default OFF on any failure.
        return

class LLMClient:
    """
    Unified LLM client routing through LiteLLM.
    Ensures per-run configuration is used instead of global environment.
    """

    def __init__(
        self,
        mode: LLMMode,
        audit_logger: AuditLogger,
        run_id: str,
        provider_config: ProviderConfig = None,
        stop_event=None,
        status_callback: Optional[Callable] = None,
        cost_meter=None,
    ):
        _configure_litellm_logging()
        self.mode = mode
        self.audit_logger = audit_logger
        self.run_id = run_id
        self.stop_event = stop_event
        self.status_callback = status_callback
        # WAVE-7: optional run-scoped cost accumulator. When set, each real
        # completion feeds its (prompt_tokens, completion_tokens, model) usage
        # into the meter as a side effect so a budget kill-switch can trip.
        self.cost_meter = cost_meter

        # Use provided config or resolve from mode
        self.provider_config = provider_config or configure_litellm_for_mode(mode)
        self.model = self.provider_config.model
        self.telemetry = TelemetryEmitter()
        
        # Configure litellm to send traces to OpenTelemetry if Telemetry is enabled
        if SYNC_ENABLED:
            litellm.success_callback = ["opentelemetry"]
            litellm.failure_callback = ["opentelemetry"]

        # If using Bifrost (API mode), set the routing header
        self.extra_headers = {}
        if mode == LLMMode.API and os.environ.get("ZAI_API_KEY"):
            self.extra_headers = {"x-zai-api-key": os.environ["ZAI_API_KEY"]}
        elif mode == LLMMode.LOCAL and os.environ.get("OLLAMA_BASE_URL"):
            self.extra_headers = {"x-ollama-base-url": os.environ["OLLAMA_BASE_URL"]}

        # OpenRouter attribution: ranks usage on openrouter.ai dashboards/leaderboards
        if self.provider_config.provider == "openrouter":
            self.extra_headers["HTTP-Referer"] = os.environ.get(
                "OPENROUTER_REFERER", "https://github.com/magggiiii/sow_2_jira"
            )
            self.extra_headers["X-Title"] = os.environ.get("OPENROUTER_APP_NAME", "SOW-to-Jira")

    def complete(
        self,
        prompt: str,
        system: str = "You are a helpful assistant.",
        temperature: float = 0.0,
        max_tokens: int = 4096,
        agent_name: str = "unknown",
        node_id: str = "",
    ) -> str:
        """
        Send a completion request. Returns the response text.
        Logs token usage to audit log and sends traces to Tempo.
        """
        
        # infr-15c: the OTel tracing span was a no-op shim, so it is unwrapped;
        # the call runs directly under the correlated-logging context.
        with logger.contextualize(agent=agent_name, run_id=self.run_id, node_id=node_id):
            return self._execute_call(prompt, system, temperature, max_tokens, agent_name, node_id)

    def _execute_call(self, prompt, system, temperature, max_tokens, agent_name, node_id) -> str:
        logger.info(f"● Calling LLM ({self.model}) for agent {agent_name}")
        
        remote_max_attempts = int(
            os.getenv("LLM_REMOTE_MAX_ATTEMPTS", os.getenv("LLM_MAX_ATTEMPTS", "8"))
        )
        remote_max_elapsed_s = int(os.getenv("LLM_REMOTE_MAX_ELAPSED_S", "300"))
        remote_max_wait_s = int(os.getenv("LLM_REMOTE_MAX_WAIT_S", "300"))
        is_local_ollama = self.mode == LLMMode.LOCAL or str(self.model).startswith("ollama/")
        
        # Set a very long timeout for local models (Ollama)
        llm_timeout = 3600 if is_local_ollama else 60

        def _perform_one_call(attempt: int, start_time: float, local_wait: bool = False) -> str:
            action_verb = "Retrying" if attempt > 1 else "Calling"
            msg = f"{action_verb} {self.model} (Attempt {attempt}, timeout: {llm_timeout}s)"
            if local_wait:
                msg = (
                    f"{action_verb} local {self.model} "
                    f"(Attempt {attempt}, timeout: {llm_timeout}s) - "
                    "this may take several minutes..."
                )

            if self.status_callback:
                self.status_callback(msg)
            logger.info(f"› System: Request sent to {self.model} (Attempt {attempt})")

            kwargs = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "temperature": temperature,
                "max_tokens": max_tokens,
                "extra_headers": self.extra_headers,
            }

            if self.provider_config.api_key:
                kwargs["api_key"] = self.provider_config.api_key
            if self.provider_config.api_base:
                kwargs["api_base"] = self.provider_config.api_base

            req_start = time.time()
            with console.status(f"[bold cyan]{msg}[/]"):
                with _suppress_litellm_output():
                    response = litellm.completion(
                        **kwargs,
                        timeout=llm_timeout
                    )
            req_duration = time.time() - req_start
            logger.info(
                f"✓ System: Response received from {self.model} "
                f"in {req_duration:.2f} seconds"
            )

            content = response.choices[0].message.content or ""
            finish_reason = getattr(response.choices[0], "finish_reason", None)
            tokens = response.usage.total_tokens if response.usage else 0
            prompt_tokens = getattr(response.usage, "prompt_tokens", 0) if response.usage else 0
            completion_tokens = (
                getattr(response.usage, "completion_tokens", 0) if response.usage else 0
            )

            # WAVE-7: feed usage into the run-scoped cost meter (side effect only;
            # complete() still returns a bare str). Best-effort — never let a
            # metering failure surface into the pipeline result.
            if self.cost_meter is not None:
                try:
                    self.cost_meter.record(self.model, prompt_tokens, completion_tokens)
                except Exception:
                    pass

            # Record Argus Metrics (only if sync enabled)
            if SYNC_ENABLED:
                latency_s = time.time() - start_time
                llm_token_usage.add(
                    prompt_tokens, {"gen_ai.token.type": "input", "model": self.model}
                )
                llm_token_usage.add(
                    completion_tokens, {"gen_ai.token.type": "output", "model": self.model}
                )
                llm_operation_duration.record(latency_s, {"model": self.model})

            logger.success(f"✓ LLM Response received ({tokens} tokens)")
            self.telemetry.emit("llm.call", {
                "run_id": self.run_id,
                "agent": agent_name,
                "model": self.model,
                "tokens_in": prompt_tokens,
                "tokens_out": completion_tokens,
                "latency_ms": int((time.time() - start_time) * 1000),
                "success": True,
            })

            self.audit_logger.log(
                run_id=self.run_id,
                agent=agent_name,
                node_id=node_id,
                action="LLM_CALL",
                task_id=None,
                detail=f"model={self.model} tokens={tokens}",
                llm_tokens_used=tokens,
                llm_model=self.model,
            )

            # Optional Langfuse generation trace. Strictly fail-open: this helper
            # no-ops when unconfigured and swallows every exception, so tracing
            # can never affect the returned content.
            response_cost = getattr(response, "_hidden_params", None)
            cost = None
            if isinstance(response_cost, dict):
                cost = response_cost.get("response_cost")
            _report_langfuse_generation(
                run_id=self.run_id,
                agent_name=agent_name,
                model=self.model,
                prompt=prompt,
                system=system,
                response_text=content,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=tokens,
                node_id=node_id,
                cost=cost,
            )

            # A length/truncation finish_reason means the provider cut the
            # response short at its token cap, so the body is partial. Surface
            # this as an explicit failure instead of returning silently-
            # truncated content that downstream JSON parsing would mangle.
            if _is_truncated_finish_reason(finish_reason):
                logger.error(
                    f"✗ LLM response truncated by token limit (finish_reason={finish_reason}, "
                    f"max_tokens={max_tokens}) for agent {agent_name}"
                )
                raise LLMTruncationError(
                    f"LLM response truncated at token limit "
                    f"(finish_reason={finish_reason}, max_tokens={max_tokens}). "
                    f"Increase max_tokens for agent {agent_name}."
                )

            return content

        try:
            if is_local_ollama:
                logger.info("› Local Ollama mode: unlimited wait enabled; cancel anytime.")
                attempt = 0
                while True:
                    attempt += 1
                    if self.stop_event and self.stop_event.is_set():
                        logger.warning("› LLM call cancelled by user")
                        raise RuntimeError("LLM call cancelled by user")
                    start_time = time.time()
                    try:
                        return _perform_one_call(
                            attempt=attempt, start_time=start_time, local_wait=True
                        )
                    except LLMTruncationError:
                        raise
                    except Exception as e:
                        if _is_non_retryable_llm_error(e):
                            logger.error(f"✗ Non-retryable LLM error: {e}")
                            raise RuntimeError(f"Non-retryable LLM error: {e}") from e
                        if _is_cancelled_error(e):
                            raise
                        logger.warning(
                            f"› Local Ollama error: {e}. Retrying... (attempt {attempt})"
                        )
                        if self.status_callback:
                            self.status_callback(
                                f"Waiting for local {self.model} "
                                f"(Attempt {attempt+1}, timeout: {llm_timeout}s) - {e}"
                            )
                        _sleep_with_cancel(min(2 ** (attempt % 6), 30), self.stop_event)

            start_total = time.monotonic()
            attempt = 0
            while True:
                attempt += 1
                if self.stop_event and self.stop_event.is_set():
                    logger.warning("› LLM call cancelled by user")
                    raise RuntimeError("LLM call cancelled by user")

                start_time = time.time()
                try:
                    return _perform_one_call(
                        attempt=attempt, start_time=start_time, local_wait=False
                    )
                except LLMTruncationError:
                    raise
                except Exception as e:
                    if _is_non_retryable_llm_error(e):
                        logger.error(f"✗ Non-retryable LLM error: {e}")
                        raise RuntimeError(f"Non-retryable LLM error: {e}") from e
                    if _is_cancelled_error(e):
                        raise
                    if not is_retryable_remote_error(e):
                        raise RuntimeError(f"LLM call failed: {e}") from e

                    elapsed = time.monotonic() - start_total
                    if attempt >= remote_max_attempts or elapsed >= remote_max_elapsed_s:
                        raise RuntimeError(
                            f"Remote LLM retry budget exhausted after {attempt} "
                            f"attempts and {int(elapsed)}s: {e}"
                        ) from e

                    retry_hint = extract_retry_hint(e)
                    wait_s = compute_wait_seconds(retry_hint, attempt, remote_max_wait_s)
                    wait_source = retry_hint.source if retry_hint else "fallback"
                    wait_reason = (
                        retry_hint.reason if retry_hint else "Jittered exponential fallback"
                    )
                    provider_name = (
                        self.provider_config.provider if self.provider_config else "provider"
                    )

                    logger.warning(
                        f"› Remote retry #{attempt} for {self.model}: "
                        f"waiting {wait_s:.1f}s ({wait_source}) - {wait_reason}"
                    )
                    if self.status_callback:
                        self.status_callback(
                            f"Rate-limited by {provider_name}. "
                            f"Waiting {wait_s:.1f}s ({wait_source}): {wait_reason}"
                        )

                    self.telemetry.emit("llm.retry", {
                        "run_id": self.run_id,
                        "agent": agent_name,
                        "model": self.model,
                        "attempt": attempt,
                        "wait_source": wait_source,
                        "wait_seconds": float(round(wait_s, 2)),
                        "error_class": type(e).__name__,
                    })
                    _sleep_with_cancel(wait_s, self.stop_event)
        except Exception as e:
            logger.error(f"✗ LLM call permanently failed: {e}")
            self.telemetry.emit("llm.call", {
                "run_id": self.run_id,
                "agent": agent_name,
                "model": self.model,
                "tokens_in": 0,
                "tokens_out": 0,
                "latency_ms": 0,
                "success": False,
            })
            # Preserve the typed truncation error so callers can distinguish a
            # token-cap cutoff from a generic call failure.
            if isinstance(e, LLMTruncationError):
                raise
            raise RuntimeError(f"LLM call failed: {e}") from e

    def complete_json(
        self,
        prompt: str,
        system: str = "You are a precise JSON extraction assistant.",
        agent_name: str = "unknown",
        node_id: str = "",
        max_tokens: int = 8192,
    ) -> list | dict:
        """
        Like complete() but parses and returns JSON.
        Raises ValueError if response is not valid JSON.
        Raises LLMTruncationError (via complete) if the provider cut the
        response short at its token limit, instead of returning partial JSON.

        ``max_tokens`` defaults higher than the prior hard-coded 4096 to reduce
        token-cap truncation; callers extracting large structures may raise it.
        """
        raw = self.complete(
            prompt=prompt,
            system=system,
            temperature=0.0,
            max_tokens=max_tokens,
            agent_name=agent_name,
            node_id=node_id,
        )
        
        # Step 0: strip <think>...</think> reasoning blocks from thinking
        # models (Qwen3-thinking, DeepSeek-R1, Gemini "thinking mode", o1-style).
        # Some models emit a closing </think> with no opener — handle both.
        cleaned = re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL).strip()
        if cleaned.startswith('</think>'):
            cleaned = cleaned[len('</think>'):].lstrip()

        # Step 1: strip markdown fences anywhere in the response
        cleaned = re.sub(r'```(?:json)?\s*', '', cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r'```\s*$', '', cleaned, flags=re.MULTILINE).strip()

        # Step 2: strip any conversational preamble before the first [ or {
        # (handles "Here is the JSON:\n[..." patterns)
        match = re.search(r'[\[\{]', cleaned)
        if match:
            cleaned = cleaned[match.start():]

        # Step 3: strip trailing content after the last ] or }
        last_bracket = max(cleaned.rfind(']'), cleaned.rfind('}'))
        if last_bracket != -1:
            cleaned = cleaned[:last_bracket + 1]

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            with logger.contextualize(agent=agent_name, run_id=self.run_id, node_id=node_id):
                logger.error(f"Failed to parse LLM JSON: {e}")
                self.audit_logger.log(
                    run_id=self.run_id,
                    agent=agent_name,
                    action="json_parse_error",
                    detail=f"JSONDecodeError: {e} | Raw (first 500): {raw[:500]}",
                    node_id=node_id
                )
            raise ValueError(
                f"LLM returned unparseable JSON from {agent_name}. "
                f"Error: {e}. Raw start: {raw[:200]}"
            )
