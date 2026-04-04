# pipeline/telemetry.py

import json
from typing import Any, Dict

from pipeline.observability import logger

def _scrub_payload(payload: Any) -> Any:
    if isinstance(payload, dict):
        return {k: _scrub_payload(v) for k, v in payload.items() if k != "section_title"}
    if isinstance(payload, list):
        return [_scrub_payload(v) for v in payload]
    return payload

class TelemetryEmitter:
    """
    Legacy telemetry emitter, now routes structured events through the
    centralized loguru logger so they flow to OTel and local JSON audit.
    """
    def emit(self, event_name: str, payload: Dict[str, Any]) -> None:
        scrubbed = _scrub_payload(payload)
        
        # We just log it as an INFO level structured log.
        # It will be picked up by the JSON audit sink and OTel Collector.
        logger.bind(event=event_name, payload=scrubbed).info(
            f"Telemetry Event: {event_name} | {json.dumps(scrubbed)}"
        )
