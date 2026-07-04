# tests/test_jira_client.py
"""
Mocked-transport tests for integrations.jira_client.JiraClient.

These tests NEVER hit a real Jira. The real `jira.JIRA` class is patched at
the import boundary inside integrations.jira_client, so JiraClient.__init__
builds a Mock instead of opening a network connection. The AuditLogger is a
plain Mock so no SQLite file is touched.
"""

from unittest.mock import MagicMock, patch

import pytest

from integrations.jira_client import JiraClient
from models.schemas import (
    JiraHierarchy,
    JiraPushResult,
    ManagedTask,
    TaskDependency,
    TaskStatus,
)


@pytest.fixture(autouse=True)
def _jira_env(monkeypatch):
    """JiraClient.__init__ reads these from os.environ; provide fakes."""
    monkeypatch.setenv("JIRA_SERVER", "https://example.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "tester@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "fake-token")


@pytest.fixture
def fake_jira():
    """
    A fake `jira.JIRA` instance. create_issue returns a stub whose .key
    increments (PROJ-1, PROJ-2, ...) so each pushed task gets a distinct key.
    create_issue_link records every call for inspection.
    """
    instance = MagicMock(name="JIRA_instance")

    # project() must return something with .issueTypes so _validate_project
    # populates the available issue types (Task, Epic, Story, Sub-task).
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


@pytest.fixture
def make_client(fake_jira):
    """
    Build a JiraClient with the real `jira.JIRA` patched to the fake instance
    and a Mock AuditLogger. Returns (client, fake_jira) so tests can assert
    on the recorded create_issue_link calls.
    """

    def _build(hierarchy=JiraHierarchy.FLAT, node_index=None):
        with patch("integrations.jira_client.JIRA", return_value=fake_jira):
            client = JiraClient(
                hierarchy=hierarchy,
                audit=MagicMock(),
                run_id="test-run",
                project_key="PROJ",
                node_index=node_index or {},
            )
        return client, fake_jira

    return _build


def _task(title, deps=None):
    return ManagedTask(
        title=title,
        short_description=f"{title} desc",
        confidence=0.9,
        status=TaskStatus.APPROVED,
        dependencies=deps or [],
    )


def test_fake_transport_does_not_touch_network(make_client):
    """Sanity: pushing tasks goes through the fake JIRA, no network call."""
    client, fake = make_client(hierarchy=JiraHierarchy.FLAT)
    results = client.push_tasks([_task("Alpha"), _task("Beta")])
    assert all(isinstance(r, JiraPushResult) for r in results)
    assert all(r.success for r in results)
    # create_issue was driven entirely by the fake.
    assert fake.create_issue.call_count == 2


def test_blocks_link_direction_blocker_is_outward(make_client):
    """
    For a 'blocks' dependency, the task that DECLARES the dependency depends on
    its target_ref. Per Jira 'Blocks' link semantics (outward = "blocks",
    inward = "is blocked by"), the dependency target is the blocker and must be
    the OUTWARD side, while the declaring (dependent) task is the INWARD side.

    Setup: 'Dependent' declares a 'blocks' dependency on 'Blocker' (it needs
    Blocker first). So Blocker -[blocks]-> Dependent: Blocker is outwardIssue,
    Dependent is inwardIssue. This matches the existing dependency-link
    direction contract in test_structured_acceptance.py.
    """
    dependent = _task(
        "Dependent",
        deps=[TaskDependency(target_ref="Blocker", reason="needs it first", kind="blocks")],
    )
    blocker = _task("Blocker")

    client, fake = make_client(hierarchy=JiraHierarchy.FLAT)
    results = client.push_tasks([dependent, blocker])

    key_by_title = {}
    for t, r in zip([dependent, blocker], results):
        assert r.success
        key_by_title[t.title] = r.jira_issue_key

    dependent_key = key_by_title["Dependent"]
    blocker_key = key_by_title["Blocker"]

    fake.create_issue_link.assert_called_once()
    _, kwargs = fake.create_issue_link.call_args
    assert kwargs["type"] == "Blocks"
    # The dependency target (the blocker) is the outward 'blocks' side; the
    # declaring/dependent task is the inward 'is blocked by' side.
    assert kwargs["outwardIssue"] == blocker_key, (
        f"blocker {blocker_key} should be the outward 'Blocks' side, "
        f"got outwardIssue={kwargs.get('outwardIssue')}"
    )
    assert kwargs["inwardIssue"] == dependent_key, (
        f"dependent {dependent_key} should be the inward side, "
        f"got inwardIssue={kwargs.get('inwardIssue')}"
    )


def test_duplicates_link_direction_declaring_task_is_outward(make_client):
    """
    For a 'duplicates' dependency the task that DECLARES it is the duplicate.
    Per Jira 'Duplicate' link semantics (outward = "duplicates", inward =
    "is duplicated by"), the declaring (source) task must be the OUTWARD side
    so the link reads 'Duplicator duplicates Original'; the target is inward.

    This is the OPPOSITE side from a 'blocks' dependency, where the target
    (the blocker) is outward — which is exactly why direction is resolved
    per-kind through the LINK_DIRECTION map rather than a single hard-coded
    outward=target rule.
    """
    duplicator = _task(
        "Duplicator",
        deps=[TaskDependency(target_ref="Original", reason="same work", kind="duplicates")],
    )
    original = _task("Original")

    client, fake = make_client(hierarchy=JiraHierarchy.FLAT)
    results = client.push_tasks([duplicator, original])

    key_by_title = {}
    for t, r in zip([duplicator, original], results):
        assert r.success
        key_by_title[t.title] = r.jira_issue_key

    fake.create_issue_link.assert_called_once()
    _, kwargs = fake.create_issue_link.call_args
    assert kwargs["type"] == "Duplicate"
    assert kwargs["outwardIssue"] == key_by_title["Duplicator"], (
        f"the declaring 'Duplicator' should be the outward 'duplicates' side, "
        f"got outwardIssue={kwargs.get('outwardIssue')}"
    )
    assert kwargs["inwardIssue"] == key_by_title["Original"], (
        f"the target 'Original' should be the inward side, "
        f"got inwardIssue={kwargs.get('inwardIssue')}"
    )


def test_push_skips_tasks_with_existing_jira_issue_key(make_client):
    """
    JIRA-2 idempotency (audit C-11): re-pushing must not re-create issues that
    already have a Jira issue key.

    A batch with one already-keyed task and one fresh task must call
    create_issue exactly once (for the fresh task only). The already-keyed task
    must still come back as a successful result carrying its original key, and
    the freshly-created task must have its new key recorded on the ManagedTask.
    """
    already = _task("AlreadyPushed")
    already.jira_issue_key = "PROJ-99"  # simulate a prior push
    fresh = _task("FreshTask")

    client, fake = make_client(hierarchy=JiraHierarchy.FLAT)
    results = client.push_tasks([already, fresh])

    # Only the fresh task should hit create_issue. The keyed task is skipped.
    assert fake.create_issue.call_count == 1, (
        f"expected create_issue once (fresh task only), "
        f"got {fake.create_issue.call_count}"
    )

    result_by_id = {r.task_id: r for r in results}

    already_result = result_by_id[already.id]
    assert already_result.success
    assert already_result.jira_issue_key == "PROJ-99", (
        "already-pushed task must report its existing key, "
        f"got {already_result.jira_issue_key}"
    )

    fresh_result = result_by_id[fresh.id]
    assert fresh_result.success
    assert fresh_result.jira_issue_key, "fresh task should have been assigned a key"
    # The freshly-created key must be recorded back onto the task so a re-push
    # of the same in-memory batch would skip it next time.
    assert fresh.jira_issue_key == fresh_result.jira_issue_key, (
        "fresh task's jira_issue_key should be recorded on the ManagedTask "
        f"after a successful create; got {fresh.jira_issue_key!r}"
    )


def test_repush_same_batch_creates_nothing(make_client):
    """A second push of the same in-memory batch must create zero new issues."""
    one = _task("One")
    two = _task("Two")

    client, fake = make_client(hierarchy=JiraHierarchy.FLAT)
    first = client.push_tasks([one, two])
    assert all(r.success for r in first)
    assert fake.create_issue.call_count == 2

    fake.create_issue.reset_mock()
    second = client.push_tasks([one, two])
    assert all(r.success for r in second)
    # Both tasks now carry keys from the first push, so nothing is re-created.
    assert fake.create_issue.call_count == 0, (
        f"re-push must not create issues, got {fake.create_issue.call_count}"
    )


# ---------------------------------------------------------------------------
# JIRA-3: rate-limit / backoff (audit H-12)
# ---------------------------------------------------------------------------


class _FakeRateLimit(Exception):
    """A 429-like error mirroring jira.exceptions.JIRAError's surface."""

    def __init__(self, status_code=429, retry_after=None, text="Rate limit exceeded"):
        self.status_code = status_code
        self.text = text
        self.response = None
        if retry_after is not None:
            resp = MagicMock()
            resp.headers = {"Retry-After": str(retry_after)}
            self.response = resp
        super().__init__(f"JiraError HTTP {status_code}: {text}")


class _FakeServerError(Exception):
    """A 5xx-like error mirroring jira.exceptions.JIRAError's surface."""

    def __init__(self, status_code=503, text="Service Unavailable"):
        self.status_code = status_code
        self.text = text
        self.response = None
        super().__init__(f"JiraError HTTP {status_code}: {text}")


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """
    Make backoff sleeps instant so retry tests run fast. The production module
    must expose a patchable sleep hook named `_sleep`; recorded sleeps let
    tests assert that a Retry-After hint was honored.
    """
    import integrations.jira_client as jc

    slept = []
    monkeypatch.setattr(jc, "_sleep", lambda s: slept.append(s), raising=False)
    return slept


def test_create_issue_retries_on_429_then_succeeds(make_client, _no_real_sleep):
    """
    A create_issue that raises a 429-like rate-limit error once and then
    succeeds must be retried and ultimately succeed (with sleep patched).
    Current behavior: a single 429 becomes a permanent failure (no retry).
    """
    call_state = {"n": 0}

    def _flaky_create_issue(fields=None, **kwargs):
        call_state["n"] += 1
        if call_state["n"] == 1:
            raise _FakeRateLimit(status_code=429)
        issue = MagicMock()
        issue.key = "PROJ-1"
        return issue

    client, fake = make_client(hierarchy=JiraHierarchy.FLAT)
    fake.create_issue.side_effect = _flaky_create_issue

    results = client.push_tasks([_task("Retryable")])

    assert len(results) == 1
    assert results[0].success, f"expected success after retry, got error={results[0].error}"
    assert results[0].jira_issue_key == "PROJ-1"
    # First attempt 429'd, second attempt succeeded => exactly two calls.
    assert fake.create_issue.call_count == 2, (
        f"expected create_issue retried once (2 calls total), "
        f"got {fake.create_issue.call_count}"
    )
    # A backoff sleep happened between the two attempts.
    assert _no_real_sleep, "expected at least one backoff sleep before the retry"


def test_create_issue_honors_retry_after_hint(make_client, _no_real_sleep):
    """When the 429 carries a Retry-After header, the backoff sleep honors it."""
    call_state = {"n": 0}

    def _flaky_create_issue(fields=None, **kwargs):
        call_state["n"] += 1
        if call_state["n"] == 1:
            raise _FakeRateLimit(status_code=429, retry_after=7)
        issue = MagicMock()
        issue.key = "PROJ-1"
        return issue

    client, fake = make_client(hierarchy=JiraHierarchy.FLAT)
    fake.create_issue.side_effect = _flaky_create_issue

    results = client.push_tasks([_task("HasRetryAfter")])

    assert results[0].success
    # The Retry-After hint (7s) must be the sleep value used before retrying.
    assert 7 in _no_real_sleep, (
        f"expected backoff to honor Retry-After=7, slept={_no_real_sleep}"
    )


def test_create_issue_retries_on_5xx_then_succeeds(make_client, _no_real_sleep):
    """A 5xx server error is transient and must be retried with bounded backoff."""
    call_state = {"n": 0}

    def _flaky_create_issue(fields=None, **kwargs):
        call_state["n"] += 1
        if call_state["n"] == 1:
            raise _FakeServerError(status_code=503)
        issue = MagicMock()
        issue.key = "PROJ-1"
        return issue

    client, fake = make_client(hierarchy=JiraHierarchy.FLAT)
    fake.create_issue.side_effect = _flaky_create_issue

    results = client.push_tasks([_task("ServerHiccup")])

    assert results[0].success, f"expected success after 5xx retry, got {results[0].error}"
    assert fake.create_issue.call_count == 2
    assert _no_real_sleep, "expected a backoff sleep before retrying the 5xx"


def test_persistent_429_eventually_fails_bounded(make_client, _no_real_sleep):
    """
    Retry must be BOUNDED: a create that always 429s gives up after a small
    cap (not infinitely) and returns a failed result.
    """
    client, fake = make_client(hierarchy=JiraHierarchy.FLAT)
    fake.create_issue.side_effect = _FakeRateLimit(status_code=429)

    results = client.push_tasks([_task("AlwaysRateLimited")])

    assert results[0].success is False
    # Bounded: a handful of attempts at most, never unbounded.
    assert 1 < fake.create_issue.call_count <= 6, (
        f"expected a small bounded number of attempts, "
        f"got {fake.create_issue.call_count}"
    )


def test_non_retryable_error_fails_without_retry(make_client, _no_real_sleep):
    """
    A non-retryable error (e.g. a 401/permission failure) must behave exactly
    as today: fail immediately, no extra retry attempts, no backoff sleep.
    """
    client, fake = make_client(hierarchy=JiraHierarchy.FLAT)
    fake.create_issue.side_effect = _FakeServerError(status_code=401, text="Unauthorized")

    results = client.push_tasks([_task("Forbidden")])

    assert results[0].success is False
    assert results[0].error
    # No retry for non-retryable errors => single create_issue call.
    assert fake.create_issue.call_count == 1, (
        f"non-retryable error must not retry, got {fake.create_issue.call_count} calls"
    )
    assert not _no_real_sleep, "non-retryable error must not trigger a backoff sleep"


def test_first_try_success_does_not_sleep(make_client, _no_real_sleep):
    """Happy path is unchanged: a first-try success never sleeps or retries."""
    client, fake = make_client(hierarchy=JiraHierarchy.FLAT)
    results = client.push_tasks([_task("Happy")])

    assert results[0].success
    assert fake.create_issue.call_count == 1
    assert not _no_real_sleep, "first-try success must not back off"


# ---------------------------------------------------------------------------
# JIRA-4: container-failure must not silently flatten the hierarchy (audit H-11)
# ---------------------------------------------------------------------------


def test_epic_container_failure_marks_children_degraded(make_client):
    """
    JIRA-4 (audit H-11): when the Epic container create fails, its children are
    still created (flat, parentless) BUT must be flagged hierarchy_degraded=True
    with a reason — not silently reported as a clean success.

    Setup: EPIC_TASK hierarchy with no source_refs, so all tasks roll up to a
    single "General" container. We make _create_container return None to
    simulate a container creation failure, while child Task creates still
    succeed.
    """
    client, fake = make_client(hierarchy=JiraHierarchy.EPIC_TASK)

    # Force the container (Epic) create to fail; children still get created.
    with patch.object(client, "_create_container", return_value=None):
        results = client.push_tasks([_task("ChildA"), _task("ChildB")])

    assert len(results) == 2
    for r in results:
        # Children are still created (we don't abort the push)...
        assert r.success, f"child should still be created flat, got error={r.error}"
        assert r.jira_issue_key, "child should have a real issue key"
        # ...but the lost hierarchy must be surfaced, not silent.
        assert r.hierarchy_degraded is True, (
            "child of a failed container must be flagged hierarchy_degraded=True"
        )
        assert r.hierarchy_degraded_reason, (
            "a degraded child must carry a human-readable reason"
        )


def test_story_container_failure_marks_subtasks_degraded(make_client):
    """Same H-11 contract for STORY_SUBTASK: failed Story => degraded sub-tasks."""
    client, fake = make_client(hierarchy=JiraHierarchy.STORY_SUBTASK)

    with patch.object(client, "_create_container", return_value=None):
        results = client.push_tasks([_task("SubA"), _task("SubB")])

    assert len(results) == 2
    for r in results:
        assert r.success
        assert r.hierarchy_degraded is True
        assert r.hierarchy_degraded_reason


def test_successful_container_leaves_children_not_degraded(make_client):
    """
    Control: when the container create SUCCEEDS, children are linked under it
    and must NOT be flagged degraded.
    """
    client, fake = make_client(hierarchy=JiraHierarchy.EPIC_TASK)

    results = client.push_tasks([_task("ChildA"), _task("ChildB")])

    assert len(results) == 2
    for r in results:
        assert r.success
        assert r.hierarchy_degraded is False, (
            "with a healthy container, children must not be flagged degraded"
        )
        assert r.hierarchy_degraded_reason is None


def test_flat_hierarchy_children_never_degraded(make_client):
    """
    FLAT hierarchy has no container by design, so a flat push must never set
    hierarchy_degraded (there is no hierarchy to degrade).
    """
    client, fake = make_client(hierarchy=JiraHierarchy.FLAT)
    results = client.push_tasks([_task("Alpha"), _task("Beta")])

    for r in results:
        assert r.success
        assert r.hierarchy_degraded is False
        assert r.hierarchy_degraded_reason is None
