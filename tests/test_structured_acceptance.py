# tests/test_structured_acceptance.py
"""
Wave 1B coverage: structured acceptance criteria + task dependencies.

Three things under test:
1. normalize_acceptance_criteria handles all three input shapes (str, dict,
   AcceptanceCriterion) and feeds the ManagedTask validator the right type.
2. ManagedTask carries TaskDependency through the state-agent _promote path.
3. JiraClient renders structured ACs in Jira wiki markup and only calls
   create_issue_link for dependency target_refs that resolve.
"""

from __future__ import annotations

import pathlib
import sys
from unittest.mock import MagicMock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from models.schemas import (
    AcceptanceCriterion,
    AcceptanceCriterionType,
    JiraHierarchy,
    JiraPushResult,
    ManagedTask,
    RawTask,
    SourceRef,
    TaskDependency,
    normalize_acceptance_criteria,
)

# ─── Dummy collaborators ─────────────────────────────────────────────────────

class _DummyAudit:
    def log(self, **kwargs):
        return None


def _node(node_id: str = "n1", title: str = "Section A") -> dict:
    return {
        "node_id": node_id,
        "title": title,
        "page_start": 1,
        "page_end": 2,
        "parent_id": None,
        "parent_chain": [],
        "depth": 0,
    }


def _source_ref(node_id: str = "n1") -> SourceRef:
    return SourceRef(
        node_id=node_id,
        section_title="Section A",
        page_start=1,
        page_end=2,
        snippet="",
    )


# ─── normalize_acceptance_criteria ───────────────────────────────────────────

def test_normalize_string_acceptance_criteria():
    out = normalize_acceptance_criteria(["[ ] foo", "[ ] bar"])
    assert out is not None
    assert len(out) == 2
    assert all(isinstance(x, AcceptanceCriterion) for x in out)
    assert all(x.type == AcceptanceCriterionType.FUNCTIONAL for x in out)
    assert all(x.verified_by == "test" for x in out)
    assert out[0].condition == "[ ] foo"


def test_normalize_dict_acceptance_criteria():
    out = normalize_acceptance_criteria(
        [{"condition": "x", "type": "security", "verified_by": "review"}]
    )
    assert out is not None
    assert len(out) == 1
    assert out[0].condition == "x"
    assert out[0].type == AcceptanceCriterionType.SECURITY
    assert out[0].verified_by == "review"


def test_normalize_already_structured():
    ac = AcceptanceCriterion(condition="y", type=AcceptanceCriterionType.PERFORMANCE)
    out = normalize_acceptance_criteria([ac])
    assert out is not None
    assert len(out) == 1
    assert out[0] is ac


def test_normalize_mixed_and_empty():
    out = normalize_acceptance_criteria(
        ["plain", {"condition": "dict-form"}, AcceptanceCriterion(condition="obj")]
    )
    assert out is not None
    assert [x.condition for x in out] == ["plain", "dict-form", "obj"]
    # Empties and Nones round-trip to None
    assert normalize_acceptance_criteria(None) is None
    assert normalize_acceptance_criteria([]) is None
    # Whitespace-only strings dropped
    assert normalize_acceptance_criteria(["   "]) is None


def test_managed_task_legacy_string_acs_normalize_via_validator():
    """Critical backward-compat: old pipeline_output.json shape must still load."""
    mt = ManagedTask.model_validate(
        {
            "title": "t",
            "short_description": "d",
            "acceptance_criteria": ["[ ] legacy form"],
            "confidence": 0.9,
        }
    )
    assert mt.acceptance_criteria is not None
    assert len(mt.acceptance_criteria) == 1
    assert isinstance(mt.acceptance_criteria[0], AcceptanceCriterion)
    assert mt.acceptance_criteria[0].condition == "[ ] legacy form"


# ─── State agent: dependencies carry through promotion ───────────────────────

def test_managed_task_carries_dependencies():
    from pipeline.agents.state import TaskStateAgent

    deps = [
        TaskDependency(target_ref="Other task", reason="needs JWT first", kind="blocks"),
        TaskDependency(target_ref="Audit log", reason="for observability"),
    ]
    raw = RawTask(
        title="Implement auth",
        short_description="do it",
        acceptance_criteria=["[ ] returns 200"],
        confidence=0.8,
        dependencies=deps,
    )
    agent = TaskStateAgent(audit_logger=_DummyAudit(), run_id="r1")
    open_tasks, closed = agent.process([raw], [], _node())
    assert len(open_tasks) == 1
    managed = open_tasks[0]
    assert managed.title == "Implement auth"
    assert len(managed.dependencies) == 2
    assert managed.dependencies[0].target_ref == "Other task"
    assert managed.dependencies[0].kind == "blocks"
    # ACs normalized to structured form
    assert isinstance(managed.acceptance_criteria[0], AcceptanceCriterion)


def test_state_agent_merge_dedupes_acs_and_deps():
    """Continuation merges should drop case-duplicate ACs and (target_ref, kind) duplicate deps."""
    from pipeline.agents.state import TaskStateAgent

    existing = ManagedTask(
        title="Build pipeline",
        short_description="initial",
        acceptance_criteria=[AcceptanceCriterion(condition="Latency < 1s")],
        confidence=0.7,
        continues_to_next=True,
        source_refs=[_source_ref()],
        dependencies=[TaskDependency(target_ref="Init DB", reason="schema", kind="blocks")],
    )
    incoming = RawTask(
        title="Build pipeline",  # similar enough to trigger continuation
        short_description="more details",
        acceptance_criteria=[
            "latency < 1s",  # duplicate (case-insensitive)
            {"condition": "Memory < 100MB", "type": "performance"},
        ],
        confidence=0.8,
        continues_to_next=False,
        dependencies=[
            {"target_ref": "Init DB", "reason": "same again", "kind": "blocks"},  # dup
            {"target_ref": "Provision worker", "reason": "needs runtime", "kind": "blocks"},
        ],
    )
    agent = TaskStateAgent(audit_logger=_DummyAudit(), run_id="r1")
    open_tasks, _ = agent.process([incoming], [existing], _node(node_id="n2", title="Section B"))
    # Continuation matched existing -> merge happened, task should still exist
    merged = next((t for t in open_tasks if t.title == "Build pipeline"), None)
    assert merged is not None
    # AC count = 2 (one carried, one new)
    assert len(merged.acceptance_criteria) == 2
    conditions = {ac.condition.lower() for ac in merged.acceptance_criteria}
    assert "latency < 1s" in conditions
    assert "memory < 100mb" in conditions
    # Deps deduped to 2
    targets = {d.target_ref for d in merged.dependencies}
    assert targets == {"Init DB", "Provision worker"}


# ─── Jira description rendering ──────────────────────────────────────────────

def _make_jira_client(node_index=None):
    """
    Build a JiraClient instance without invoking JIRA() — we just need the
    methods. Patch the JIRA constructor.
    """
    import os
    from unittest.mock import patch
    os.environ.setdefault("JIRA_SERVER", "http://example")
    os.environ.setdefault("JIRA_EMAIL", "u@e")
    os.environ.setdefault("JIRA_API_TOKEN", "tok")

    from integrations.jira_client import JiraClient
    with patch("integrations.jira_client.JIRA") as mock_jira_cls:
        mock_jira = MagicMock()
        mock_jira_cls.return_value = mock_jira
        client = JiraClient(
            hierarchy=JiraHierarchy.FLAT,
            audit=_DummyAudit(),
            run_id="r1",
            project_key="TEST",
            node_index=node_index or {},
        )
    return client, mock_jira


def test_jira_description_renders_structured_ac():
    client, _ = _make_jira_client()
    task = ManagedTask(
        title="Build login",
        short_description="JWT login flow",
        acceptance_criteria=[
            AcceptanceCriterion(condition="POST /login returns 200 with token"),
            AcceptanceCriterion(
                condition="Brute force throttled after 5 attempts",
                type=AcceptanceCriterionType.SECURITY,
                verified_by="review",
            ),
        ],
        confidence=0.9,
        source_refs=[_source_ref()],
    )
    desc = client._build_description(task)
    # Both conditions rendered
    assert "POST /login returns 200 with token" in desc
    assert "Brute force throttled after 5 attempts" in desc
    # Default-typed AC has no italic annotation
    assert "type: functional" not in desc
    # Non-default AC has the italic annotation
    assert "type: security" in desc
    assert "verified by: review" in desc
    # Wiki-markup checkbox prefix is present
    assert "* [ ] POST /login returns 200" in desc


def test_jira_description_handles_legacy_string_ac():
    """If somehow a legacy plain string survives normalization, render it."""
    client, _ = _make_jira_client()
    task = ManagedTask(
        title="X",
        short_description="d",
        acceptance_criteria=[AcceptanceCriterion(condition="placeholder")],
        confidence=0.5,
        source_refs=[_source_ref()],
    )
    # Smuggle plain strings in by direct attribute assignment (Pydantic v2
    # allows this and skips re-validation), so we exercise the defensive
    # renderer branch without losing the typed source_refs.
    object.__setattr__(task, "acceptance_criteria", ["[ ] raw legacy"])
    desc = client._build_description(task)
    assert "[ ] raw legacy" in desc


# ─── Dependency link resolution ──────────────────────────────────────────────

def test_dependency_link_resolution_creates_links_for_resolvable_refs():
    client, mock_jira = _make_jira_client()
    task_a = ManagedTask(
        title="Implement user auth API",
        short_description="JWT issuer",
        confidence=0.9,
        source_refs=[_source_ref()],
    )
    task_b = ManagedTask(
        title="Build login page",
        short_description="UI",
        confidence=0.9,
        source_refs=[_source_ref()],
        dependencies=[
            TaskDependency(target_ref="Implement user auth API", reason="needs JWT", kind="blocks"),
            TaskDependency(target_ref="Nonexistent task", reason="oops", kind="blocks"),
        ],
    )
    results = [
        JiraPushResult(task_id=task_a.id, success=True, jira_issue_key="PROJ-1"),
        JiraPushResult(task_id=task_b.id, success=True, jira_issue_key="PROJ-2"),
    ]
    client._create_dependency_links([task_a, task_b], results)
    # One link created (for resolvable target_ref), one skipped
    assert mock_jira.create_issue_link.call_count == 1
    args, kwargs = mock_jira.create_issue_link.call_args
    assert kwargs["type"] == "Blocks"
    assert kwargs["inwardIssue"] == "PROJ-2"
    assert kwargs["outwardIssue"] == "PROJ-1"


def test_dependency_link_resolution_skips_when_source_push_failed():
    client, mock_jira = _make_jira_client()
    task_a = ManagedTask(
        title="Target", short_description="x", confidence=0.5, source_refs=[_source_ref()],
    )
    task_b = ManagedTask(
        title="Source",
        short_description="x",
        confidence=0.5,
        source_refs=[_source_ref()],
        dependencies=[TaskDependency(target_ref="Target", reason="r", kind="blocks")],
    )
    results = [
        JiraPushResult(task_id=task_a.id, success=True, jira_issue_key="PROJ-10"),
        JiraPushResult(task_id=task_b.id, success=False, error="boom"),  # failed push
    ]
    client._create_dependency_links([task_a, task_b], results)
    # Source has no jira_issue_key, so no link attempted
    assert mock_jira.create_issue_link.call_count == 0


def test_dependency_link_resolution_records_warning_on_link_failure():
    client, mock_jira = _make_jira_client()
    mock_jira.create_issue_link.side_effect = RuntimeError("403 forbidden")

    task_a = ManagedTask(
        title="A", short_description="x", confidence=0.5, source_refs=[_source_ref()],
    )
    task_b = ManagedTask(
        title="B",
        short_description="x",
        confidence=0.5,
        source_refs=[_source_ref()],
        dependencies=[TaskDependency(target_ref="A", reason="r", kind="blocks")],
    )
    res_b = JiraPushResult(task_id=task_b.id, success=True, jira_issue_key="PROJ-2")
    results = [
        JiraPushResult(task_id=task_a.id, success=True, jira_issue_key="PROJ-1"),
        res_b,
    ]
    client._create_dependency_links([task_a, task_b], results)
    # Did not crash; warning recorded
    assert res_b.warning is not None
    assert "403 forbidden" in res_b.warning


def test_dependency_link_resolution_maps_kind_to_link_type():
    client, mock_jira = _make_jira_client()
    task_a = ManagedTask(
        title="A", short_description="x", confidence=0.5, source_refs=[_source_ref()],
    )
    task_b = ManagedTask(
        title="B",
        short_description="x",
        confidence=0.5,
        source_refs=[_source_ref()],
        dependencies=[
            TaskDependency(target_ref="A", reason="r", kind="duplicates"),
        ],
    )
    results = [
        JiraPushResult(task_id=task_a.id, success=True, jira_issue_key="PROJ-1"),
        JiraPushResult(task_id=task_b.id, success=True, jira_issue_key="PROJ-2"),
    ]
    client._create_dependency_links([task_a, task_b], results)
    assert mock_jira.create_issue_link.call_args.kwargs["type"] == "Duplicate"
