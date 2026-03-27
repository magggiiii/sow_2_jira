# pipeline/observability.py

import os
import sys
import json
import time
import functools
from datetime import datetime
from typing import Optional, List, Sequence

from loguru import logger
from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider, ReadableSpan
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
import requests

# Resolve Global Data Path (Default to local ./data if not set by installer)
DATA_DIR = os.environ.get("SOW_DATA_DIR", "data")
TELEMETRY_QUEUE_FILE = os.path.join(DATA_DIR, "telemetry_queue.jsonl")
os.makedirs(DATA_DIR, exist_ok=True)

class PIIScrubbingSpanProcessor(BatchSpanProcessor):
    """
    Intercepts spans before export and removes PII/Confidential text.
    Specifically targets litellm and custom LLM prompt/response attributes.
    """
    def on_end(self, span: ReadableSpan):
        if hasattr(span, "_attributes") and span._attributes:
            keys_to_remove = []
            for k in span._attributes.keys():
                k_lower = k.lower()
                if any(x in k_lower for x in ["prompt", "response", "completion", "messages", "content", "statement"]):
                    keys_to_remove.append(k)
            for k in keys_to_remove:
                # We use a mutable copy if needed, but SDK spans usually allow attribute deletion
                try:
                    del span._attributes[k]
                except (TypeError, KeyError):
                    pass
        super().on_end(span)

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
        headers = {"Authorization": f"Bearer {telemetry_token}"}
        otlp_exporter = OTLPSpanExporter(endpoint=telemetry_url, headers=headers)
        span_processor = PIIScrubbingSpanProcessor(otlp_exporter)
        provider.add_span_processor(span_processor)
    else:
        # Buffer locally if no remote URL is set
        buffer_exporter = OfflineBufferSpanExporter()
        provider.add_span_processor(BatchSpanProcessor(buffer_exporter))
    
    trace.set_tracer_provider(provider)
    return trace.get_tracer(__name__)

tracer = setup_tracing()

class LokiHandler:
    """Loguru-compatible handler that sends logs to Grafana Loki with local fallback."""
    def __init__(self, loki_url: str):
        self.url = f"{loki_url}/loki/api/v1/push" if loki_url else None

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
                response = requests.post(self.url, json=payload, timeout=1)
                if response.status_code < 400:
                    success = True
            except Exception:
                pass
        
        if not success:
            # Buffer local log for telemetry sync later
            try:
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
    if not telemetry_url:
        return

    logger.info("Attempting to sync local telemetry buffer...")
    # Implementation of sync logic (reading file, pushing to OTLP/Loki, then clearing)
    # This would typically use the OTLP exporter directly or a separate sync endpoint.
    # For now, we stub it as it's a complex background task.

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
