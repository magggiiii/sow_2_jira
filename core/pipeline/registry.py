# core/pipeline/registry.py
"""
``StageRegistry`` — an ordered collection of pipeline stages.

Registration order IS execution order; the :class:`~core.pipeline.runner.PipelineRunner`
iterates the registry to run a pipeline. Keeping the ordering explicit (rather
than implicit in a hard-coded ``run()`` body) is what lets the pipeline be
declared as data — inspected, reordered (e.g. the PEV ordering), or extended.
"""

from __future__ import annotations

from typing import Iterator

from core.pipeline.stage import Stage


class StageRegistry:
    """Ordered registry of :class:`Stage` instances (registration = run order)."""

    def __init__(self) -> None:
        self._stages: list[Stage] = []

    def register(self, stage: Stage) -> Stage:
        """Append ``stage`` to the end of the run order and return it."""
        self._stages.append(stage)
        return stage

    def names(self) -> list[str]:
        return [s.name for s in self._stages]

    def __iter__(self) -> Iterator[Stage]:
        return iter(self._stages)

    def __len__(self) -> int:
        return len(self._stages)


__all__ = ["StageRegistry"]
