# tests/test_eval_cassette.py
"""
INV-4 cassette + replay: deterministic, offline replacement for the LLM at the
AgentRunner.complete_structured seam.

complete_structured lives on AgentRunner (NOT the LLMProvider port) and calls
instructor/litellm directly — so the offline interception point is that method,
keyed by the (agent_name, node_id) the agents already pass. The Cassette stores
recorded structured responses per key (a queue, since an agent can be called
more than once for the same key — e.g. batched dedup); the replay validates the
recorded dict into the requested response_model and returns it.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from core.agent_runner import AgentRunner
from core.ports import LLMProvider
from pipeline.evals.cassette import Cassette, CassetteMiss
from pipeline.evals.replay import NoOpProvider, make_structured_replay


class _Resp(BaseModel):
    kind: str
    n: int = 0


# ─── Cassette ────────────────────────────────────────────────────────────────


def test_cassette_next_returns_recorded_in_order():
    cas = Cassette.from_dict({"entries": [
        {"agent": "ExtractionAgent", "node_id": "n0", "response": {"kind": "a"}},
        {"agent": "ExtractionAgent", "node_id": "n1", "response": {"kind": "b"}},
    ]})
    assert cas.next("ExtractionAgent", "n0") == {"kind": "a"}
    assert cas.next("ExtractionAgent", "n1") == {"kind": "b"}


def test_cassette_repeated_key_pops_queue_in_order():
    cas = Cassette.from_dict({"entries": [
        {"agent": "DeduplicationAgent", "node_id": None, "response": {"kind": "batch1"}},
        {"agent": "DeduplicationAgent", "node_id": None, "response": {"kind": "batch2"}},
    ]})
    assert cas.next("DeduplicationAgent", None) == {"kind": "batch1"}
    assert cas.next("DeduplicationAgent", None) == {"kind": "batch2"}


def test_cassette_miss_raises_descriptive():
    cas = Cassette.from_dict({"entries": [
        {"agent": "ExtractionAgent", "node_id": "n0", "response": {"kind": "a"}},
    ]})
    with pytest.raises(CassetteMiss) as exc:
        cas.next("ExtractionAgent", "nX")
    assert "ExtractionAgent" in str(exc.value) and "nX" in str(exc.value)


def test_cassette_exhaustion_raises():
    cas = Cassette.from_dict({"entries": [
        {"agent": "ExtractionAgent", "node_id": "n0", "response": {"kind": "a"}},
    ]})
    cas.next("ExtractionAgent", "n0")
    with pytest.raises(CassetteMiss):
        cas.next("ExtractionAgent", "n0")  # queue drained


# ─── Replay + NoOpProvider ───────────────────────────────────────────────────


def test_noop_provider_satisfies_llm_port():
    assert isinstance(NoOpProvider(), LLMProvider)


def test_replay_validates_recorded_dict_into_response_model():
    cas = Cassette.from_dict({"entries": [
        {"agent": "X", "node_id": "n0", "response": {"kind": "actionable", "n": 3}},
    ]})
    replay = make_structured_replay(cas)
    runner = AgentRunner(NoOpProvider())

    # Bind the replay onto a real AgentRunner instance (mirrors how the harness
    # patches AgentRunner.complete_structured for a full offline run).
    result = replay(runner, prompt="p", response_model=_Resp, agent_name="X", node_id="n0")
    assert isinstance(result, _Resp)
    assert result.kind == "actionable" and result.n == 3


def test_replay_miss_raises_cassette_miss():
    cas = Cassette.from_dict({"entries": []})
    replay = make_structured_replay(cas)
    runner = AgentRunner(NoOpProvider())
    with pytest.raises(CassetteMiss):
        replay(runner, prompt="p", response_model=_Resp, agent_name="X", node_id="n0")
