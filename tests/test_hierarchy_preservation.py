# tests/test_hierarchy_preservation.py
"""
Unit coverage for Improvement #1 (Wave 1A): hierarchy preservation through
indexer -> SourceRef -> JiraClient grouping.

The three behaviours under test:
1. flatten_tree() emits parent_id, parent_chain, depth, and node_index.
2. SourceRef built in TaskStateAgent.process carries the hierarchy fields.
3. JiraClient.push_tasks groups containers by parent_id, not section_title.
"""

from __future__ import annotations

import pathlib
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from models.schemas import (
    JiraHierarchy,
    ManagedTask,
    RawTask,
    SourceRef,
    TaskStatus,
)


# ─── Helpers ────────────────────────────────────────────────────────────────

class _DummyAudit:
    def log(self, **kwargs):
        return None


def _make_tree():
    """
    Three-level fake PageIndex tree:

    root_a
    ├── child_a1
    │   └── leaf_a1a
    └── child_a2
    root_b
    """
    return [
        {
            "node_id": "root_a",
            "title": "Root A",
            "start_index": 1,
            "end_index": 10,
            "summary": "root a summary",
            "text": "root a text",
            "nodes": [
                {
                    "node_id": "child_a1",
                    "title": "Child A1",
                    "start_index": 2,
                    "end_index": 5,
                    "summary": "child a1 summary",
                    "text": "child a1 text",
                    "nodes": [
                        {
                            "node_id": "leaf_a1a",
                            "title": "Leaf A1a",
                            "start_index": 3,
                            "end_index": 4,
                            "summary": "leaf a1a summary",
                            "text": "leaf a1a text",
                            "nodes": [],
                        }
                    ],
                },
                {
                    "node_id": "child_a2",
                    "title": "Child A2",
                    "start_index": 6,
                    "end_index": 10,
                    "summary": "child a2 summary",
                    "text": "child a2 text",
                    "nodes": [],
                },
            ],
        },
        {
            "node_id": "root_b",
            "title": "Root B",
            "start_index": 11,
            "end_index": 20,
            "summary": "root b summary",
            "text": "root b text",
            "nodes": [],
        },
    ]


# ─── Tests ──────────────────────────────────────────────────────────────────

def test_flatten_tree_emits_parent_id():
    from pipeline.indexer import DocumentIndexer

    indexer = DocumentIndexer.__new__(DocumentIndexer)
    indexer.last_tree = None
    indexer.last_result = None
    indexer.node_index_map = {}
    indexer.model = "test"
    indexer.config = {}

    flat = indexer.flatten_tree(_make_tree())
    by_id = {n["node_id"]: n for n in flat}

    # All five nodes flattened
    assert set(by_id.keys()) == {"root_a", "child_a1", "leaf_a1a", "child_a2", "root_b"}

    # Depth-first ordering preserved via node_index
    order = [n["node_id"] for n in sorted(flat, key=lambda n: n["node_index"])]
    assert order == ["root_a", "child_a1", "leaf_a1a", "child_a2", "root_b"]

    # Roots
    assert by_id["root_a"]["parent_id"] is None
    assert by_id["root_a"]["parent_chain"] == []
    assert by_id["root_a"]["depth"] == 0
    assert by_id["root_b"]["parent_id"] is None
    assert by_id["root_b"]["depth"] == 0

    # Level 1 — parent is root_a
    assert by_id["child_a1"]["parent_id"] == "root_a"
    assert by_id["child_a1"]["parent_chain"] == ["root_a"]
    assert by_id["child_a1"]["depth"] == 1

    assert by_id["child_a2"]["parent_id"] == "root_a"
    assert by_id["child_a2"]["parent_chain"] == ["root_a"]
    assert by_id["child_a2"]["depth"] == 1

    # Level 2 — full ancestry
    assert by_id["leaf_a1a"]["parent_id"] == "child_a1"
    assert by_id["leaf_a1a"]["parent_chain"] == ["root_a", "child_a1"]
    assert by_id["leaf_a1a"]["depth"] == 2

    # Page/text passthrough still works
    assert by_id["leaf_a1a"]["page_start"] == 3
    assert by_id["leaf_a1a"]["page_end"] == 4
    assert by_id["leaf_a1a"]["text"] == "leaf a1a text"


def test_flatten_tree_handles_empty_and_legacy_inputs():
    from pipeline.indexer import DocumentIndexer

    indexer = DocumentIndexer.__new__(DocumentIndexer)
    indexer.last_tree = None
    indexer.last_result = None
    indexer.node_index_map = {}
    indexer.model = "test"
    indexer.config = {}

    # Empty tree -> empty list (must not crash)
    assert indexer.flatten_tree(None) == []
    assert indexer.flatten_tree([]) == []

    # Single root node with no children
    flat = indexer.flatten_tree([{
        "node_id": "solo",
        "title": "Solo",
        "start_index": 1,
        "end_index": 2,
    }])
    assert len(flat) == 1
    assert flat[0]["parent_id"] is None
    assert flat[0]["parent_chain"] == []
    assert flat[0]["depth"] == 0
    assert flat[0]["node_index"] == 0


def test_source_ref_carries_parent_chain():
    from pipeline.agents.state import TaskStateAgent

    agent = TaskStateAgent(audit_logger=_DummyAudit(), run_id="r1")

    node_with_hierarchy = {
        "node_id": "leaf_a1a",
        "title": "Leaf A1a",
        "page_start": 3,
        "page_end": 4,
        "parent_id": "child_a1",
        "parent_chain": ["root_a", "child_a1"],
        "depth": 2,
    }

    raw = RawTask(
        title="Build login form",
        short_description="Implement the login form per spec",
        confidence=0.9,
    )

    updated_open, newly_closed = agent.process([raw], [], node_with_hierarchy)
    assert len(updated_open) == 1
    task = updated_open[0]
    assert len(task.source_refs) == 1
    ref = task.source_refs[0]
    assert ref.node_id == "leaf_a1a"
    assert ref.parent_id == "child_a1"
    assert ref.parent_chain == ["root_a", "child_a1"]
    assert ref.depth == 2
    assert newly_closed == []


def test_source_ref_defaults_when_node_missing_hierarchy():
    """Backward compat: legacy pipeline_output.json nodes lack parent_id."""
    from pipeline.agents.state import TaskStateAgent

    agent = TaskStateAgent(audit_logger=_DummyAudit(), run_id="r1")

    legacy_node = {
        "node_id": "old_node",
        "title": "Legacy Section",
        "page_start": 1,
        "page_end": 2,
    }
    raw = RawTask(
        title="Something",
        short_description="anything",
        confidence=0.8,
    )

    updated_open, _ = agent.process([raw], [], legacy_node)
    ref = updated_open[0].source_refs[0]
    assert ref.parent_id is None
    assert ref.parent_chain == []
    assert ref.depth == 0


def test_jira_grouping_uses_parent_id():
    """
    Two tasks share section_title 'Implementation' but live under different
    parents. The new structural grouping must put them in different epics.
    """
    from integrations.jira_client import JiraClient

    node_index = {
        "root_a": {"title": "Backend Module", "parent_id": None, "depth": 0},
        "root_b": {"title": "Frontend Module", "parent_id": None, "depth": 0},
        "section_a": {"title": "Implementation", "parent_id": "root_a", "depth": 1},
        "section_b": {"title": "Implementation", "parent_id": "root_b", "depth": 1},
    }

    # Build two tasks: same section_title, different parent_chains
    task_a = ManagedTask(
        id=uuid4(),
        title="Backend impl task",
        short_description="x",
        confidence=0.9,
        status=TaskStatus.CLOSED,
        source_refs=[SourceRef(
            node_id="section_a",
            section_title="Implementation",
            page_start=2, page_end=3,
            parent_id="root_a",
            parent_chain=["root_a"],
            depth=1,
        )],
    )
    task_b = ManagedTask(
        id=uuid4(),
        title="Frontend impl task",
        short_description="y",
        confidence=0.9,
        status=TaskStatus.CLOSED,
        source_refs=[SourceRef(
            node_id="section_b",
            section_title="Implementation",
            page_start=11, page_end=12,
            parent_id="root_b",
            parent_chain=["root_b"],
            depth=1,
        )],
    )

    # Stand up the client without touching env or the real JIRA SDK.
    with patch.dict("os.environ", {
        "JIRA_SERVER": "https://fake.atlassian.net",
        "JIRA_EMAIL": "x@x.test",
        "JIRA_API_TOKEN": "fake",
    }, clear=False), patch("integrations.jira_client.JIRA") as JiraSDK:
        JiraSDK.return_value = MagicMock()
        client = JiraClient(
            hierarchy=JiraHierarchy.EPIC_TASK,
            audit=_DummyAudit(),
            run_id="test-run",
            project_key="TEST",
            node_index=node_index,
        )

    # Pretend project validation succeeds with Epic + Task types.
    client._validate_project = MagicMock(return_value={"Epic", "Task"})
    client.available_issue_types = {"Epic", "Task"}

    # Track which titles get created as containers.
    created_containers: list[tuple[str, str]] = []  # (title, key)

    def fake_create_container(section_title: str, issue_type: str):
        key = f"EPIC-{len(created_containers) + 1}"
        created_containers.append((section_title, key))
        return key

    created_tasks: list[tuple[str, str | None]] = []

    def fake_create_task(task, parent_key, issue_type):
        from models.schemas import JiraPushResult
        created_tasks.append((task.title, parent_key))
        return JiraPushResult(
            task_id=task.id,
            success=True,
            jira_issue_key=f"TEST-{len(created_tasks)}",
        )

    client._create_container = fake_create_container
    client._create_task = fake_create_task

    client.push_tasks([task_a, task_b])

    # Two distinct containers must be created — proves grouping is structural.
    assert len(created_containers) == 2
    container_titles = [c[0] for c in created_containers]
    container_keys = [c[1] for c in created_containers]
    # Titles come from the node_index (root titles), not the section_title.
    assert "Backend Module" in container_titles
    assert "Frontend Module" in container_titles

    # Each task gets routed to a different parent.
    parents_by_task = dict(created_tasks)
    assert parents_by_task["Backend impl task"] != parents_by_task["Frontend impl task"]
    assert parents_by_task["Backend impl task"] in container_keys
    assert parents_by_task["Frontend impl task"] in container_keys


def test_jira_grouping_legacy_tasks_fall_back_to_section_title():
    """
    Backward compat: tasks loaded from an old pipeline_output.json have
    SourceRef.parent_id=None/parent_chain=[]; the client must not crash and
    should fall back to grouping by source node (legacy behaviour) using the
    section title as the container summary.
    """
    from integrations.jira_client import JiraClient

    legacy_task = ManagedTask(
        id=uuid4(),
        title="Old style task",
        short_description="x",
        confidence=0.9,
        status=TaskStatus.CLOSED,
        source_refs=[SourceRef(
            node_id="legacy_node",
            section_title="Legacy Section",
            page_start=1, page_end=2,
        )],
    )

    with patch.dict("os.environ", {
        "JIRA_SERVER": "https://fake.atlassian.net",
        "JIRA_EMAIL": "x@x.test",
        "JIRA_API_TOKEN": "fake",
    }, clear=False), patch("integrations.jira_client.JIRA") as JiraSDK:
        JiraSDK.return_value = MagicMock()
        # Empty node_index simulates an in-flight run started before this PR.
        client = JiraClient(
            hierarchy=JiraHierarchy.EPIC_TASK,
            audit=_DummyAudit(),
            run_id="test-run",
            project_key="TEST",
            node_index={},
        )

    client._validate_project = MagicMock(return_value={"Epic", "Task"})
    client.available_issue_types = {"Epic", "Task"}

    created_containers: list[tuple[str, str]] = []

    def fake_create_container(section_title: str, issue_type: str):
        key = f"EPIC-{len(created_containers) + 1}"
        created_containers.append((section_title, key))
        return key

    def fake_create_task(task, parent_key, issue_type):
        from models.schemas import JiraPushResult
        return JiraPushResult(
            task_id=task.id,
            success=True,
            jira_issue_key="TEST-1",
        )

    client._create_container = fake_create_container
    client._create_task = fake_create_task

    results = client.push_tasks([legacy_task])

    # Did not crash, did create exactly one container.
    assert len(results) == 1
    assert results[0].success
    assert len(created_containers) == 1
    # Fell back to section_title for the container summary.
    assert created_containers[0][0] == "Legacy Section"
