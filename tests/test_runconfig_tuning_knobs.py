# tests/test_runconfig_tuning_knobs.py
"""
STEP 5.4: move pipeline tuning knobs into ``RunConfig``.

Today the orchestrator reads ~11 tuning knobs straight from ``os.getenv`` (and two
from ``app_config["pipeline"]``) in ``__init__`` and a few resolver methods. STEP
5.4 promotes them to typed, optional ``RunConfig`` fields with this resolution
precedence everywhere:

    explicit config field  >  env var / app_config  >  hardcoded legacy default

Each field defaults to ``None`` (the "not specified" sentinel) so that:
  * a run with no field set and no env behaves EXACTLY as before (identical defaults),
  * an existing env-based deployment keeps working (env fallback / back-compat),
  * an old ``pipeline_output.json`` checkpoint (no fields) still loads (-> None),
  * an explicit field overrides the env.

All offline — FakeLLM/FakeAudit, no network, no PageIndex.
"""

from __future__ import annotations

import pytest

from pipeline.orchestrator import PipelineOrchestrator
from models.schemas import RunConfig, LLMMode, JiraHierarchy


# ─── Fakes (mirror the existing orchestrator tests) ────────────────────────────


class FakeLLM:
    def complete(self, *a, **k):
        return ""

    def complete_json(self, *a, **k):
        return []


class FakeAudit:
    def __init__(self):
        self.entries: list[dict] = []

    def log(self, **kwargs):
        self.entries.append(kwargs)


# Every env knob STEP 5.4 promotes. Cleared by default so tests are isolated from
# the ambient shell environment.
TUNING_ENV_VARS = [
    "EXTRACTION_CONFIDENCE_THRESHOLD",
    "DEDUP_SIMILARITY_THRESHOLD",
    "SOW_CLASSIFIER_ENABLED",
    "SOW_ENABLE_CRITIC",
    "SOW_SEMANTIC_COVERAGE",
    "SOW_CRITIC_THRESHOLD",
    "SOW_NODE_CONCURRENCY",
    "SOW_COVERAGE_MIN_CONFIDENCE",
    "SOW_COVERAGE_CORPUS_FILTER",
]


def _clear_env(monkeypatch):
    for v in TUNING_ENV_VARS:
        monkeypatch.delenv(v, raising=False)


def _build_orch(app_config=None, **config_kwargs):
    base = dict(
        run_id="knob-test",
        sow_pdf_path="/tmp/x.pdf",
        llm_mode=LLMMode.CUSTOM,
        jira_hierarchy=JiraHierarchy.EPIC_TASK,
        jira_project_key="TEST",
    )
    base.update(config_kwargs)
    config = RunConfig(**base)
    app_config = app_config or {
        "pipeline": {"max_gap_recovery_iterations": 2, "max_section_chars": 16000}
    }
    return PipelineOrchestrator(
        config=config, app_config=app_config, audit=FakeAudit(), llm=FakeLLM()
    )


# ─── 1. Fields exist on the schema and default to None ─────────────────────────

NEW_FIELDS = [
    "extraction_confidence_threshold",
    "dedup_similarity_threshold",
    "classifier_enabled",
    "critic_enabled",
    "semantic_coverage_enabled",
    "critic_threshold",
    "max_section_chars",
    "max_gap_recovery_iterations",
    "coverage_min_confidence",
    "coverage_corpus_filter",
    "node_concurrency",
]


@pytest.mark.parametrize("field", NEW_FIELDS)
def test_runconfig_has_tuning_field_defaulting_to_none(field):
    assert field in RunConfig.model_fields, f"RunConfig missing tuning field {field!r}"
    cfg = RunConfig(
        sow_pdf_path="/tmp/x.pdf",
        llm_mode=LLMMode.CUSTOM,
        jira_hierarchy=JiraHierarchy.EPIC_TASK,
        jira_project_key="TEST",
    )
    assert getattr(cfg, field) is None


def test_old_checkpoint_dict_without_new_fields_still_loads():
    # A pre-5.4 pipeline_output.json config block has none of the new keys; it must
    # still deserialize, leaving every knob None (-> legacy resolution at runtime).
    legacy = {
        "sow_pdf_path": "/tmp/x.pdf",
        "llm_mode": "custom",
        "jira_hierarchy": "epic_task",
        "jira_project_key": "TEST",
        "run_id": "legacy01",
    }
    cfg = RunConfig.model_validate(legacy)
    for field in NEW_FIELDS:
        assert getattr(cfg, field) is None


def test_runconfig_round_trips_with_new_fields_set():
    cfg = RunConfig(
        sow_pdf_path="/tmp/x.pdf",
        llm_mode=LLMMode.CUSTOM,
        jira_hierarchy=JiraHierarchy.EPIC_TASK,
        jira_project_key="TEST",
        extraction_confidence_threshold=0.42,
        node_concurrency=3,
        classifier_enabled=False,
    )
    restored = RunConfig.model_validate(cfg.model_dump(mode="json"))
    assert restored.extraction_confidence_threshold == 0.42
    assert restored.node_concurrency == 3
    assert restored.classifier_enabled is False


# ─── 2. Defaults unchanged: field None + env unset == legacy behavior ──────────


def test_defaults_identical_when_nothing_set(monkeypatch):
    _clear_env(monkeypatch)
    orch = _build_orch()
    assert orch.extraction_agent.confidence_threshold == 0.6
    assert orch.extraction_agent.max_section_chars == 16000
    assert orch.dedup_agent.threshold == 0.85
    assert orch.gap_agent.max_iterations == 2  # from app_config
    assert orch.classifier is not None
    assert orch.critic is not None
    assert orch.critic.auto_fix_threshold == 0.8
    assert orch.coverage_checker is not None
    assert orch._node_concurrency() == 6
    assert orch._coverage_floor() == 0.6  # dynamic default == coverage_checker.min_confidence
    assert orch._coverage_filter_enabled() is False


# ─── 3. Explicit config field wins (over both default and env) ─────────────────


def test_config_field_overrides_default_and_env(monkeypatch):
    # Env set to one value, config field to another: config must win.
    monkeypatch.setenv("EXTRACTION_CONFIDENCE_THRESHOLD", "0.11")
    monkeypatch.setenv("DEDUP_SIMILARITY_THRESHOLD", "0.22")
    monkeypatch.setenv("SOW_CRITIC_THRESHOLD", "0.33")
    monkeypatch.setenv("SOW_NODE_CONCURRENCY", "9")
    monkeypatch.setenv("SOW_COVERAGE_MIN_CONFIDENCE", "0.44")
    monkeypatch.setenv("SOW_COVERAGE_CORPUS_FILTER", "1")
    orch = _build_orch(
        extraction_confidence_threshold=0.7,
        dedup_similarity_threshold=0.9,
        critic_threshold=0.55,
        max_section_chars=9000,
        max_gap_recovery_iterations=5,
        coverage_min_confidence=0.5,
        coverage_corpus_filter=False,
        node_concurrency=3,
    )
    assert orch.extraction_agent.confidence_threshold == 0.7
    assert orch.extraction_agent.max_section_chars == 9000
    assert orch.dedup_agent.threshold == 0.9
    assert orch.gap_agent.max_iterations == 5
    assert orch.critic.auto_fix_threshold == 0.55
    assert orch._node_concurrency() == 3
    assert orch._coverage_floor() == 0.5
    assert orch._coverage_filter_enabled() is False  # config False beats env "1"


def test_config_toggles_override_enable_flags(monkeypatch):
    # Env says enabled; config explicitly disables each intelligence agent.
    monkeypatch.setenv("SOW_CLASSIFIER_ENABLED", "1")
    monkeypatch.setenv("SOW_ENABLE_CRITIC", "1")
    monkeypatch.setenv("SOW_SEMANTIC_COVERAGE", "1")
    orch = _build_orch(
        classifier_enabled=False,
        critic_enabled=False,
        semantic_coverage_enabled=False,
    )
    assert orch.classifier is None
    assert orch.critic is None
    assert orch.coverage_checker is None


def test_config_toggle_true_overrides_env_disable(monkeypatch):
    # Env disables; config explicitly re-enables.
    monkeypatch.setenv("SOW_CLASSIFIER_ENABLED", "0")
    monkeypatch.setenv("SOW_ENABLE_CRITIC", "0")
    monkeypatch.setenv("SOW_SEMANTIC_COVERAGE", "0")
    orch = _build_orch(
        classifier_enabled=True,
        critic_enabled=True,
        semantic_coverage_enabled=True,
    )
    assert orch.classifier is not None
    assert orch.critic is not None
    assert orch.coverage_checker is not None


# ─── 4. Env fallback preserved when the config field is None (back-compat) ─────


def test_env_fallback_when_field_none(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("EXTRACTION_CONFIDENCE_THRESHOLD", "0.15")
    monkeypatch.setenv("DEDUP_SIMILARITY_THRESHOLD", "0.25")
    monkeypatch.setenv("SOW_CRITIC_THRESHOLD", "0.35")
    monkeypatch.setenv("SOW_NODE_CONCURRENCY", "8")
    monkeypatch.setenv("SOW_COVERAGE_MIN_CONFIDENCE", "0.45")
    monkeypatch.setenv("SOW_COVERAGE_CORPUS_FILTER", "1")
    orch = _build_orch()  # all knobs None -> env path
    assert orch.extraction_agent.confidence_threshold == 0.15
    assert orch.dedup_agent.threshold == 0.25
    assert orch.critic.auto_fix_threshold == 0.35
    assert orch._node_concurrency() == 8
    assert orch._coverage_floor() == 0.45
    assert orch._coverage_filter_enabled() is True


def test_env_toggle_disable_fallback_when_field_none(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("SOW_CLASSIFIER_ENABLED", "0")
    monkeypatch.setenv("SOW_ENABLE_CRITIC", "0")
    monkeypatch.setenv("SOW_SEMANTIC_COVERAGE", "0")
    orch = _build_orch()  # toggles None -> env path disables
    assert orch.classifier is None
    assert orch.critic is None
    assert orch.coverage_checker is None


# ─── 5. app_config fallback for the two non-env knobs ──────────────────────────


def test_app_config_fallback_for_non_env_knobs(monkeypatch):
    _clear_env(monkeypatch)
    orch = _build_orch(
        app_config={"pipeline": {"max_gap_recovery_iterations": 4, "max_section_chars": 12000}}
    )
    assert orch.gap_agent.max_iterations == 4
    assert orch.extraction_agent.max_section_chars == 12000


def test_node_concurrency_config_is_floored_to_one(monkeypatch):
    _clear_env(monkeypatch)
    orch = _build_orch(node_concurrency=0)
    # Mirrors the legacy max(1, ...) floor so "1 == sequential" stays the minimum.
    assert orch._node_concurrency() == 1
