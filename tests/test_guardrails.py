# tests/test_guardrails.py

from dataclasses import dataclass

from core.guardrails import (
    ConfidenceGate,
    VerifyResult,
    assert_finish_complete,
    assert_nonempty_when_expected,
    assert_work_done,
)


# ─── Test fixtures ─────────────────────────────────────────────────────────────


@dataclass
class _Candidate:
    confidence: float


@dataclass
class _NoField:
    other: float = 0.9


# ─── ConfidenceGate.admit ──────────────────────────────────────────────────────


def test_admit_above_floor():
    gate = ConfidenceGate(field="confidence", floor=0.5)
    assert gate.admit(_Candidate(confidence=0.8)) is True


def test_admit_at_floor_is_inclusive():
    # Mirrors the critic's `>=` semantics: exactly at the floor is admitted.
    gate = ConfidenceGate(field="confidence", floor=0.5)
    assert gate.admit(_Candidate(confidence=0.5)) is True


def test_admit_below_floor_rejected():
    gate = ConfidenceGate(field="confidence", floor=0.5)
    assert gate.admit(_Candidate(confidence=0.49)) is False


def test_admit_missing_attribute_treated_as_zero():
    gate = ConfidenceGate(field="confidence", floor=0.5)
    assert gate.admit(_NoField()) is False


def test_admit_missing_attribute_with_zero_floor_admitted():
    gate = ConfidenceGate(field="confidence", floor=0.0)
    assert gate.admit(_NoField()) is True


def test_admit_does_not_mutate_candidate():
    cand = _Candidate(confidence=0.3)
    gate = ConfidenceGate(field="confidence", floor=0.5)
    gate.admit(cand)
    assert cand.confidence == 0.3


# ─── ConfidenceGate.admit_value ────────────────────────────────────────────────


def test_admit_value_above_and_below():
    gate = ConfidenceGate(field="confidence", floor=0.7)
    assert gate.admit_value(0.71) is True
    assert gate.admit_value(0.7) is True
    assert gate.admit_value(0.69) is False


def test_admit_value_non_numeric_treated_as_zero():
    gate = ConfidenceGate(field="confidence", floor=0.5)
    assert gate.admit_value("not-a-number") is False  # type: ignore[arg-type]


def test_admit_value_non_numeric_with_zero_floor():
    gate = ConfidenceGate(field="confidence", floor=0.0)
    assert gate.admit_value(None) is True  # type: ignore[arg-type]


# ─── ConfidenceGate config ─────────────────────────────────────────────────────


def test_on_reject_default_is_drop():
    gate = ConfidenceGate(field="confidence", floor=0.5)
    assert gate.on_reject == "drop"


def test_on_reject_flag_low_confidence():
    gate = ConfidenceGate(
        field="confidence", floor=0.5, on_reject="flag_low_confidence"
    )
    assert gate.on_reject == "flag_low_confidence"


def test_floor_coerced_to_float():
    gate = ConfidenceGate(field="confidence", floor=1)
    assert isinstance(gate.floor, float)
    assert gate.floor == 1.0


# ─── assert_work_done ──────────────────────────────────────────────────────────


def test_work_done_large_input_zero_change_fails():
    res = assert_work_done(input_count=150, change_count=0)
    assert res.passed is False
    assert "150" in res.reason
    assert "threshold 100" in res.reason


def test_work_done_large_input_with_change_passes():
    res = assert_work_done(input_count=150, change_count=3)
    assert res.passed is True
    assert res.reason == ""


def test_work_done_at_threshold_passes():
    # Strict `>` semantics: input_count == threshold is not "large".
    res = assert_work_done(input_count=100, change_count=0)
    assert res.passed is True


def test_work_done_small_input_zero_change_passes():
    res = assert_work_done(input_count=10, change_count=0)
    assert res.passed is True


def test_work_done_custom_threshold():
    assert assert_work_done(11, 0, threshold=10).passed is False
    assert assert_work_done(10, 0, threshold=10).passed is True


def test_work_done_unpacks_as_tuple():
    passed, reason = assert_work_done(150, 0)
    assert passed is False
    assert isinstance(reason, str)


# ─── assert_finish_complete ────────────────────────────────────────────────────


def test_finish_complete_stop_passes():
    assert assert_finish_complete("stop").passed is True


def test_finish_complete_none_passes():
    assert assert_finish_complete(None).passed is True


def test_finish_complete_empty_passes():
    assert assert_finish_complete("").passed is True


def test_finish_complete_length_fails():
    res = assert_finish_complete("length")
    assert res.passed is False
    assert "length" in res.reason


def test_finish_complete_max_tokens_fails():
    assert assert_finish_complete("max_tokens").passed is False


def test_finish_complete_max_output_tokens_fails():
    assert assert_finish_complete("max_output_tokens").passed is False


def test_finish_complete_case_and_whitespace_insensitive():
    assert assert_finish_complete("  LENGTH  ").passed is False
    assert assert_finish_complete("Max_Tokens").passed is False


# ─── assert_nonempty_when_expected ─────────────────────────────────────────────


def test_nonempty_expected_and_present_passes():
    assert assert_nonempty_when_expected([1, 2], expected=True).passed is True


def test_nonempty_expected_but_empty_fails():
    res = assert_nonempty_when_expected([], expected=True)
    assert res.passed is False
    assert res.reason


def test_nonempty_expected_but_none_fails():
    assert assert_nonempty_when_expected(None, expected=True).passed is False


def test_nonempty_not_expected_empty_passes():
    assert assert_nonempty_when_expected([], expected=False).passed is True


def test_nonempty_not_expected_present_passes():
    assert assert_nonempty_when_expected([1], expected=False).passed is True


# ─── VerifyResult shape ────────────────────────────────────────────────────────


def test_verify_result_is_namedtuple():
    res = VerifyResult(True, "")
    assert res.passed is True
    assert res.reason == ""
    # Tuple unpacking works.
    passed, reason = res
    assert passed is True
    assert reason == ""
