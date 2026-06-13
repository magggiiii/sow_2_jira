# tests/test_core_results.py

from core.results import LLMResult, StageResult, StageStatus


# ─── Classmethods set status correctly ────────────────────────────────────────

def test_ok_sets_status_and_output():
    r = StageResult.ok({"tasks": [1, 2, 3]})
    assert r.status is StageStatus.OK
    assert r.output == {"tasks": [1, 2, 3]}
    assert r.reason == ""


def test_ok_with_no_output_defaults_to_none():
    r = StageResult.ok()
    assert r.status is StageStatus.OK
    assert r.output is None


def test_degraded_sets_status_and_reason():
    r = StageResult.degraded("partial parse")
    assert r.status is StageStatus.DEGRADED
    assert r.reason == "partial parse"
    assert r.output is None


def test_failed_sets_status_and_reason():
    r = StageResult.failed("model error")
    assert r.status is StageStatus.FAILED
    assert r.reason == "model error"


def test_classmethods_accept_extra_kwargs():
    r = StageResult.ok(
        output=["a"],
        agent="extraction",
        tokens_in=10,
        tokens_out=20,
        cost_usd=0.0015,
        finish_reason="stop",
    )
    assert r.agent == "extraction"
    assert r.tokens_in == 10
    assert r.tokens_out == 20
    assert r.cost_usd == 0.0015
    assert r.finish_reason == "stop"


# ─── Defaults are sane ─────────────────────────────────────────────────────────

def test_stage_result_defaults():
    r = StageResult(status=StageStatus.SKIPPED)
    assert r.output is None
    assert r.reason == ""
    assert r.agent == ""
    assert r.tokens_in == 0
    assert r.tokens_out == 0
    assert r.cost_usd == 0.0
    assert r.finish_reason == ""


def test_stage_status_members():
    assert {s.value for s in StageStatus} == {"OK", "DEGRADED", "FAILED", "SKIPPED"}


# ─── Round-trips model_dump ────────────────────────────────────────────────────

def test_stage_result_round_trips_model_dump():
    r = StageResult.ok({"k": "v"}, agent="dedup", tokens_in=5, cost_usd=0.01)
    dumped = r.model_dump()
    assert isinstance(dumped, dict)
    rebuilt = StageResult.model_validate(dumped)
    assert rebuilt == r
    assert rebuilt.status is StageStatus.OK
    assert rebuilt.output == {"k": "v"}


# ─── LLMResult contract ────────────────────────────────────────────────────────

def test_llm_result_defaults():
    r = LLMResult(content="hello")
    assert r.content == "hello"
    assert r.finish_reason == ""
    assert r.prompt_tokens == 0
    assert r.completion_tokens == 0
    assert r.usd_cost == 0.0


def test_llm_result_round_trips_model_dump():
    r = LLMResult(
        content="out",
        finish_reason="stop",
        prompt_tokens=3,
        completion_tokens=7,
        usd_cost=0.002,
    )
    rebuilt = LLMResult.model_validate(r.model_dump())
    assert rebuilt == r
