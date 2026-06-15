# tests/test_cassette_recorder.py
"""
INV-4: the cassette RECORDER — the capture half of the replay seam.

A faithful 103-node golden cassette is recorded by wrapping
``AgentRunner.complete_structured`` during a real run: each call is captured as
``(agent_name, node_id, validated_output)`` and serialized into the SAME
entries + ``_metadata`` shape the :class:`Cassette` / replay consume. These
tests pin the capture contract and prove a recorded cassette round-trips back
through the replay seam — without a live LLM.
"""

from __future__ import annotations

from pipeline.agents.classifier import RawClassification, SectionType
from pipeline.agents.coverage_check import CoverageAudit
from pipeline.evals.cassette import Cassette
from pipeline.evals.recorder import CassetteRecorder, record_cassette
from pipeline.evals.replay import make_structured_replay


class _Runner:
    """Stand-in for an AgentRunner instance (the bound ``self``)."""


def _fake_real(payloads):
    """A fake live ``complete_structured`` returning ``response_model`` validated
    from the next payload — simulating the Instructor round trip."""
    seq = iter(payloads)

    def real(runner_self, *, prompt, response_model, system=None, max_tokens=None,
             agent_name=None, node_id=None):
        return response_model.model_validate(next(seq))

    return real


def test_recorder_captures_agent_node_and_passes_output_through():
    rec = CassetteRecorder()
    wrapped = rec.wrap(_fake_real([{"missed_items": []}]))

    out = wrapped(
        _Runner(), prompt="p", response_model=CoverageAudit,
        agent_name="CoverageChecker", node_id="node-0",
    )

    # passthrough: the live result is returned UNCHANGED
    assert isinstance(out, CoverageAudit)
    assert out.missed_items == []
    # capture: one entry keyed by (agent, node_id), response == model_dump
    assert rec.entries == [
        {"agent": "CoverageChecker", "node_id": "node-0", "response": {"missed_items": []}}
    ]


def test_to_dict_carries_metadata_and_entries():
    rec = CassetteRecorder(metadata={"provider": "openrouter", "model": "x", "mode": "custom"})
    wrapped = rec.wrap(_fake_real([{"missed_items": []}]))
    wrapped(_Runner(), prompt="p", response_model=CoverageAudit,
            agent_name="CoverageChecker", node_id="node-0")

    d = rec.to_dict()
    assert d["_metadata"]["provider"] == "openrouter"
    assert d["_metadata"]["model"] == "x"
    assert len(d["entries"]) == 1


def test_recorded_cassette_roundtrips_through_replay():
    """The whole point: a recorded cassette replays to the SAME outputs at the
    same (agent, node_id) keys."""
    rec = CassetteRecorder()
    payloads = [
        {"type": "actionable", "confidence": 0.9, "reason": "r"},
        {"missed_items": []},
    ]
    wrapped = rec.wrap(_fake_real(payloads))
    wrapped(_Runner(), prompt="p", response_model=RawClassification,
            agent_name="SectionClassifier", node_id="node-0")
    wrapped(_Runner(), prompt="p", response_model=CoverageAudit,
            agent_name="CoverageChecker", node_id="node-0")

    cassette = Cassette.from_dict(rec.to_dict())
    replay = make_structured_replay(cassette)

    c = replay(_Runner(), prompt="p", response_model=RawClassification,
               agent_name="SectionClassifier", node_id="node-0")
    assert c.type == SectionType.ACTIONABLE
    assert c.confidence == 0.9
    cov = replay(_Runner(), prompt="p", response_model=CoverageAudit,
                 agent_name="CoverageChecker", node_id="node-0")
    assert cov.missed_items == []


def test_multiple_calls_same_key_recorded_in_fifo_order():
    """Batched agents (e.g. dedup) call with the same key repeatedly; the cassette
    is a FIFO queue per key, so order must be preserved."""
    rec = CassetteRecorder()
    wrapped = rec.wrap(_fake_real([{"missed_items": []}, {"missed_items": []}]))
    wrapped(_Runner(), prompt="p", response_model=CoverageAudit,
            agent_name="CoverageChecker", node_id=None)
    wrapped(_Runner(), prompt="p", response_model=CoverageAudit,
            agent_name="CoverageChecker", node_id=None)

    cassette = Cassette.from_dict(rec.to_dict())
    # both pop cleanly, then the queue is exhausted
    cassette.next("CoverageChecker", None)
    cassette.next("CoverageChecker", None)
    assert cassette.exhausted()


def test_record_cassette_context_patches_and_restores(monkeypatch):
    """``record_cassette()`` patches AgentRunner.complete_structured for the
    duration (capturing calls through a real AgentRunner) and restores it after."""
    from core.agent_runner import AgentRunner

    def fake_real(self, *, prompt, response_model, system=None, max_tokens=None,
                  agent_name=None, node_id=None):
        return response_model.model_validate({"missed_items": []})

    monkeypatch.setattr(AgentRunner, "complete_structured", fake_real)

    with record_cassette(metadata={"mode": "custom"}) as rec:
        runner = AgentRunner.__new__(AgentRunner)  # no __init__, no LLM
        out = runner.complete_structured(
            prompt="p", response_model=CoverageAudit,
            agent_name="CoverageChecker", node_id="node-1",
        )
        assert isinstance(out, CoverageAudit)

    assert rec.entries == [
        {"agent": "CoverageChecker", "node_id": "node-1", "response": {"missed_items": []}}
    ]
    # restored to the underlying method, not the recorder wrapper
    assert AgentRunner.complete_structured is fake_real
