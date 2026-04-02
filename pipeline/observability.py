# pipeline/observability.py

import os
import sys
import time
import functools
import contextlib
from typing import Optional

import logging
from loguru import logger
from traceloop.sdk import Traceloop
from opentelemetry import trace, metrics
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.instrumentation.logging import LoggingInstrumentor

# ─── ARGUS BACKBONE ─────────────────────────────────────────────────────────
# Dynamic resolution for Argus central observability
# The app talks to the local Argus Edge Collector (sidecar).

# Toggle for remote Argus synchronization (defaults to OFF)
SYNC_ENABLED = os.environ.get("ARGUS_SYNC_ENABLED", "false").lower() == "true"

def resolve_collector_endpoint() -> str:
    """
    Resolves the local OTel Collector endpoint.
    By default, it expects the collector sidecar on localhost:4317.
    """
    return os.environ.get("ARGUS_COLLECTOR_URL", "http://localhost:4317")

# Instance ID for Argus identifying this specific user/installation
INSTANCE_ID = os.environ.get("SOW_INSTANCE_ID", "unknown-instance")

# ────────────────────────────────────────────────────────────────────────────

# Resolve Global Data Path
DATA_DIR = os.environ.get("SOW_DATA_DIR", "data")
os.makedirs(DATA_DIR, exist_ok=True)

# Centrally managed for consistent labeling across traces/logs/events
DEFAULT_JOB_NAME = "sow-to-jira"

def init_argus(service_name: str = "sow-to-jira"):
    """
    Initializes Argus Observability:
    1. Traceloop (OpenLLMetry) for LLM Traces & Spans
    2. OTel Logging instrumentation
    3. OTel Metrics
    """
    if not SYNC_ENABLED:
        logger.info("Argus remote sync is disabled (default-off). Skipping OTel initialization.")
        return

    # 1. Initialize Traceloop (OpenLLMetry)
    # It handles OTLP export to the endpoint specified in TRACELOOP_BASE_URL or OTLP defaults
    Traceloop.init(
        app_name=service_name,
        disable_reports=True, # We send via OTLP, not Traceloop's platform
        exporter_args={
            "endpoint": resolve_collector_endpoint(),
        },
        resource_attributes={
            SERVICE_NAME: service_name,
            "argus.instance_id": INSTANCE_ID,
        }
    )

    # 2. Instrument Logging
    # Automatically injects trace_id and span_id into log records
    LoggingInstrumentor().instrument(set_logging_format=True)

    # 3. Setup Metrics
    resource = Resource(attributes={
        SERVICE_NAME: service_name,
        "argus.instance_id": INSTANCE_ID
    })
    
    # Metrics are automatically exported if OTLP_EXPORTER is configured in env
    # For now, we rely on the Traceloop/OTel default env vars:
    # OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317

    logger.info(f"Argus initialized for instance: {INSTANCE_ID}")

# Global Metrics Instruments (only used if SYNC_ENABLED)
meter = metrics.get_meter(DEFAULT_JOB_NAME)
llm_token_usage = meter.create_counter(
    name="gen_ai.client.token.usage",
    description="Number of tokens used per request",
    unit="1"
)
llm_operation_duration = meter.create_histogram(
    name="gen_ai.client.operation.duration",
    description="Total time for the client operation",
    unit="s"
)

# Initialize Argus (only if enabled)
init_argus()

# ─── Loguru Configuration ───────────────────────────────────────────────────
logger.remove()

def otel_log_format(record):
    """Format that includes OTel trace ID if synchronization is enabled."""
    if SYNC_ENABLED:
        span = trace.get_current_span()
        if span and span.get_span_context().is_valid:
            record["extra"]["otelTraceID"] = format(span.get_span_context().trace_id, "032x")
        else:
            record["extra"]["otelTraceID"] = "0" * 32
    else:
        record["extra"]["otelTraceID"] = "disabled"
    
    return "<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{extra[agent]}</cyan> - <level>{message}</level>\n"

# Standard Console Output
logger.configure(extra={"agent": "system", "run_id": "none"})
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

# Permanent LOCAL JSON AUDIT (regardless of sync toggle)
AUDIT_JSON_FILE = os.path.join(DATA_DIR, "audit.jsonl")
logger.add(
    AUDIT_JSON_FILE, 
    serialize=True, 
    rotation="50 MB", 
    retention="30 days",
    filter=lambda record: record["level"].name != "DEBUG" # Keep audit clean
)

def add_run_file_logger(run_id: str):
    run_log_dir = os.path.join(DATA_DIR, "logs")
    os.makedirs(run_log_dir, exist_ok=True)
    run_log_path = os.path.join(run_log_dir, f"run_{run_id}.log")
    
    def run_log_format(record):
        if SYNC_ENABLED:
            span = trace.get_current_span()
            if span and span.get_span_context().is_valid:
                record["extra"]["otelTraceID"] = format(span.get_span_context().trace_id, "032x")
            else:
                record["extra"]["otelTraceID"] = "0" * 32
        else:
            record["extra"]["otelTraceID"] = "disabled"
            
        return "{time:HH:mm:ss} | {level: <8} | {extra[agent]} | [trace={extra[otelTraceID]}] | {message}\n"

    return logger.add(
        run_log_path, 
        format=run_log_format,
        filter=lambda record: record["extra"].get("run_id") == run_id
    )

@contextlib.contextmanager
def run_logger(run_id: str):
    """Context manager that adds a run-specific file logger and ensures it's removed on exit."""
    handler_id = add_run_file_logger(run_id)
    try:
        yield
    finally:
        logger.remove(handler_id)

def trace_span(name: str, agent: str = "system", run_id: str = "none"):
    """
    Decorator for tracing a function.
    If SYNC_ENABLED, uses active OTel tracer.
    If disabled, simply executes the function with loguru context.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            if SYNC_ENABLED:
                tracer = trace.get_tracer(__name__)
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
