"""In-memory ``ProgressStore`` fake for the async/worker seam (offline).

``FakeProgressStore`` satisfies the ``ProgressStore`` Protocol
(core/progress_port.py) structurally, backed by a plain dict. ``get`` returns
``None`` for an unknown run id and round-trips whatever ``set`` last recorded.
The real Redis-backed adapter will land later behind the same interface.
"""

from __future__ import annotations

from typing import Optional

__all__ = ["FakeProgressStore"]


class FakeProgressStore:
    """Dict-backed ``ProgressStore``; each ``set`` overwrites the run's snapshot."""

    def __init__(self) -> None:
        self._runs: dict[str, dict] = {}

    def set(
        self,
        run_id: str,
        status: str,
        progress: float,
        current_step: int,
        message: str,
    ) -> None:
        self._runs[run_id] = {
            "status": status,
            "progress": progress,
            "current_step": current_step,
            "message": message,
        }

    def get(self, run_id: str) -> Optional[dict]:
        snap = self._runs.get(run_id)
        if snap is None:
            return None
        # Return a copy so callers can't mutate internal state through the ref.
        return dict(snap)
