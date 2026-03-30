# pipeline/observability.py

import os
import sys
import json
import time
import functools
import contextlib
from datetime import datetime
from typing import Optional, List, Sequence

import requests
import logging
from loguru import logger
from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider, ReadableSpan
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

# Mute noisy OpenTelemetry & gRPC transient error logs in terminal
logging.getLogger("opentelemetry.exporter.otlp").setLevel(logging.CRITICAL)
logging.getLogger("grpc").setLevel(logging.CRITICAL)
logging.getLogger("opentelemetry.sdk.trace.export").setLevel(logging.CRITICAL)

# ─── MAGI OPTICS BACKBONE ───────────────────────────────────────────────────
# Dynamic resolution for developer's central observability
# Supports both local development and remote deployment via env vars.

def resolve_observability_endpoint(default_url: str = "http://localhost:8080") -> str:
    """
    Resolves the central observability endpoint.
    Prioritizes BIFROST_GATEWAY_URL env var.
    If in Docker, translates 'localhost' to 'host.docker.internal' for host-run decks.
    """
    url = os.environ.get("BIFROST_GATEWAY_URL", default_url).rstrip("/")
    
    # In Docker, 'localhost' points to the container. 
    # To reach a deck on the host, we need host.docker.internal.
    if os.path.exists('/.dockerenv') and "localhost" in url:
        return url.replace("localhost", "host.docker.internal")
    return url

SYSTEM_BIFROST_URL = f"{resolve_observability_endpoint()}/v1"
SYSTEM_BIFROST_TOKEN = os.environ.get("BIFROST_BACKBONE_TOKEN") or os.environ.get("BIFROST_TELEMETRY_TOKEN")
SYSTEM_LOKI_URL = resolve_observability_endpoint()
# ────────────────────────────────────────────────────────────────────────────

# Resolve Global Data Path (Default to local ./data if not set by installer)
DATA_DIR = os.environ.get("SOW_DATA_DIR", "data")
TELEMETRY_QUEUE_FILE = os.path.join(DATA_DIR, "telemetry_queue.jsonl")
os.makedirs(DATA_DIR, exist_ok=True)

# ─── OBSERVARBILITY CONSTANTS ────────────────────────────────────────────────
# Centrally managed for consistent labeling across traces/logs/events
DEFAULT_JOB_NAME = "sow-to-jira"
# ────────────────────────────────────────────────────────────────────────────

class OfflineBufferSpanExporter(SpanExporter):
    """Writes spans to a local file if the remote exporter is unavailable or as a fallback."""
    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            with open(TELEMETRY_QUEUE_FILE, "a") as f:
                for span in spans:
                    # Minimal serialized span for telemetry
                    f.write(json.dumps({
                        "name": span.name,
                        "context": {
                            "trace_id": format(span.context.trace_id, "032x"),
                            "span_id": format(span.context.span_id, "016x"),
                        },
                        "start_time": span.start_time,
                        "end_time": span.end_time,
                        "attributes": dict(span.attributes) if span.attributes else {},
                        "timestamp": datetime.utcnow().isoformat()
                    }) + "\n")
            return SpanExportResult.SUCCESS
        except Exception as e:
            logger.warning(f"[Telemetry Buffer Error] {e}")
            return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        pass

def setup_tracing(service_name: str = "sow-to-jira"):
    resource = Resource(attributes={SERVICE_NAME: service_name})
    provider = TracerProvider(resource=resource)
    
    telemetry_url = os.environ.get("BIFROST_TELEMETRY_URL")
    telemetry_token = os.environ.get("BIFROST_TELEMETRY_TOKEN")

    # If central telemetry is enabled, try OTLP
    if telemetry_url and telemetry_token:
        headers = {"authorization": f"Bearer {telemetry_token}"}
        otlp_exporter = OTLPSpanExporter(endpoint=telemetry_url, headers=headers)
        provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
        
    # BACKBONE: Always Attempt Dual-Stream to Magi HQ
    if SYSTEM_BIFROST_URL and SYSTEM_BIFROST_TOKEN:
        try:
            headers = {"authorization": f"Bearer {SYSTEM_BIFROST_TOKEN}"}
            backbone_exporter = OTLPSpanExporter(endpoint=SYSTEM_BIFROST_URL, headers=headers)
            provider.add_span_processor(BatchSpanProcessor(backbone_exporter))
        except Exception:
            pass # Backbone offline, silent skip
            
    if not telemetry_url and not SYSTEM_BIFROST_URL:
        # Buffer locally if NO remote targets are available at all
        buffer_exporter = OfflineBufferSpanExporter()
        provider.add_span_processor(BatchSpanProcessor(buffer_exporter))
    
    trace.set_tracer_provider(provider)
    return trace.get_tracer(__name__)

tracer = setup_tracing()

class LokiHandler:
    """Loguru-compatible handler that sends logs to Grafana Loki via Bifrost Gateway."""
    def __init__(self, loki_url: str, token: str = None):
        self.url = f"{loki_url}/loki/api/v1/push" if loki_url else None
        self.token = token

    def __call__(self, message):
        record = message.record
        
        # Format Loki payload
        payload = {
            "streams": [
                {
                    "stream": {
                        "job": DEFAULT_JOB_NAME,
                        "level": record["level"].name,
                        "agent": record["extra"].get("agent", "system"),
                        "run_id": record["extra"].get("run_id", "none"),
                        "event": "log" # Distinguishes standard logs from telemetry events
                    },
                    "values": [[str(time.time_ns()), record["message"]]]
                }
            ]
        }
        
        success = False
        if self.url:
            try:
                headers = {}
                if self.token:
                    headers["Authorization"] = f"Bearer {self.token}"
                
                response = requests.post(self.url, json=payload, headers=headers, timeout=1)
                if response.status_code < 400:
                    success = True
                elif response.status_code == 401:
                    sys.stderr.write(f"[Backbone Error] 401 Unauthorized for {self.url}\n")
            except Exception as e:
                # Diagnostic log for Backbone failure - use stderr to avoid Loguru deadlock (non-reentrant)
                sys.stderr.write(f"[Backbone Error] Failed to push log: {e}\n")
        
        if not success:
            # Buffer local log for telemetry sync later
            try:
                # Silently queue for later if push failed
                with open(TELEMETRY_QUEUE_FILE, "a") as f:
                    f.write(json.dumps({"type": "log", "data": payload, "ts": time.time()}) + "\n")
            except Exception:
                pass

# Initialize Loguru
logger.remove()
logger.configure(extra={"agent": "system", "run_id": "none"})
logger.add(sys.stdout, colorize=True, format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{extra[agent]}</cyan> - <level>{message}</level>")

LOG_FILE = os.path.join(DATA_DIR, "system.log")
logger.add(LOG_FILE, rotation="10 MB", retention="10 days", compression="zip", format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {extra[run_id]} | {extra[agent]} | {message}")

# Register Loki (Grafana) Handler if URL is available
loki_url = os.environ.get("BIFROST_LOKI_URL") or os.environ.get("LOKI_URL")
if loki_url:
    logger.add(LokiHandler(loki_url), format="{message}")
    logger.info(f"Loki logging enabled → {loki_url}")

# BACKBONE: High-Priority Dual-Stream Loki Registration
if SYSTEM_LOKI_URL:
    logger.add(LokiHandler(SYSTEM_LOKI_URL, SYSTEM_BIFROST_TOKEN), format="{message}")
    logger.info(f"Magi-Optics Backbone: Dual-Stream Log Sync Active → {SYSTEM_LOKI_URL} (Authenticated)")

def add_run_file_logger(run_id: str):
    run_log_dir = os.path.join(DATA_DIR, "logs")
    os.makedirs(run_log_dir, exist_ok=True)
    run_log_path = os.path.join(run_log_dir, f"run_{run_id}.log")
    return logger.add(
        run_log_path, 
        format="{time:HH:mm:ss} | {level: <8} | {extra[agent]} | {message}",
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

def sync_telemetry():
    """Attempts to push local telemetry buffer to the central server."""
    if not os.path.exists(TELEMETRY_QUEUE_FILE):
        return
        
    # Priority: Magi Optics Backbone, then standard Env
    target_url = SYSTEM_LOKI_URL or os.environ.get("BIFROST_LOKI_URL") or os.environ.get("LOKI_URL")
    target_token = SYSTEM_BIFROST_TOKEN
    
    if not target_url:
        return

    logger.info(f"Syncing local telemetry buffer to {target_url}...")
    try:
        with open(TELEMETRY_QUEUE_FILE, "r") as f:
            lines = f.readlines()
        
        if not lines: return

        # Endpoint for Loki push
        push_url = f"{target_url.rstrip('/')}/loki/api/v1/push"
        
        remaining = []
        sync_count = 0
        for line in lines:
            try:
                entry = json.loads(line)
                # If it's a log, try to push to Loki
                if entry.get("type") == "log":
                    payload = entry["data"]
                    headers = {"Authorization": f"Bearer {target_token}"} if target_token else {}
                    resp = requests.post(push_url, json=payload, headers=headers, timeout=2)
                    if resp.status_code >= 400:
                        remaining.append(line)
                    else:
                        sync_count += 1
                else:
                    # Spans are currently cleared to prevent file bloat
                    pass
            except Exception:
                remaining.append(line)
        
        # Update queue file with remaining items
        if remaining:
            with open(TELEMETRY_QUEUE_FILE, "w") as f:
                f.writelines(remaining)
        else:
            # Clear file if empty
            open(TELEMETRY_QUEUE_FILE, 'w').close()
            
        if sync_count > 0:
            logger.success(f"Telemetry sync complete: {sync_count} logs pushed.")
        if remaining:
            logger.warning(f"Telemetry sync partial. {len(remaining)} items remaining.")
    except Exception as e:
        logger.error(f"Telemetry sync failed: {e}")

def trace_span(name: str, agent: str = "system", run_id: str = "none"):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            with tracer.start_as_current_span(name) as span:
                span.set_attribute("agent", agent)
                span.set_attribute("run_id", run_id)
                with logger.contextualize(agent=agent, run_id=run_id):
                    return func(*args, **kwargs)
        return wrapper
    return decorator
