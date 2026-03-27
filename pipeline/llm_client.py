# pipeline/llm_client.py

import os
import json
import re
from typing import Union
import litellm
from models.schemas import LLMMode
from pipeline.llm_router import configure_litellm_for_mode
from audit.logger import AuditLogger
from pipeline.observability import logger, tracer

class LLMClient:
    """
    Unified LLM client routing through Maxim Bifrost.
    Bifrost handles model switching and tracing automatically.
    Both API and local modes use the same OpenAI-compatible interface.
    """

    def __init__(self, mode: LLMMode, audit_logger: AuditLogger, run_id: str):
        self.mode = mode
        self.audit_logger = audit_logger
        self.run_id = run_id

        # Use the router to determine model string and inject env vars
        self.model = configure_litellm_for_mode(mode)
        
        # Configure litellm to send traces to OpenTelemetry if Telemetry is enabled
        if os.environ.get("BIFROST_TELEMETRY_URL"):
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
        import time

        with tracer.start_as_current_span(f"LLM_CALL_{agent_name}") as span:
            span.set_attribute("agent", agent_name)
            span.set_attribute("node_id", node_id)
            span.set_attribute("model", self.model)
            span.set_attribute("prompt_preview", prompt[:1000])

            with logger.contextualize(agent=agent_name, run_id=self.run_id, node_id=node_id):
                logger.info(f"Calling LLM ({self.model}) for agent {agent_name}")
                
                for attempt in range(3):
                    try:
                        # Extract optional custom API params
                        custom_api_key = os.environ.get("LITELLM_API_KEY") if self.mode == LLMMode.CUSTOM else None
                        custom_api_base = os.environ.get("LITELLM_API_BASE") if self.mode == LLMMode.CUSTOM else None
                        
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
                        
                        if custom_api_key:
                            kwargs["api_key"] = custom_api_key
                        if custom_api_base:
                            kwargs["api_base"] = custom_api_base

                        response = litellm.completion(**kwargs)
                        content = response.choices[0].message.content or ""
                        tokens = response.usage.total_tokens if response.usage else 0

                        span.set_attribute("tokens", tokens)
                        span.set_attribute("response_preview", content[:1000])
                        
                        logger.success(f"LLM Response received ({tokens} tokens)")

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
                        logger.warning(f"LLM attempt {attempt+1} failed: {e}")
                        if attempt == 2:
                            span.record_exception(e)
                            logger.error(f"LLM call permanently failed: {e}")
                            raise RuntimeError(
                                f"LLM call failed after 3 attempts: {e}"
                            ) from e
                        time.sleep(2 ** attempt)

        return ""  # unreachable

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
