# core/pipeline/context.py
"""
``PipelineContext`` — the mutable run-state carrier threaded through the stages.

It holds exactly the state that ``PipelineOrchestrator.run()`` keeps in local
variables as it moves between phases (nodes, the coverage tracker, the closed/
open task lists, the deduplicated result, the coverage report) plus a ``cancelled``
flag. Stages read and write these fields; the runner threads one instance through
every stage. ``orch`` is the orchestrator the stages call back into — typed
``Any`` so this core module never imports ``pipeline.orchestrator`` (keeping the
dependency edge one-way: pipeline → core, never core → pipeline).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PipelineContext:
    """State shared across pipeline stages for one run."""

    orch: Any
    nodes: list = field(default_factory=list)
    coverage: Any = None
    all_closed_tasks: list = field(default_factory=list)
    open_tasks: list = field(default_factory=list)
    deduplicated: list = field(default_factory=list)
    report: dict = field(default_factory=dict)
    cancelled: bool = False
    run_start: float = 0.0

    def result(self) -> list:
        """The run's task output.

        After deduplication ``deduplicated`` is the final set. Before it (e.g. a
        run cancelled mid-extraction) the closed-task set is the best available
        output — mirroring ``run()``'s ``return all_closed_tasks`` on cancel.
        """
        return self.deduplicated if self.deduplicated else self.all_closed_tasks
