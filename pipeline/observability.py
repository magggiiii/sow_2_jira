# pipeline/observability.py

import os
import sys
import json
import time
import functools
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
# Hardcoded defaults for developer's central observability (Magi's Mac)
# Port 8080 is the unified Bifrost Gateway for both Logs (Loki) and Traces (OTLP)
SYSTEM_BIFROST_URL = "http://localhost:8080/v1"
SYSTEM_BIFROST_TOKEN = "sk-bf-85187814-2d9c-4b37-b148-b56b81d9a130"
SYSTEM_LOKI_URL = "http://localhost:8080"
# ────────────────────────────────────────────────────────────────────────────

# Resolve Global Data Path (Default to local ./data if not set by installer)
DATA_DIR = os.environ.get("SOW_DATA_DIR", "data")
TELEMETRY_QUEUE_FILE = os.path.join(DATA_DIR, "telemetry_queue.jsonl")
os.makedirs(DATA_DIR, exist_ok=True)

# Resolve Global Data Path (Default to local ./data if not set by installer)

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
            print(f"[Telemetry Buffer Error] {e}", file=sys.stderr)
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
                        "job": "sow-to-jira",
                        "level": record["level"].name,
                        "agent": record["extra"].get("agent", "system"),
                        "run_id": record["extra"].get("run_id", "none")
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
                    print(f"[Backbone Error] 401 Unauthorized for {self.url}", file=sys.stderr)
            except Exception as e:
                # Diagnostic log for Backbone failure - only shows in terminal, not user logs
                print(f"[Backbone Error] Failed to push log: {e}", file=sys.stderr)
        
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

def sync_telemetry():
    """Attempts to push local telemetry buffer to the central server."""
    if not os.path.exists(TELEMETRY_QUEUE_FILE):
        return
        
    telemetry_url = os.environ.get("BIFROST_TELEMETRY_URL")
    loki_url = os.environ.get("BIFROST_LOKI_URL") or os.environ.get("LOKI_URL")
    
    if not telemetry_url and not loki_url:
        return

    logger.info("Syncing local telemetry buffer...")
    try:
        with open(TELEMETRY_QUEUE_FILE, "r") as f:
            lines = f.readlines()
        
        if not lines: return

        # Re-initialize Loki handler for manual push if needed
        loki_sender = LokiHandler(loki_url) if loki_url else None
        
        remaining = []
        for line in lines:
            try:
                entry = json.loads(line)
                # If it's a log, try to push to Loki
                if entry.get("type") == "log" and loki_sender:
                    # Manually trigger handler call
                    loki_sender({"message": entry["data"], "record": {"extra": entry["data"]["streams"][0]["stream"], "level": type('Level', (object,), {"name": entry["data"]["streams"][0]["stream"]["level"]})(), "message": entry["data"]["streams"][0]["values"][0][1]}})
                # If it's a span, we'd need a more complex OTLP push (omitted for brevity, just clearing for now)
            except Exception:
                remaining.append(line)
        
        # Clear or update queue file
        with open(TELEMETRY_QUEUE_FILE, "w") as f:
            f.writelines(remaining)
            
        logger.success("Telemetry sync complete.")
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
