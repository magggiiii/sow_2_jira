# tests/test_enums.py
"""
H-23: domain-model hardening. Exercises the `_NormalizedStrEnum` base that
backs the promoted closed-set fields. The base must:

- compare equal to its raw string value (behavior-preserving for existing
  `x == "merge"` style comparisons across the codebase);
- render as its value in f-strings / str() (so text rendered into Jira and
  audit logs does not regress to `Member.NAME`);
- COERCE dirty-but-known inputs via `_missing_`: mixed case, surrounding
  whitespace, and separator drift (spaces / hyphens -> underscore), plus an
  optional per-enum `_aliases()` map of normalized-string -> member;
- FAIL LOUDLY (raise ValueError) on genuinely unknown values so bad data
  surfaces instead of silently passing through.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from models.schemas import _NormalizedStrEnum, DedupDecision, DedupDecisionType


class _Sample(_NormalizedStrEnum):
    MERGE = "merge"
    KEEP_FIRST = "keep_first"
    KEEP_BOTH = "keep_both"

    @classmethod
    def _aliases(cls):
        return {"first": cls.KEEP_FIRST, "both": cls.KEEP_BOTH}


def test_is_str_subclass_and_compares_equal_to_value():
    # A str-Enum member IS a str and == its value, so existing
    # `x == "merge"` comparisons keep working unchanged.
    assert isinstance(_Sample.MERGE, str)
    assert _Sample.MERGE == "merge"
    assert _Sample.MERGE in ("merge", "keep_first")


def test_str_renders_as_value_not_member_repr():
    # Behavior-preserving for text rendered into Jira / audit strings.
    assert str(_Sample.MERGE) == "merge"
    assert f"{_Sample.MERGE}" == "merge"
    # str methods still return plain str values
    assert _Sample.MERGE.upper() == "MERGE"
    assert _Sample.KEEP_FIRST.lower() == "keep_first"


def test_exact_value_passes_through():
    assert _Sample("merge") is _Sample.MERGE
    assert _Sample("keep_first") is _Sample.KEEP_FIRST


@pytest.mark.parametrize(
    "dirty,expected",
    [
        ("MERGE", _Sample.MERGE),          # uppercase
        ("merge", _Sample.MERGE),          # already clean
        (" Merge ", _Sample.MERGE),        # whitespace + mixed case
        ("keep-first", _Sample.KEEP_FIRST),  # hyphen separator
        ("keep first", _Sample.KEEP_FIRST),  # space separator
        ("Keep_First", _Sample.KEEP_FIRST),  # mixed case underscore
        ("  KEEP-BOTH  ", _Sample.KEEP_BOTH),
    ],
)
def test_dirty_but_known_values_coerce(dirty, expected):
    assert _Sample(dirty) is expected


@pytest.mark.parametrize(
    "alias,expected",
    [
        ("first", _Sample.KEEP_FIRST),
        ("First", _Sample.KEEP_FIRST),    # aliases are matched after normalization
        ("both", _Sample.KEEP_BOTH),
        ("  BOTH ", _Sample.KEEP_BOTH),
    ],
)
def test_aliases_resolve(alias, expected):
    assert _Sample(alias) is expected


@pytest.mark.parametrize("unknown", ["teleport", "kep_first", "", "merg", "123"])
def test_unknown_value_raises(unknown):
    # Fail loudly on genuinely unknown values.
    with pytest.raises(ValueError):
        _Sample(unknown)


def test_non_string_input_raises():
    # Non-string dirty input is not coercible -> loud failure, not silent pass.
    with pytest.raises(ValueError):
        _Sample(42)


# ─── ENUM-2: DedupDecision.decision promotion (real field) ────────────────────


def test_dedup_decision_coerces_dirty_keep_both_and_compares_equal():
    """ENUM-2: a DedupDecision built with a dirty LLM value ('KEEP_BOTH')
    coerces to DedupDecisionType at the Pydantic boundary and still compares
    equal to its canonical lowercase string — so the existing
    `decision in ("merge", "keep_first")` / `== "keep_second"` checks in
    deduplication.py keep working unchanged.
    """
    d = DedupDecision(task_id_a="a", task_id_b="b", decision="KEEP_BOTH", reason="r")
    assert d.decision is DedupDecisionType.KEEP_BOTH
    assert isinstance(d.decision, DedupDecisionType)
    # str-enum equality: behavior-preserving for every existing comparison site.
    assert d.decision == "keep_both"
    # f"DEDUP_{decision.decision.upper()}" audit string must stay stable.
    assert d.decision.upper() == "KEEP_BOTH"
    assert f"{d.decision}" == "keep_both"


@pytest.mark.parametrize(
    "dirty,expected",
    [
        ("KEEP_BOTH", DedupDecisionType.KEEP_BOTH),   # uppercase underscore
        ("keep both", DedupDecisionType.KEEP_BOTH),   # space separator
        ("keepboth", DedupDecisionType.KEEP_BOTH),    # no separator -> _aliases
        ("KeepBoth", DedupDecisionType.KEEP_BOTH),    # no separator + case
        (" Merge ", DedupDecisionType.MERGE),         # whitespace + case
        ("keep-first", DedupDecisionType.KEEP_FIRST), # hyphen separator
        ("Keep_Second", DedupDecisionType.KEEP_SECOND),
    ],
)
def test_dedup_decision_field_coerces_dirty_forms(dirty, expected):
    d = DedupDecision(task_id_a="a", task_id_b="b", decision=dirty, reason="r")
    assert d.decision is expected
    assert d.decision == expected.value


def test_dedup_decision_unknown_raises():
    """An unknown decision is NOT silently coerced — Pydantic raises so bad
    LLM output surfaces instead of corrupting the merge logic.
    """
    with pytest.raises(Exception):  # pydantic ValidationError wraps ValueError
        DedupDecision(task_id_a="a", task_id_b="b", decision="teleport", reason="r")


# ─── ENUM-3: TaskDependency.kind + AcceptanceCriterion.verified_by ────────────
#
# Promote the two closed-set string fields to `_NormalizedStrEnum`. Coercion
# and the field-type change land in the SAME step (INV-1): a dirty/legacy LLM
# value must land on the right enum member at the Pydantic boundary AND still
# compare equal to the canonical lowercase string the rest of the code checks.

from unittest.mock import MagicMock, patch  # noqa: E402

from integrations.jira_client import JiraClient  # noqa: E402
from models.schemas import (  # noqa: E402
    AcceptanceCriterion,
    DependencyKind,
    JiraHierarchy,
    ManagedTask,
    TaskDependency,
    TaskStatus,
    VerifiedBy,
)


@pytest.mark.parametrize(
    "dirty,expected",
    [
        ("blocks", DependencyKind.BLOCKS),
        ("BLOCKS", DependencyKind.BLOCKS),            # uppercase
        (" Blocks ", DependencyKind.BLOCKS),          # whitespace + case
        ("relates-to", DependencyKind.RELATES_TO),    # hyphen separator
        ("relates to", DependencyKind.RELATES_TO),    # space separator
        ("RELATES_TO", DependencyKind.RELATES_TO),    # uppercase underscore
        (" Duplicates ", DependencyKind.DUPLICATES),
    ],
)
def test_dependency_kind_field_coerces_dirty_forms(dirty, expected):
    """ENUM-3: a TaskDependency built with a dirty LLM `kind` coerces to
    DependencyKind at the Pydantic boundary and still compares equal to its
    canonical lowercase string — so `(dep.kind or "blocks").lower()` dict
    lookups in jira_client.py / state.py keep working unchanged.
    """
    dep = TaskDependency(target_ref="t", reason="r", kind=dirty)
    assert dep.kind is expected
    assert isinstance(dep.kind, DependencyKind)
    # str-enum equality + the actual downstream `.lower()` shape.
    assert dep.kind == expected.value
    assert (dep.kind or "blocks").lower() == expected.value


def test_dependency_kind_unknown_raises():
    """A genuinely unknown kind fails loudly instead of silently passing."""
    with pytest.raises(Exception):  # pydantic ValidationError wraps ValueError
        TaskDependency(target_ref="t", reason="r", kind="teleports")


@pytest.mark.parametrize(
    "dirty,expected",
    [
        ("test", VerifiedBy.TEST),
        ("TEST", VerifiedBy.TEST),
        (" Review ", VerifiedBy.REVIEW),
        ("demo", VerifiedBy.DEMO),
        ("INSPECTION", VerifiedBy.INSPECTION),
    ],
)
def test_verified_by_field_coerces_dirty_forms(dirty, expected):
    """ENUM-3: AcceptanceCriterion.verified_by coerces dirty LLM casing and the
    f-string rendered into the Jira description stays the lowercase value.
    """
    ac = AcceptanceCriterion(condition="x", verified_by=dirty)
    assert ac.verified_by is expected
    assert isinstance(ac.verified_by, VerifiedBy)
    assert f"verified by: {ac.verified_by}" == f"verified by: {expected.value}"
    # The `(ac.verified_by or "test").lower() == "test"` default check holds.
    assert (ac.verified_by or "test").lower() == expected.value


def test_verified_by_unknown_raises():
    with pytest.raises(Exception):  # pydantic ValidationError wraps ValueError
        AcceptanceCriterion(condition="x", verified_by="telepathy")


# ─── ENUM-3: the Jira link path still resolves a dirty kind correctly ─────────
#
# Reuses the mocked-jira harness pattern from tests/test_jira_client.py: the
# real `jira.JIRA` class is patched at the import boundary so JiraClient never
# touches the network. We feed a DIRTY kind ("BLOCKS") and assert the link path
# still resolves it to the "Blocks" link type with the correct direction —
# proving the promotion is behavior-preserving for the `.lower()` lookup AND
# that the link-direction logic is untouched.


@pytest.fixture
def _jira_env(monkeypatch):
    monkeypatch.setenv("JIRA_SERVER", "https://example.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "tester@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "fake-token")


@pytest.fixture
def fake_jira():
    instance = MagicMock(name="JIRA_instance")

    def _it(name):
        m = MagicMock()
        m.name = name
        return m

    project = MagicMock()
    project.issueTypes = [_it(n) for n in ("Task", "Epic", "Story", "Sub-task")]
    instance.project.return_value = project

    counter = {"n": 0}

    def _create_issue(fields=None, **kwargs):
        counter["n"] += 1
        issue = MagicMock()
        issue.key = f"PROJ-{counter['n']}"
        return issue

    instance.create_issue.side_effect = _create_issue
    return instance


def _client(fake_jira):
    with patch("integrations.jira_client.JIRA", return_value=fake_jira):
        return JiraClient(
            hierarchy=JiraHierarchy.FLAT,
            audit=MagicMock(),
            run_id="test-run",
            project_key="PROJ",
            node_index={},
        )


def _task(title, deps=None):
    return ManagedTask(
        title=title,
        short_description=f"{title} desc",
        confidence=0.9,
        status=TaskStatus.APPROVED,
        dependencies=deps or [],
    )


def test_jira_link_path_resolves_dirty_blocks_kind(_jira_env, fake_jira):
    """A dirty `kind="BLOCKS"` coerces to DependencyKind.BLOCKS and the Jira
    link path still maps it to the 'Blocks' link type with the blocker as the
    outward side (link-direction logic unchanged).
    """
    dependent = _task(
        "Dependent",
        deps=[TaskDependency(target_ref="Blocker", reason="needs it first", kind="BLOCKS")],
    )
    blocker = _task("Blocker")

    # The promotion happened at construction: dirty input is now a clean member.
    assert dependent.dependencies[0].kind is DependencyKind.BLOCKS

    client = _client(fake_jira)
    results = client.push_tasks([dependent, blocker])

    key_by_title = {}
    for t, r in zip([dependent, blocker], results):
        assert r.success
        key_by_title[t.title] = r.jira_issue_key

    fake_jira.create_issue_link.assert_called_once()
    _, kwargs = fake_jira.create_issue_link.call_args
    # The dirty kind resolved to the correct link type via `.lower()` lookup.
    assert kwargs["type"] == "Blocks"
    # Direction contract preserved: blocker outward, dependent inward.
    assert kwargs["outwardIssue"] == key_by_title["Blocker"]
    assert kwargs["inwardIssue"] == key_by_title["Dependent"]


def test_jira_link_path_resolves_dirty_relates_kind(_jira_env, fake_jira):
    """A dirty `kind="Relates-To"` coerces to RELATES_TO and resolves to the
    'Relates' link type through the same `.lower()` lookup.
    """
    a = _task(
        "A",
        deps=[TaskDependency(target_ref="B", reason="see also", kind="Relates-To")],
    )
    b = _task("B")
    assert a.dependencies[0].kind is DependencyKind.RELATES_TO

    client = _client(fake_jira)
    results = client.push_tasks([a, b])
    assert all(r.success for r in results)

    fake_jira.create_issue_link.assert_called_once()
    _, kwargs = fake_jira.create_issue_link.call_args
    assert kwargs["type"] == "Relates"
