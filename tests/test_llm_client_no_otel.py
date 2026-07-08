# tests/test_llm_client_no_otel.py
"""WAVE 4 — the dead OpenTelemetry residue in pipeline/llm_client.py is removed.

observability.py was fully de-OTel'd in the Argus decommission: its tracer/meter
objects are PERMANENT no-op shims and ``SYNC_ENABLED`` now aliases
``LANGFUSE_ENABLED``. Two inert OTel call sites nonetheless lingered in
``LLMClient``:

  * ``litellm.success_callback = ["opentelemetry"]`` / ``failure_callback`` —
    a footgun that asks litellm to emit OTel traces with no exporter wired.
  * ``llm_token_usage.add(...)`` / ``llm_operation_duration.record(...)`` — calls
    into the no-op meter shims that do nothing.

These tests lock in that both are gone and that the now-unused observability
symbols are no longer imported, so the file cannot silently regrow the residue.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LLM_CLIENT = REPO_ROOT / "pipeline" / "llm_client.py"


def _source() -> str:
    return LLM_CLIENT.read_text()


def test_no_opentelemetry_litellm_callback():
    """The inert litellm OTel success/failure callback assignment is deleted.

    Scoped to the exact dead patterns rather than a whole-file 'opentelemetry'
    scan, which would spuriously fail on any future comment mentioning the word.
    """
    src = _source()
    assert 'success_callback = ["opentelemetry"]' not in src
    assert 'failure_callback = ["opentelemetry"]' not in src


def test_no_noop_meter_calls_or_imports():
    """The no-op OTel meter shims are neither called nor imported (deduped)."""
    src = _source()
    assert "llm_token_usage" not in src
    assert "llm_operation_duration" not in src
    assert "SYNC_ENABLED" not in src


def test_llm_client_still_imports_and_constructs():
    """Behavioral guard: removing the dead code does not break construction.

    ``logger`` remains wired and a client builds with an explicit ProviderConfig
    (no litellm global mutation from the deleted OTel branch).
    """
    from audit.logger import AuditLogger
    from models.schemas import LLMMode, ProviderConfig
    from pipeline.llm_client import LLMClient

    client = LLMClient(
        mode=LLMMode.API,
        audit_logger=AuditLogger(),
        run_id="otel-cleanup-test",
        provider_config=ProviderConfig(provider="openai", model="openai/gpt-4o"),
    )
    assert client.model == "openai/gpt-4o"
