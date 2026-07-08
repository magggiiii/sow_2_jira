"""Tests for the telemetry payload allowlist (WAVE 7 / SC-DEPLOY gate B2).

``pipeline.telemetry._scrub_payload`` must be an ALLOWLIST: only keys the
pipeline is known to emit pass through; anything unknown or sensitive is
dropped. Crucially, ``cost_usd`` (the LLM cost field the async-exec wave will
emit) MUST be allowed, and every key currently emitted by the live pipeline
MUST survive scrubbing so we never silently lose real telemetry.
"""

from pipeline.telemetry import ALLOWED_KEYS, _scrub_payload

# The keys the live pipeline actually emits today (grepped from every
# ``TelemetryEmitter.emit(...)`` call site in pipeline/orchestrator.py and
# pipeline/llm_client.py) plus the forward-compat cost field.
LIVE_EMIT_KEYS = {
    # llm.call
    "run_id",
    "agent",
    "model",
    "tokens_in",
    "tokens_out",
    "latency_ms",
    "success",
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
    # step.completed
    "step",
    "duration_ms",
    "node_count",
    "task_count",
    # run.completed
    "coverage_pct",
    # forward-compat: LLM cost (async-exec wave)
    "cost_usd",
}


def test_allowed_keys_is_superset_of_live_emit_keys():
    missing = LIVE_EMIT_KEYS - ALLOWED_KEYS
    assert not missing, f"ALLOWED_KEYS is missing live emit keys: {sorted(missing)}"


def test_cost_usd_is_allowed():
    assert "cost_usd" in ALLOWED_KEYS


def test_unknown_and_sensitive_keys_are_dropped():
    payload = {
        "run_id": "r-123",
        "cost_usd": 0.0421,
        # sensitive / unknown keys that must never leak through telemetry:
        "api_key": "sk-secret",
        "authorization": "Bearer nope",
        "section_title": "Statement of Work — confidential",
        "prompt": "full raw prompt text",
    }
    scrubbed = _scrub_payload(payload)
    assert scrubbed == {"run_id": "r-123", "cost_usd": 0.0421}


def test_all_live_keys_survive_scrubbing():
    payload = {k: "v" for k in LIVE_EMIT_KEYS}
    scrubbed = _scrub_payload(payload)
    assert set(scrubbed) == LIVE_EMIT_KEYS


def test_non_allowed_keys_dropped_regardless_of_value_type():
    # In an allowlist, a key not in ALLOWED_KEYS is dropped even when its value
    # is a dict or list that happens to contain allowed sub-keys.
    payload = {
        "run_id": "r-1",
        "nested": {"cost_usd": 1.0, "leak": "secret"},
        "items": [{"agent": "extractor", "leak": "x"}],
    }
    scrubbed = _scrub_payload(payload)
    assert scrubbed == {"run_id": "r-1"}
