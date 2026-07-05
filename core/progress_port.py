# core/progress_port.py
"""Progress-store port for the async/worker seam.

``ProgressStore`` is the surface that replaces the in-process ``active_runs``
status dict in ``ui/server.py``. A background worker running on a queue can no
longer mutate a Python dict in the web process, so run status/progress must live
behind a port that both an in-memory ``FakeProgressStore``
(integrations/progress/fake.py) and a future Redis-backed adapter can satisfy.

Like the other ports in ``core/ports.py`` this is ``@runtime_checkable`` so a
concrete adapter structurally satisfies it with no inheritance, and tests can
assert ``isinstance(obj, ProgressStore)``. ``runtime_checkable`` only verifies
method *presence*, not signatures.

Additive and dependency-free beyond ``typing`` — importing it must not pull in
redis, litellm, or observability.
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

__all__ = ["ProgressStore"]


@runtime_checkable
class ProgressStore(Protocol):
    """Read/write surface for per-run progress and status.

    ``set`` records a full snapshot for ``run_id``; ``get`` returns that
    snapshot as a dict (keys: ``status``, ``progress``, ``current_step``,
    ``message``) or ``None`` if the run is unknown.
    """

    def set(
        self,
        run_id: str,
        status: str,
        progress: float,
        current_step: int,
        message: str,
    ) -> None:
        """Record the current status/progress snapshot for ``run_id``."""
        ...

    def get(self, run_id: str) -> Optional[dict]:
        """Return the latest snapshot for ``run_id``, or None if unknown."""
        ...
