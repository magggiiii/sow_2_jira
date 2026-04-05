# pipeline/llm_client.py

import os
import json
import re
import time
from typing import Union, Optional, Callable
import litellm
from litellm import RateLimitError, APIConnectionError, Timeout
import logging
import contextlib
import io
from tenacity import retry, stop_never, wait_exponential, retry_if_exception_type, before_sleep_log
from models.schemas import LLMMode, ProviderConfig
from pipeline.llm_router import configure_litellm_for_mode
from audit.logger import AuditLogger
from pipeline.observability import logger, tracer, llm_token_usage, llm_operation_duration, INSTANCE_ID, SYNC_ENABLED
from pipeline.telemetry import TelemetryEmitter
from rich.console import Console

console = Console()

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

class LLMClient:
    """
    Unified LLM client routing through LiteLLM.
    Ensures per-run configuration is used instead of global environment.
    """

    def __init__(self, mode: LLMMode, audit_logger: AuditLogger, run_id: str, provider_config: ProviderConfig = None, stop_event=None, status_callback: Optional[Callable] = None):
        _configure_litellm_logging()
        self.mode = mode
        self.audit_logger = audit_logger
        self.run_id = run_id
        self.stop_event = stop_event
        self.status_callback = status_callback

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
        
        # Setup context for when SYNC is disabled
        if not SYNC_ENABLED:
            with logger.contextualize(agent=agent_name, run_id=self.run_id, node_id=node_id):
                return self._execute_call(prompt, system, temperature, max_tokens, agent_name, node_id, None)

        with tracer.start_as_current_span(f"LLM_CALL_{agent_name}") as span:
            span.set_attribute("agent", agent_name)
            span.set_attribute("node_id", node_id)
            span.set_attribute("model", self.model)
            span.set_attribute("prompt_preview", prompt[:1000])

            with logger.contextualize(agent=agent_name, run_id=self.run_id, node_id=node_id):
                return self._execute_call(prompt, system, temperature, max_tokens, agent_name, node_id, span)

    def _execute_call(self, prompt, system, temperature, max_tokens, agent_name, node_id, span) -> str:
        logger.info(f"● Calling LLM ({self.model}) for agent {agent_name}")
        
        # Set a very long timeout for local models (Ollama)
        llm_timeout = 300 if self.mode == LLMMode.LOCAL else 60

        @retry(
            stop=stop_never,
            wait=wait_exponential(multiplier=2, min=4, max=60),
            retry=retry_if_exception_type((RateLimitError, APIConnectionError, Timeout, Exception)),
            before_sleep=before_sleep_log(logging.getLogger("pipeline"), logging.WARNING),
            reraise=True
        )
        def _do_call():
            if self.stop_event and self.stop_event.is_set():
                logger.warning("› LLM call cancelled by user")
                raise RuntimeError("LLM call cancelled by user")

            start_time = time.time()
            
            # Update UI message
            if self.status_callback:
                self.status_callback(f"Waiting for {self.model} response...")

            try:
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

                with console.status(f"Waiting for LLM ({self.model})..."):
                    logger.info(f"  ... waiting for {self.model} response (timeout: {llm_timeout}s)")
                    with _suppress_litellm_output():
                        response = litellm.completion(
                            **kwargs,
                            timeout=llm_timeout
                        )
                content = response.choices[0].message.content or ""
                tokens = response.usage.total_tokens if response.usage else 0
                prompt_tokens = getattr(response.usage, "prompt_tokens", 0) if response.usage else 0
                completion_tokens = getattr(response.usage, "completion_tokens", 0) if response.usage else 0

                if span:
                    span.set_attribute("tokens", tokens)
                    span.set_attribute("response_preview", content[:1000])
                
                # Record Argus Metrics (only if sync enabled)
                if SYNC_ENABLED:
                    latency_s = time.time() - start_time
                    llm_token_usage.add(prompt_tokens, {"gen_ai.token.type": "input", "argus.instance_id": INSTANCE_ID, "model": self.model})
                    llm_token_usage.add(completion_tokens, {"gen_ai.token.type": "output", "argus.instance_id": INSTANCE_ID, "model": self.model})
                    llm_operation_duration.record(latency_s, {"argus.instance_id": INSTANCE_ID, "model": self.model})

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
                return content

            except Exception as e:
                logger.warning(f"› LLM attempt failed: {e}")
                if self.status_callback:
                    self.status_callback(f"LLM Error: Retrying {self.model}...")
                raise e

        try:
            return _do_call()
        except Exception as e:
            if span:
                span.record_exception(e)
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
            raise RuntimeError(f"LLM call failed: {e}") from e

    def complete_json(
        self,
        prompt: str,
        system: str = "You are a precise JSON extraction assistant.",
        agent_name: str = "unknown",
        node_id: str = "",
    ) -> list | dict:
        """
        Like complete() but parses and returns JSON.
        Raises ValueError if response is not valid JSON.
        """
        raw = self.complete(
            prompt=prompt,
            system=system,
            temperature=0.0,
            max_tokens=4096,
            agent_name=agent_name,
            node_id=node_id,
        )
        
        # Step 1: strip markdown fences anywhere in the response
        cleaned = re.sub(r'```(?:json)?\s*', '', raw, flags=re.IGNORECASE).strip()
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
