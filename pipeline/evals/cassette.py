# pipeline/evals/cassette.py
"""
A cassette of recorded structured-output responses for deterministic, offline
replay of the pipeline at the ``AgentRunner.complete_structured`` seam.

Responses are keyed by the ``(agent_name, node_id)`` pair the agents pass to
``complete_structured`` — stable and order-independent for the per-node agents
even under parallel extraction (B1), where raw call order is nondeterministic.
A key maps to a FIFO queue because an agent can be called more than once for the
same key (e.g. batched dedup, all with ``node_id=None``). A lookup miss or a
drained queue raises :class:`CassetteMiss` so a replay fails loudly rather than
silently degrading an agent to its safe default.
"""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Optional


class CassetteMiss(LookupError):
    """No recorded response for a requested (agent_name, node_id) key."""


def _key(agent_name: Optional[str], node_id: Optional[str]) -> tuple:
    return (agent_name, node_id)


class Cassette:
    """Recorded structured responses keyed by (agent_name, node_id)."""

    def __init__(self, entries: list[dict]):
        self._queues: dict[tuple, deque] = {}
        for e in entries:
            k = _key(e.get("agent"), e.get("node_id"))
            self._queues.setdefault(k, deque()).append(e.get("response"))

    @classmethod
    def from_dict(cls, data: dict) -> "Cassette":
        return cls(list(data.get("entries") or []))

    @classmethod
    def from_file(cls, path) -> "Cassette":
        return cls.from_dict(json.loads(Path(path).read_text()))

    def next(self, agent_name: Optional[str], node_id: Optional[str]) -> dict:
        """Pop and return the next recorded response dict for the key, or raise
        :class:`CassetteMiss` if the key is unknown or its queue is drained."""
        k = _key(agent_name, node_id)
        q = self._queues.get(k)
        if not q:
            raise CassetteMiss(
                f"cassette has no recorded response for agent={agent_name!r} "
                f"node_id={node_id!r} (unknown key or queue exhausted). "
                f"Known keys: {sorted(repr(kk) for kk in self._queues)}"
            )
        return q.popleft()

    def exhausted(self) -> bool:
        """True when every recorded response has been consumed."""
        return all(len(q) == 0 for q in self._queues.values())


__all__ = ["Cassette", "CassetteMiss"]
