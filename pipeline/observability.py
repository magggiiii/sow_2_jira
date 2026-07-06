# pipeline/observability.py

import os
import re
import sys
import functools
import contextlib

from loguru import logger

# ─── REMOTE TELEMETRY SYNC: RETIRED ─────────────────────────────────────────
# Remote tracing/metrics export (the legacy collector + direct-to-Langfuse
# OTLP paths) has been decommissioned. This module no longer imports any remote
# tracing/metrics SDK anywhere — not at module top, not lazily inside
# init_argus(). Observability now comes from two places only:
#   1. Local loguru sinks (stdout + system.log + audit.jsonl + per-run files),
#      configured below and always on.
#   2. The DIRECT Langfuse Cloud SDK path, which lives in pipeline/llm_client.py
#      and reads LANGFUSE_HOST itself — it does NOT touch any symbol here.
#
# The tracer/meter/instrument objects below are PERMANENT no-op shims; they are
# never swapped for real OTel objects. SYNC_ENABLED is retained as an importable
# bool (ui/server.py + llm_client.py import the name) and still reflects whether
# Langfuse Cloud credentials are present.

# Langfuse Cloud credentials still drive SYNC_ENABLED. When both keys are set,
# the direct Langfuse SDK path in llm_client.py is active.
LANGFUSE_PUBLIC_KEY = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY = os.environ.get("LANGFUSE_SECRET_KEY", "")
LANGFUSE_ENABLED = bool(LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY)

# Toggle reflecting whether Langfuse Cloud observability is configured. Kept as
# an importable module-level bool for ui/server.py + llm_client.py + tests.
SYNC_ENABLED = LANGFUSE_ENABLED

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

def init_argus(service_name: str = "sow-to-jira"):
    """
    Retired remote-sync initializer (kept for API compatibility).

    Remote tracing/metrics export has been decommissioned, so this is
    now a log-only no-op. The module keeps its permanent no-op tracer/meter/
    instrument shims; local loguru sinks and the direct Langfuse Cloud SDK path
    (in pipeline/llm_client.py) provide all observability. Signature is
    unchanged so existing callers (ui/server.py, main.py) keep working.
    """
    logger.info(
        "Remote tracing/metrics sync is retired; using local loguru sinks + "
        "the direct Langfuse Cloud SDK for observability."
    )
    return


def _current_otel_trace_id() -> str:
    """Trace-id shim: remote OTel is retired, so there is no active trace id.

    Always returns 'disabled'. Retained (and still stamped into the log
    formats) so the log-format surface is unchanged from when OTel was live.
    """
    return "disabled"


# Retired remote sync — log-only no-op, safe to call unconditionally.
init_argus()

# ─── Secret Redaction ───────────────────────────────────────────────────────
# Redact secret-looking values from log records BEFORE they hit any sink
# (stdout + audit.jsonl + run files). Applied via logger.patch so it runs for
# every emitted record regardless of sink.

REDACTED = "***REDACTED***"

# key=value / key: value where key looks sensitive (api_key, token, secret,
# password, authorization, etc.). Captures the value after the delimiter.
#
# Two deliberate design choices, each fixing a prior redaction defect:
#
#   1. The key may carry an arbitrary word-char PREFIX joined by [-_] (e.g.
#      JIRA_API_TOKEN, LITELLM_API_KEY, client_secret). The old `\b(token)`
#      anchor could not match inside API_TOKEN because the '_' before 'token'
#      is a word char, so no word boundary existed — the value leaked. We now
#      allow `(?:[A-Za-z0-9]+[-_])*` before the sensitive keyword so the whole
#      env-var name matches and its value gets scrubbed.
#
#   2. The delimiter is a REQUIRED ':' or '=' (with optional surrounding
#      whitespace). A bare space is NOT a delimiter. The old `|\s+` alternative
#      meant any trigger word followed by a space redacted the next token,
#      corrupting ordinary prose ("auth failed for user bob") and, worse,
#      UUID run_id/session_id correlation keys ("session <uuid> started").
_SECRET_KV_RE = re.compile(
    r"(?i)\b("
    r"(?:[a-z0-9]+[-_])*"
    r"(?:api[-_]?key|apikey|access[-_]?token|refresh[-_]?token|token|secret|"
    r"password|passwd|pwd|authorization|auth|client[-_]?secret|"
    r"private[-_]?key)"
    r")"
    # Delimiter tolerates quotes around the key and the value so JSON/dict forms
    # ("api_key": "v", 'password'='v') are matched, not just bare key=value.
    r"([\"']?\s*[:=]\s*[\"']?)"
    r"([^\s,;\"']+)"
)

# HTTP auth schemes: scrub the credential after Bearer/Basic — covers a bare
# "Bearer <jwt>" and the value of an "Authorization: Bearer <jwt>" header. The
# {12,}-char floor keeps ordinary prose ("bearer of good news") intact.
_AUTH_SCHEME_RE = re.compile(r"(?i)\b(bearer|basic)\s+([A-Za-z0-9._~+/=-]{12,})")

# Long high-entropy-ish blobs (>= 32 chars of base64/hex/token characters).
# Deliberately conservative so ordinary words/UUIDs-in-prose survive.
_HIGH_ENTROPY_RE = re.compile(r"\b[A-Za-z0-9_\-]{32,}\b")

# Canonical UUID (run_id / session_id — the primary debugging correlation key).
# These are hex+hyphen and would otherwise trip the high-entropy heuristic
# (lowercase hex letters + digits = 2 char classes). Never redact them.
_UUID_RE = re.compile(
    r"(?i)^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def _looks_high_entropy(token: str) -> bool:
    """Heuristic: mixes character classes and is long enough to be a key/token,
    not an English word, a dotted path, or a UUID correlation id."""
    if len(token) < 32:
        return False
    # Preserve UUID run_id/session_id — the primary debugging correlation key.
    if _UUID_RE.match(token):
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

    # 1) HTTP auth schemes first, so the token after Bearer/Basic is scrubbed
    #    before the key=value pass could grab only the scheme word and leave the
    #    credential behind.
    redacted = _AUTH_SCHEME_RE.sub(lambda m: f"{m.group(1)} {REDACTED}", value)

    # 2) key=value / key: value, tolerating quotes (JSON/dict secret forms).
    redacted = _SECRET_KV_RE.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", redacted
    )

    # 3) long high-entropy fallback (UUID correlation ids preserved).
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
                    with logger.contextualize(agent=agent, run_id=run_id):
                        return func(*args, **kwargs)
            else:
                with logger.contextualize(agent=agent, run_id=run_id):
                    return func(*args, **kwargs)
        return wrapper
    return decorator

# Legacy sync_telemetry: permanent no-op now that remote sync is retired.
def sync_telemetry():
    pass
