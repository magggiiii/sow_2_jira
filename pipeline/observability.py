# pipeline/observability.py

import os
import re
import sys
import base64
import functools
import contextlib

from loguru import logger

# ─── OTel/Traceloop: LAZY & OPTIONAL ────────────────────────────────────────
# OpenTelemetry / Traceloop are the R4 removal target. This module must import
# and run correctly even when they are UNINSTALLED. All opentelemetry/traceloop
# imports live INSIDE init_argus() (and its helpers), guarded by try/except
# ImportError. When absent, we fall back to the no-op shims defined below.
#
# NOTE: intentionally NO top-level `import opentelemetry` / `import traceloop`.

# ─── ARGUS BACKBONE ─────────────────────────────────────────────────────────
# Two destinations supported:
#   1. Local Argus Edge Collector at ARGUS_COLLECTOR_URL (the legacy path).
#   2. Langfuse Cloud directly when LANGFUSE_PUBLIC_KEY + LANGFUSE_SECRET_KEY
#      are set — no collector required. Useful for solo/small-team setups
#      that don't want to run the full Argus HQ deck.

# Toggle for remote synchronization (defaults to OFF). Auto-enabled when
# Langfuse Cloud keys are present so users don't have to flip both flags.
LANGFUSE_PUBLIC_KEY = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY = os.environ.get("LANGFUSE_SECRET_KEY", "")
LANGFUSE_BASE_URL = os.environ.get("LANGFUSE_BASE_URL", "https://us.cloud.langfuse.com").rstrip("/")
LANGFUSE_ENABLED = bool(LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY)

SYNC_ENABLED = (
    os.environ.get("ARGUS_SYNC_ENABLED", "false").lower() == "true"
    or LANGFUSE_ENABLED
)

def resolve_collector_endpoint() -> str:
    """
    Resolves the OTLP trace endpoint.

    Priority:
      1. ARGUS_COLLECTOR_URL when set — local OTel collector handles fan-out
         to Tempo / Langfuse / Loki with its own filters.
      2. Langfuse Cloud HTTP endpoint when LANGFUSE_PUBLIC_KEY/SECRET_KEY are
         present and no collector is configured — direct, no fan-out.
      3. localhost:4317 fallback.
    """
    explicit = os.environ.get("ARGUS_COLLECTOR_URL", "").strip()
    if explicit:
        return explicit
    if LANGFUSE_ENABLED:
        return f"{LANGFUSE_BASE_URL}/api/public/otel/v1/traces"
    return "http://localhost:4317"


def _use_local_collector() -> bool:
    """True iff ARGUS_COLLECTOR_URL is set — i.e. we're routing through the
    local OTel collector instead of direct-to-Langfuse."""
    return bool(os.environ.get("ARGUS_COLLECTOR_URL", "").strip())

# Instance ID for Argus identifying this specific user/installation
INSTANCE_ID = os.environ.get("SOW_INSTANCE_ID", "unknown-instance")

# ────────────────────────────────────────────────────────────────────────────

# Resolve Global Data Path
DATA_DIR = os.environ.get("SOW_DATA_DIR", "data")
os.makedirs(DATA_DIR, exist_ok=True)

# Centrally managed for consistent labeling across traces/logs/events
DEFAULT_JOB_NAME = "sow-to-jira"


# ─── NO-OP SHIMS (used when OTel absent, or when sync is disabled) ───────────
# These mirror the tiny slice of the OTel API that live importers touch:
#   tracer.start_as_current_span(name) -> context manager yielding a span
#   span.set_attribute(key, value)
#   counter.add(amount, attributes=None)
#   histogram.record(value, attributes=None)

class _NoOpSpan:
    """A span that accepts attribute sets and does nothing."""
    def set_attribute(self, *args, **kwargs):
        return None

    def add_event(self, *args, **kwargs):
        return None

    def record_exception(self, *args, **kwargs):
        return None

    def set_status(self, *args, **kwargs):
        return None


class _NoOpTracer:
    """A tracer whose start_as_current_span returns a no-op span context."""
    @contextlib.contextmanager
    def start_as_current_span(self, name, *args, **kwargs):
        yield _NoOpSpan()


class _NoOpInstrument:
    """A metric instrument with working (no-op) .add() and .record()."""
    def add(self, amount, attributes=None, *args, **kwargs):
        return None

    def record(self, value, attributes=None, *args, **kwargs):
        return None


class _NoOpMeter:
    """A meter that hands back no-op instruments."""
    def create_counter(self, *args, **kwargs):
        return _NoOpInstrument()

    def create_histogram(self, *args, **kwargs):
        return _NoOpInstrument()

    def create_up_down_counter(self, *args, **kwargs):
        return _NoOpInstrument()


# Default (safe) globals. init_argus() may replace these with real OTel objects
# when SYNC_ENABLED and the SDK is importable.
meter = _NoOpMeter()
tracer = _NoOpTracer()
llm_token_usage = meter.create_counter(
    name="gen_ai.client.token.usage",
    description="Number of tokens used per request",
    unit="1",
)
llm_operation_duration = meter.create_histogram(
    name="gen_ai.client.operation.duration",
    description="Total time for the client operation",
    unit="s",
)

# Set by init_argus() when the real OTel stack is live; used by log formatters
# to fetch the active trace id. Stays None when OTel is absent/disabled.
_otel_trace_api = None


def init_argus(service_name: str = "sow-to-jira"):
    """
    Initializes Argus Observability (best-effort, fully optional):
      1. Traceloop (OpenLLMetry) for LLM Traces & Spans
      2. OTel Logging instrumentation
      3. OTel Metrics

    All opentelemetry/traceloop imports happen HERE, lazily and guarded by
    try/except ImportError. If sync is disabled OR the SDK is not installed,
    the module keeps its no-op tracer/meter/instruments and returns quietly.
    """
    global meter, tracer, llm_token_usage, llm_operation_duration, _otel_trace_api

    if not SYNC_ENABLED:
        logger.info("Argus remote sync is disabled (default-off). Skipping OTel initialization.")
        return

    # Lazy, optional OTel imports. If any are missing, keep the no-op shims.
    try:
        from traceloop.sdk import Traceloop
        from opentelemetry import trace, metrics
        from opentelemetry.sdk.resources import SERVICE_NAME
        from opentelemetry.instrumentation.logging import LoggingInstrumentor
    except ImportError as exc:
        logger.warning(
            f"OpenTelemetry/Traceloop not installed ({exc}); "
            "running with no-op telemetry shims."
        )
        return

    endpoint = resolve_collector_endpoint()
    exporter = None

    if _use_local_collector():
        # Send everything to the local OTel collector via gRPC. The collector
        # is responsible for fan-out: all spans → Tempo, LLM-only → Langfuse,
        # logs → Loki. No filtering in app code.
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
        logger.info(f"Routing OTel traces to local collector at {endpoint}")
    elif LANGFUSE_ENABLED:
        # No local collector — send directly to Langfuse Cloud OTLP/HTTP.
        # Beware: this sends ALL spans (incl. FastAPI + orchestration), making
        # the Langfuse dashboard noisy. Prefer the local-collector path.
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        auth = base64.b64encode(f"{LANGFUSE_PUBLIC_KEY}:{LANGFUSE_SECRET_KEY}".encode()).decode()
        exporter = OTLPSpanExporter(
            endpoint=endpoint,
            headers={"Authorization": f"Basic {auth}"},
        )
        logger.warning(
            f"Direct-to-Langfuse mode (no collector). All spans go to {LANGFUSE_BASE_URL}; "
            "for LLM-only filtering, run the local collector and set ARGUS_COLLECTOR_URL."
        )
    else:
        # Legacy: gRPC to whatever endpoint resolve_collector_endpoint returned.
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)

    Traceloop.init(
        app_name=service_name,
        exporter=exporter,
        telemetry_enabled=False,  # disable Traceloop platform reporting
        resource_attributes={
            SERVICE_NAME: service_name,
            "argus.instance_id": INSTANCE_ID,
        }
    )

    # 2. Instrument Logging
    # Automatically injects trace_id and span_id into log records
    LoggingInstrumentor().instrument(set_logging_format=True)

    # 3. Setup Metrics
    # (Resource attributes are applied to Traceloop above via resource_attributes.)
    # Metrics are automatically exported if OTLP_EXPORTER is configured in env
    # For now, we rely on the Traceloop/OTel default env vars:
    # OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317

    # Swap the no-op shims for real OTel objects now that the SDK is live.
    _otel_trace_api = trace
    tracer = trace.get_tracer(DEFAULT_JOB_NAME)
    meter = metrics.get_meter(DEFAULT_JOB_NAME)
    llm_token_usage = meter.create_counter(
        name="gen_ai.client.token.usage",
        description="Number of tokens used per request",
        unit="1",
    )
    llm_operation_duration = meter.create_histogram(
        name="gen_ai.client.operation.duration",
        description="Total time for the client operation",
        unit="s",
    )

    logger.info(f"Argus initialized for instance: {INSTANCE_ID}")


def _current_otel_trace_id() -> str:
    """Best-effort active OTel trace id as a 32-hex string.

    Returns 'disabled' when sync is off, and the zero-trace when OTel is live
    but there's no active span. Never raises — falls back to 'disabled' if the
    OTel API is unavailable for any reason.
    """
    if not SYNC_ENABLED or _otel_trace_api is None:
        return "disabled"
    try:
        span = _otel_trace_api.get_current_span()
        if span and span.get_span_context().is_valid:
            return format(span.get_span_context().trace_id, "032x")
        return "0" * 32
    except Exception:
        return "disabled"


# Initialize Argus (only if enabled; safe when OTel absent)
init_argus()

# ─── Secret Redaction ───────────────────────────────────────────────────────
# Redact secret-looking values from log records BEFORE they hit any sink
# (stdout + audit.jsonl + run files). Applied via logger.patch so it runs for
# every emitted record regardless of sink.

REDACTED = "***REDACTED***"

# key=value / key: value where key looks sensitive (api_key, token, secret,
# password, authorization, etc.). Captures the value after the delimiter.
_SECRET_KV_RE = re.compile(
    r"(?i)\b("
    r"api[-_]?key|apikey|access[-_]?token|refresh[-_]?token|token|secret|"
    r"password|passwd|pwd|authorization|auth|bearer|client[-_]?secret|"
    r"private[-_]?key"
    r")"
    r"(\s*[:=]\s*|\s+)"
    r"(\"?)([^\s,;\"']+)"
)

# Long high-entropy-ish blobs (>= 20 chars of base64/hex/token characters).
# Deliberately conservative so ordinary words/UUIDs-in-prose survive.
_HIGH_ENTROPY_RE = re.compile(r"\b[A-Za-z0-9_\-]{32,}\b")


def _looks_high_entropy(token: str) -> bool:
    """Heuristic: mixes character classes and is long enough to be a key/token,
    not an English word or a dotted path."""
    if len(token) < 32:
        return False
    has_lower = any(c.islower() for c in token)
    has_upper = any(c.isupper() for c in token)
    has_digit = any(c.isdigit() for c in token)
    classes = sum([has_lower, has_upper, has_digit])
    return classes >= 2


def _redact_text(value: str) -> str:
    """Redact secret-looking substrings from a string."""
    if not value or REDACTED in value:
        return value

    def _kv_sub(m):
        key, delim, quote = m.group(1), m.group(2), m.group(3)
        return f"{key}{delim}{quote}{REDACTED}"

    redacted = _SECRET_KV_RE.sub(_kv_sub, value)
    redacted = _HIGH_ENTROPY_RE.sub(
        lambda m: REDACTED if _looks_high_entropy(m.group(0)) else m.group(0),
        redacted,
    )
    return redacted


def _redact_patcher(record):
    """Loguru patcher: scrub secrets from the message and any string extras
    before the record reaches any sink."""
    try:
        record["message"] = _redact_text(record["message"])
        extra = record.get("extra")
        if extra:
            for k, v in list(extra.items()):
                if isinstance(v, str):
                    extra[k] = _redact_text(v)
    except Exception:
        # Never let redaction break logging.
        pass


# ─── Loguru Configuration ───────────────────────────────────────────────────
logger.remove()

def otel_log_format(record):
    """Format that includes OTel trace ID if synchronization is enabled."""
    record["extra"]["otelTraceID"] = _current_otel_trace_id()
    return "<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{extra[agent]}</cyan> - <level>{message}</level>\n"

# Base context: agent + run_id + user_id, all defaulted. user_id defaults to
# 'none' and is overridden per-run via run_logger(...).
logger.configure(extra={"agent": "system", "run_id": "none", "user_id": "none"})

# Apply the secret-redaction patcher globally (stdout + all file sinks).
logger = logger.patch(_redact_patcher)

# Standard Console Output
logger.add(sys.stdout, colorize=True, format=otel_log_format)

# Permanent Local Logs (System & Run-specific)
LOG_FILE = os.path.join(DATA_DIR, "system.log")
logger.add(
    LOG_FILE,
    rotation="10 MB",
    retention="10 days",
    compression="zip",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[run_id]} | {extra[agent]} | [trace={extra[otelTraceID]}] | {message}"
)

# Permanent LOCAL JSON AUDIT (regardless of sync toggle). Carries both user_id
# and run_id in the serialized record.extra.
AUDIT_JSON_FILE = os.path.join(DATA_DIR, "audit.jsonl")
logger.add(
    AUDIT_JSON_FILE,
    serialize=True,
    rotation="50 MB",
    retention="30 days",
    filter=lambda record: record["level"].name != "DEBUG"  # Keep audit clean
)

def add_run_file_logger(run_id: str):
    run_log_dir = os.path.join(DATA_DIR, "logs")
    os.makedirs(run_log_dir, exist_ok=True)
    run_log_path = os.path.join(run_log_dir, f"run_{run_id}.log")

    def run_log_format(record):
        record["extra"]["otelTraceID"] = _current_otel_trace_id()
        return "{time:HH:mm:ss} | {level: <8} | {extra[agent]} | [trace={extra[otelTraceID]}] | {message}\n"

    return logger.add(
        run_log_path,
        format=run_log_format,
        filter=lambda record: record["extra"].get("run_id") == run_id
    )

@contextlib.contextmanager
def run_logger(run_id: str, user_id: str = "none"):
    """Context manager that adds a run-specific file logger and binds the
    run_id (and user_id) into the loguru context so both land in the JSON
    audit record.extra. Ensures the file handler is removed on exit."""
    handler_id = add_run_file_logger(run_id)
    try:
        with logger.contextualize(run_id=run_id, user_id=user_id):
            yield
    finally:
        logger.remove(handler_id)

def trace_span(name: str, agent: str = "system", run_id: str = "none"):
    """
    Decorator for tracing a function.
    If SYNC_ENABLED, uses active OTel tracer.
    If disabled, simply executes the function with loguru context.

    Works as a pass-through decorator whether or not OTel is installed: the
    module-level `tracer` is either the real OTel tracer or a no-op that yields
    a no-op span, so the `with tracer.start_as_current_span(...)` path is always
    safe.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            if SYNC_ENABLED:
                with tracer.start_as_current_span(name) as span:
                    span.set_attribute("agent", agent)
                    span.set_attribute("run_id", run_id)
                    span.set_attribute("argus.instance_id", INSTANCE_ID)
                    with logger.contextualize(agent=agent, run_id=run_id):
                        return func(*args, **kwargs)
            else:
                with logger.contextualize(agent=agent, run_id=run_id):
                    return func(*args, **kwargs)
        return wrapper
    return decorator

# Legacy sync_telemetry is now a no-op as Argus Edge Collector handles it
def sync_telemetry():
    pass
