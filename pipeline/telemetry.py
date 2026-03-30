# pipeline/telemetry.py

import json
import queue
import threading
import time
from typing import Any, Dict

import requests

# Endpoints are now centrally managed in observability.py
# We import SYSTEM_BIFROST_TOKEN and SYSTEM_LOKI_URL from observability.py

def _scrub_payload(payload: Any) -> Any:
    if isinstance(payload, dict):
        return {k: _scrub_payload(v) for k, v in payload.items() if k != "section_title"}
    if isinstance(payload, list):
        return [_scrub_payload(v) for v in payload]
    return payload


class _TelemetryWorker:
    _instance: "_TelemetryWorker | None" = None
    _lock = threading.Lock()

    def __init__(self, url: str | None, token: str | None):
        self.url = f"{url}/loki/api/v1/push" if url else None
        self.token = token
        self._queue: queue.Queue[Dict[str, Any]] = queue.Queue(maxsize=1000)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    @classmethod
    def instance(cls) -> "_TelemetryWorker":
        with cls._lock:
            if cls._instance is None:
                from pipeline.observability import SYSTEM_LOKI_URL, SYSTEM_BIFROST_TOKEN
                cls._instance = _TelemetryWorker(SYSTEM_LOKI_URL, SYSTEM_BIFROST_TOKEN)
            return cls._instance

    def enqueue(self, item: Dict[str, Any]) -> None:
        if not self.url:
            return
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            pass

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                headers = {}
                if self.token:
                    headers["Authorization"] = f"Bearer {self.token}"
                requests.post(self.url, json=item, headers=headers, timeout=1)
            except Exception:
                pass
            finally:
                self._queue.task_done()


class TelemetryEmitter:
    def __init__(self):
        self._worker = _TelemetryWorker.instance()

    def emit(self, event_name: str, payload: Dict[str, Any]) -> None:
        from pipeline.observability import DEFAULT_JOB_NAME
        scrubbed = _scrub_payload(payload)
        record = {
            "streams": [
                {
                    "stream": {
                        "job": DEFAULT_JOB_NAME,
                        "event": event_name,
                        "run_id": payload.get("run_id", "none")
                    },
                    "values": [[str(time.time_ns()), json.dumps(scrubbed)]],
                }
            ]
        }
        self._worker.enqueue(record)
