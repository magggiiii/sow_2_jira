"""Tests for core/domain/ids.py — canonical run-id factory (WAVE 1 STEP 1.5)."""

from uuid import UUID

from core.domain.ids import make_run_id


def test_make_run_id_is_full_uuid():
    rid = make_run_id()
    assert isinstance(rid, str)
    assert len(rid) == 36  # canonical hyphenated UUID form
    # Parses as a real UUID (raises ValueError otherwise).
    assert str(UUID(rid)) == rid


def test_make_run_id_is_unique():
    ids = {make_run_id() for _ in range(1000)}
    assert len(ids) == 1000


def test_run_config_run_id_is_full_uuid():
    """RunConfig().run_id must be a full 36-char UUID (no more uuid4()[:8])."""
    from models.schemas import RunConfig, LLMMode, JiraHierarchy

    cfg = RunConfig(
        sow_pdf_path="x.pdf",
        llm_mode=LLMMode.API,
        jira_hierarchy=JiraHierarchy.EPIC_TASK,
        jira_project_key="PROJ",
    )
    assert len(cfg.run_id) == 36
    assert str(UUID(cfg.run_id)) == cfg.run_id


def test_two_runs_get_distinct_ids():
    from models.schemas import RunConfig, LLMMode, JiraHierarchy

    def _cfg():
        return RunConfig(
            sow_pdf_path="same.pdf",
            llm_mode=LLMMode.API,
            jira_hierarchy=JiraHierarchy.EPIC_TASK,
            jira_project_key="PROJ",
        )

    assert _cfg().run_id != _cfg().run_id
