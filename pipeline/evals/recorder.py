# pipeline/evals/recorder.py
"""
INV-4 cassette RECORDER — the capture half of the replay seam.

The faithful 103-node golden cassette is produced by wrapping
``AgentRunner.complete_structured`` during a REAL run: every structured call is
captured as an ``(agent_name, node_id, validated_output)`` entry in the SAME
shape :class:`pipeline.evals.cassette.Cassette` / :func:`make_structured_replay`
consume, plus a ``_metadata`` block (provider/model/mode/embed model/schema
version) so a later replay can assert it is being driven against the environment
the cassette was recorded in.

This is pure plumbing: the wrapper calls THROUGH to the real method and returns
its result UNCHANGED, so a recorded run behaves identically to an un-recorded
one — the recording is a side-channel. The companion :func:`make_structured_replay`
(``replay.py``) is the exact inverse; a round trip
``record -> Cassette -> replay`` is asserted in ``tests/test_cassette_recorder.py``.

Symmetric with the replay shim, it deliberately does NOT touch
``pipeline/llm_router.py`` / ``BIFROST_*`` / ``os.environ`` — it only patches the
``AgentRunner.complete_structured`` seam, exactly as the harness does.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Optional


class CassetteRecorder:
    """Accumulate ``(agent, node_id, response)`` entries at the structured seam."""

    def __init__(self, metadata: Optional[dict] = None):
        self.entries: list[dict] = []
        self.metadata: dict = dict(metadata or {})

    def wrap(self, real_complete_structured: Callable) -> Callable:
        """Return a ``complete_structured`` replacement that records each call.

        ``real_complete_structured`` is the underlying (unbound) method; the
        returned wrapper takes the AgentRunner instance as its first positional
        ``runner_self`` — matching :func:`make_structured_replay` — so it can be
        bound onto the class via ``mock.patch.object`` or a plain class assignment.
        """
        recorder = self

        def complete_structured(
            runner_self,
            *,
            prompt: str,
            response_model,
            system: Optional[str] = None,
            max_tokens: Optional[int] = None,
            agent_name: Optional[str] = None,
            node_id: Optional[str] = None,
        ) -> Any:
            result = real_complete_structured(
                runner_self,
                prompt=prompt,
                response_model=response_model,
                system=system,
                max_tokens=max_tokens,
                agent_name=agent_name,
                node_id=node_id,
            )
            recorder.entries.append(
                {
                    "agent": agent_name,
                    "node_id": node_id,
                    "response": recorder._dump(result),
                }
            )
            return result

        return complete_structured

    @staticmethod
    def _dump(result: Any) -> Any:
        """JSON-serializable form of a validated response (model_dump in json mode)."""
        if hasattr(result, "model_dump"):
            return result.model_dump(mode="json")
        return result

    def to_dict(self) -> dict:
        """Serialize to the cassette shape ``{"_metadata": {...}, "entries": [...]}``."""
        return {"_metadata": dict(self.metadata), "entries": list(self.entries)}

    def write(self, path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return p


@contextmanager
def record_cassette(metadata: Optional[dict] = None):
    """Patch ``AgentRunner.complete_structured`` to record-through for the block.

    Yields a :class:`CassetteRecorder`; every structured call made through any
    ``AgentRunner`` while the context is active is captured and the original
    method is restored on exit (even on error).
    """
    from core.agent_runner import AgentRunner

    rec = CassetteRecorder(metadata=metadata)
    original = AgentRunner.complete_structured
    AgentRunner.complete_structured = rec.wrap(original)
    try:
        yield rec
    finally:
        AgentRunner.complete_structured = original


__all__ = ["CassetteRecorder", "record_cassette"]
