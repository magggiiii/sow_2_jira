# tests/test_node_capping.py
"""
A2: graceful max_nodes capping.

A 50-60pg SOW can index past the max_nodes cap. The legacy behavior raised a
hard RuntimeError with NO partial output (Denial-of-Wallet protection). A2 makes
the default "degraded" strategy cap the node list, keep partial output, and flag
the run — while "strict" preserves the legacy hard-stop. ``cap_nodes`` is the
pure decision; these tests pin both strategies without running a pipeline.
"""

from __future__ import annotations

import pytest

from pipeline.orchestrator import cap_nodes
from models.schemas import RunConfig, LLMMode, JiraHierarchy


def _nodes(n: int) -> list[dict]:
    return [{"node_id": f"n{i}", "title": f"Node {i}"} for i in range(n)]


# ─── cap_nodes: under / at / over the cap ─────────────────────────────────────


def test_under_cap_passes_through_unchanged():
    nodes = _nodes(10)
    capped, degraded, reason = cap_nodes(nodes, max_nodes=200, strategy="degraded")
    assert capped is nodes
    assert degraded is False
    assert reason == ""


def test_at_cap_is_not_degraded():
    nodes = _nodes(200)
    capped, degraded, reason = cap_nodes(nodes, max_nodes=200, strategy="degraded")
    assert len(capped) == 200
    assert degraded is False


def test_over_cap_degraded_strategy_caps_and_flags():
    nodes = _nodes(250)
    capped, degraded, reason = cap_nodes(nodes, max_nodes=200, strategy="degraded")
    assert len(capped) == 200
    assert capped == nodes[:200]
    assert degraded is True
    assert "200" in reason and "250" in reason


def test_over_cap_strict_strategy_raises():
    nodes = _nodes(250)
    with pytest.raises(RuntimeError) as exc:
        cap_nodes(nodes, max_nodes=200, strategy="strict")
    assert "Denial of Wallet" in str(exc.value)


def test_default_strategy_is_degraded():
    """Omitting strategy must NOT raise on overflow (degraded is the default)."""
    nodes = _nodes(250)
    capped, degraded, _ = cap_nodes(nodes, max_nodes=200)
    assert len(capped) == 200
    assert degraded is True


# ─── RunConfig.node_processing_strategy field ─────────────────────────────────


def _cfg(**kw) -> dict:
    base = dict(
        sow_pdf_path="/tmp/x.pdf",
        llm_mode=LLMMode.CUSTOM,
        jira_hierarchy=JiraHierarchy.EPIC_TASK,
        jira_project_key="TEST",
    )
    base.update(kw)
    return base


def test_run_config_defaults_to_degraded_strategy():
    cfg = RunConfig(**_cfg())
    assert cfg.node_processing_strategy == "degraded"


def test_run_config_accepts_strict_strategy():
    cfg = RunConfig(**_cfg(node_processing_strategy="strict"))
    assert cfg.node_processing_strategy == "strict"


def test_run_config_rejects_unknown_strategy():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        RunConfig(**_cfg(node_processing_strategy="banana"))
