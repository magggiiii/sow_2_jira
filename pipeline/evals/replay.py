# pipeline/evals/replay.py
"""
Offline replay shims for the eval harness.

``NoOpProvider`` satisfies the ``core.ports.LLMProvider`` port so it can fill the
``PipelineOrchestrator(llm=...)`` slot without constructing a real ``LLMClient``
(no network at orchestrator build time). It is never actually consulted for
structured output — agents reach structured output through
``AgentRunner.complete_structured``, which the harness patches with
``make_structured_replay`` to serve recorded responses from a :class:`Cassette`.

``make_structured_replay`` returns a function with the exact signature of
``AgentRunner.complete_structured`` (``self`` first, then keyword-only args), so
it can be bound onto the class via ``mock.patch.object`` for a full offline run.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional, Type, Union

from pydantic import BaseModel

from pipeline.evals.cassette import Cassette


class NoOpProvider:
    """Minimal ``LLMProvider`` for the orchestrator's ``llm=`` slot — structured
    output is served by the patched ``complete_structured``, not by this."""

    def __init__(self, model: str = "replay/none"):
        self.model = model
        # Some code paths read provider_config.model as a fallback.
        self.provider_config = type(
            "PC",
            (),
            {"model": model, "api_key": "", "api_base": "", "provider": "replay"},
        )()

    def complete(
        self,
        prompt: str,
        system: str = "You are a helpful assistant.",
        temperature: float = 0.0,
        max_tokens: int = 4096,
        agent_name: str = "unknown",
        node_id: str = "",
    ) -> str:
        return ""

    def complete_json(
        self,
        prompt: str,
        system: str = "You are a precise JSON extraction assistant.",
        agent_name: str = "unknown",
        node_id: str = "",
        max_tokens: int = 8192,
    ) -> Union[list, dict]:
        return []


def make_structured_replay(cassette: Cassette):
    """
    Build a replacement for ``AgentRunner.complete_structured`` that serves the
    next recorded response from ``cassette`` for the call's ``(agent_name,
    node_id)``, validated into the requested ``response_model``. Raises
    ``CassetteMiss`` (loudly) on an unknown/exhausted key.
    """

    def complete_structured(
        runner_self,
        *,
        prompt: str,
        response_model: Type[BaseModel],
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
        agent_name: Optional[str] = None,
        node_id: Optional[str] = None,
    ) -> Any:
        recorded = cassette.next(agent_name, node_id)
        return response_model.model_validate(recorded)

    return complete_structured


# ── Replay-time dedup-UUID remapper (STEP 3.8) ───────────────────────────────
# A recorded DeduplicationAgent response embeds the RECORDING run's fresh task
# UUIDs in task_id_a/task_id_b. A replay run mints DIFFERENT UUIDs (uuid4 in
# ManagedTask), so a statically-recorded dedup decision won't match the replay
# run's tasks. This remapper rewrites the recorded ids onto the current run's
# task ids, matching each recorded task identity by title -> source node_id ->
# position. It lives on the REPLAY side (the recorder captures raw truth
# unchanged); operating on the recorded DICT keeps this module dependency-light.


class RecordedTaskRef(BaseModel):
    """Identity of a task as it existed in the RECORDING run.

    Pairs the recording-run task UUID (``id``, exactly as it appears in a
    recorded ``DedupDecision``'s ``task_id_a``/``task_id_b``) with stable anchors
    so a replay run can map it onto its own freshly-minted task. A faithful
    cassette carries one of these per task the dedup step saw.
    """

    id: str
    title: str = ""
    node_id: str = ""
    position: int = -1


def _norm_title(title: Optional[str]) -> str:
    return (title or "").strip().lower()


def build_dedup_id_remap(recorded_refs, current_tasks) -> dict:
    """Map each recording-run task UUID onto the current run's task id.

    Matches each :class:`RecordedTaskRef` to a current task by, in priority
    order: (1) title — only when it uniquely identifies one current task; (2)
    source node_id — only when it uniquely identifies one current task; (3)
    position — ONLY when the ref carries no title/node anchors at all (an
    anchor-less cassette); a populated-but-missed anchor means the recorded task
    drifted away, so position is NOT trusted (that would merge an unrelated
    present task). Refs that match nothing are omitted.

    Each current task is claimed by at most one recorded ref, so two distinct
    recorded ids never collapse onto the same current id (which would later
    surface as a destructive self-merge).
    """
    by_title: dict = defaultdict(list)
    by_node: dict = defaultdict(list)
    for task in current_tasks:
        by_title[_norm_title(task.title)].append(task)
        for ref in task.source_refs:
            by_node[ref.node_id].append(task)

    remap: dict = {}
    claimed: set = set()

    def _claim(ref_id: str, task) -> None:
        remap[ref_id] = str(task.id)
        claimed.add(str(task.id))

    # Pass 1 — strong identity matches (title, then node). Anchored refs get
    # first pick of the current tasks so a later positional ref can never
    # pre-empt an anchored owner.
    for ref in recorded_refs:
        match = None
        title_hits = by_title.get(_norm_title(ref.title), [])
        if len(title_hits) == 1:
            match = title_hits[0]
        if match is None:
            node_hits = by_node.get(ref.node_id, [])
            if len(node_hits) == 1:
                match = node_hits[0]
        if match is not None and str(match.id) not in claimed:
            _claim(ref.id, match)

    # Pass 2 — positional fallback, ONLY for refs that carry no identity anchors
    # at all (an anchor-less cassette). A populated-but-missed anchor means the
    # recorded task drifted away, so position is not trusted (that would merge an
    # unrelated present task); such refs are left unmapped.
    for ref in recorded_refs:
        if ref.id in remap:
            continue
        has_anchors = bool(_norm_title(ref.title)) or bool(ref.node_id)
        if has_anchors:
            continue
        if 0 <= ref.position < len(current_tasks):
            match = current_tasks[ref.position]
            if str(match.id) not in claimed:
                _claim(ref.id, match)
    return remap


def remap_recorded_dedup_ids(recorded: dict, recorded_refs, current_tasks) -> dict:
    """Rewrite a recorded DeduplicationAgent response (a ``DedupDecisionList``-
    shaped dict) so its ``task_id_a``/``task_id_b`` reference the CURRENT run's
    task ids.

    Decisions whose endpoints cannot BOTH be remapped are dropped, never faked —
    a replay must not invent a merge against a task that isn't present. A decision
    whose endpoints resolve to the SAME current task (``new_a == new_b``) is also
    dropped: a self-merge would delete the real task on replay.
    """
    id_remap = build_dedup_id_remap(recorded_refs, current_tasks)
    remapped = []
    for decision in recorded.get("decisions", []) or []:
        new_a = id_remap.get(decision.get("task_id_a"))
        new_b = id_remap.get(decision.get("task_id_b"))
        if new_a is None or new_b is None or new_a == new_b:
            continue
        remapped.append({**decision, "task_id_a": new_a, "task_id_b": new_b})
    return {**recorded, "decisions": remapped}


__all__ = [
    "NoOpProvider",
    "make_structured_replay",
    "RecordedTaskRef",
    "build_dedup_id_remap",
    "remap_recorded_dedup_ids",
]
