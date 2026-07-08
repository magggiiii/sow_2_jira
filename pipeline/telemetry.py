# pipeline/telemetry.py

import json
from typing import Any, Dict

from pipeline.observability import logger

# Allowlist of telemetry payload keys that are safe to emit. This is the
# inverse of the old denylist: instead of dropping one known-bad key and
# passing everything else, we pass ONLY these known-good keys so a future
# emit call site can never leak a raw prompt, credential, or SOW section
# title by accident. Every key here is a value the pipeline actually emits
# today (grepped from every TelemetryEmitter.emit(...) call site in
# pipeline/orchestrator.py and pipeline/llm_client.py), plus ``cost_usd``,
# the per-call LLM cost the async-execution wave will start emitting.
ALLOWED_KEYS = frozenset(
    {
        # correlation / identity (safe, non-secret)
        "run_id",
        "agent",
        "model",
        # llm.call metrics
        "tokens_in",
        "tokens_out",
        "latency_ms",
        "success",
        "cost_usd",
        # llm.retry
        "attempt",
        "wait_source",
        "wait_seconds",
        "error_class",
        # run.started
        "llm_mode",
        "jira_hierarchy",
        "max_nodes",
        "filename",
        # step.completed / run.completed
        "step",
        "duration_ms",
        "node_count",
        "task_count",
        "coverage_pct",
    }
)


def _scrub_payload(payload: Any) -> Any:
    """Allowlist-scrub a telemetry payload.

    Only keys in :data:`ALLOWED_KEYS` survive; any other key (unknown or
    sensitive) is dropped entirely, regardless of its value's type. Allowed
    values that are themselves dicts/lists are scrubbed recursively so nested
    structures follow the same allowlist rule.
    """
    if isinstance(payload, dict):
        return {
            k: _scrub_payload(v) for k, v in payload.items() if k in ALLOWED_KEYS
        }
    if isinstance(payload, list):
        return [_scrub_payload(v) for v in payload]
    return payload

class TelemetryEmitter:
    """
    Legacy telemetry emitter, now routes structured events through the
    centralized loguru logger so they flow to the platform log stream and the
    local JSON audit sink.
    """
    def emit(self, event_name: str, payload: Dict[str, Any]) -> None:
        scrubbed = _scrub_payload(payload)

        # We just log it as an INFO level structured log.
        # It is picked up by the loguru sinks (stdout + local JSON audit).
        logger.bind(event=event_name, payload=scrubbed).info(
            f"Telemetry Event: {event_name} | {json.dumps(scrubbed)}"
        )
