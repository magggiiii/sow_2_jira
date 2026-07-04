# integrations/jira_client.py

import json
import os
import time
from pathlib import Path
from typing import Callable, Optional, TypeVar, Union
from jira import JIRA
from models.schemas import (
    AcceptanceCriterion,
    AcceptanceCriterionType,
    JiraHierarchy,
    JiraPushResult,
    ManagedTask,
    TaskFlag,
)
from audit.logger import AuditLogger
from pipeline.observability import logger, tracer, trace_span

# Module-level sleep hook so tests can patch backoff to be instant
# (monkeypatch integrations.jira_client._sleep). Production sleeps for real.
_sleep = time.sleep

# Bounded-retry tuning (audit H-12). Kept small so a flaky push degrades to a
# short delay, never an unbounded loop.
_MAX_RETRY_ATTEMPTS = 4  # total attempts including the first
_BASE_BACKOFF_SECONDS = 1.0
_MAX_BACKOFF_SECONDS = 30.0

# ── Dependency link direction (audit jira-8) ─────────────────────────────────
# A TaskDependency is declared ON the source task and NAMES a target. Jira link
# types are directional: the OUTWARD issue carries the type's outward verb toward
# the INWARD issue (for "Blocks", outward="blocks" / inward="is blocked by").
# We resolve (jira_link_type, target_is_outward) PER KIND so each link reads
# naturally — the direction is deliberately NOT uniform across kinds:
#
#   blocks     -> ("Blocks",    target_is_outward=True)
#       the target is the blocker/prerequisite: "target blocks source"
#       (equivalently, source is blocked by target).
#   duplicates -> ("Duplicate", target_is_outward=False)
#       the DECLARING source task is the duplicate: "source duplicates target".
#   relates_to -> ("Relates",   target_is_outward=False)
#       "Relates" is symmetric; "source relates to target" (direction cosmetic).
#
# Keyed by the lowercased DependencyKind value (matches the `.lower()` coercion
# below, which already normalizes dirty enum casing). Unknown kinds fall back to
# a symmetric "Relates" originating from the source.
LINK_DIRECTION: dict[str, tuple[str, bool]] = {
    "blocks": ("Blocks", True),
    "duplicates": ("Duplicate", False),
    "relates_to": ("Relates", False),
}
_DEFAULT_LINK_DIRECTION: tuple[str, bool] = ("Relates", False)

T = TypeVar("T")


def _retry_after_seconds(error: Exception) -> Optional[float]:
    """Extract a Retry-After hint (seconds) from a Jira error if present."""
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    raw = headers.get("Retry-After") or headers.get("retry-after")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _is_rate_limited(error: Exception) -> bool:
    """True for HTTP 429 / textual rate-limit errors."""
    if getattr(error, "status_code", None) == 429:
        return True
    text = (str(error) or "").lower()
    return "429" in text or "rate limit" in text or "too many requests" in text


def _is_server_error(error: Exception) -> bool:
    """True for transient HTTP 5xx errors."""
    status = getattr(error, "status_code", None)
    if isinstance(status, int) and 500 <= status <= 599:
        return True
    return False


def _with_retry(
    fn: Callable[[], T],
    *,
    description: str,
    max_attempts: int = _MAX_RETRY_ATTEMPTS,
) -> T:
    """
    Run `fn` with a bounded retry on transient Jira failures.

    Retry policy (audit H-12):
      - 429 / rate-limit: honor a Retry-After hint when present, otherwise
        exponential backoff.
      - 5xx: exponential backoff.
      - anything else: re-raise immediately (today's behavior is preserved).

    Sleeps go through the module-level `_sleep` hook so tests can patch them.
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 — classify, then re-raise or retry
            rate_limited = _is_rate_limited(e)
            server_error = _is_server_error(e)
            if not (rate_limited or server_error):
                raise  # non-retryable: behave as before
            if attempt >= max_attempts:
                logger.warning(
                    f"{description}: giving up after {attempt} attempts ({e})"
                )
                raise
            if rate_limited:
                delay = _retry_after_seconds(e)
                if delay is None:
                    delay = _BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
            else:
                delay = _BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
            delay = min(delay, _MAX_BACKOFF_SECONDS)
            logger.warning(
                f"{description}: transient error (attempt {attempt}/{max_attempts}), "
                f"backing off {delay}s ({e})"
            )
            _sleep(delay)

class JiraClient:

    def __init__(
        self,
        hierarchy: JiraHierarchy,
        audit: AuditLogger,
        run_id: str,
        project_key: str,
        node_index: Optional[dict[str, dict]] = None,
    ):
        self.hierarchy = hierarchy
        self.audit = audit
        self.run_id = run_id
        self.project_key = project_key
        self.server = os.environ["JIRA_SERVER"]
        self.available_issue_types: set[str] = set()
        self.node_index: dict[str, dict] = node_index or self._load_node_index()

        with logger.contextualize(agent="JiraClient", run_id=self.run_id):
            logger.info(f"Initializing JiraClient for project {self.project_key}")

        self.jira = JIRA(
            server=self.server,
            basic_auth=(os.environ["JIRA_EMAIL"], os.environ["JIRA_API_TOKEN"]),
        )

    def _load_node_index(self) -> dict[str, dict]:
        """Best-effort load of the orchestrator-persisted hierarchy map."""
        path = Path(f"data/sessions/{self.run_id}/node_index.json")
        if not path.exists():
            return {}
        try:
            with open(path) as f:
                return json.load(f) or {}
        except Exception as e:
            logger.warning(f"Could not read node_index.json at {path}: {e}")
            return {}

    def _container_key_for(self, task: ManagedTask) -> tuple[Optional[str], str]:
        """
        Resolve (group_key, container_title) for a task. The group_key is the
        node_id used as the cache key — preferring the top-level ancestor so
        multi-level trees roll up to a single Epic/Story per top section.
        Falls back to the task's immediate source node (legacy behaviour) when
        hierarchy metadata is missing on the SourceRef.
        """
        if not task.source_refs:
            return None, "General"

        ref = task.source_refs[0]
        # Prefer the root ancestor so a 3-level tree maps to one container.
        root_id = ref.parent_chain[0] if ref.parent_chain else (ref.parent_id or ref.node_id)
        title = self.node_index.get(root_id, {}).get("title") if root_id else None
        if not title:
            # Fallbacks: immediate parent's title, then the source section title.
            if ref.parent_id:
                title = self.node_index.get(ref.parent_id, {}).get("title")
            if not title:
                title = ref.section_title or "General"
        return root_id, title

    def _validate_project(self) -> set[str]:
        """Pre-flight check: does the project exist and what issue types are available?"""
        try:
            project = self.jira.project(self.project_key)
            available = set()
            for it in project.issueTypes:
                available.add(it.name)
            logger.info(f"Project '{self.project_key}' validated. Available types: {available}")
            self.available_issue_types = available
            return available
        except Exception as e:
            raise ValueError(
                f"Project '{self.project_key}' not found or inaccessible: {e}. "
                f"Check that the project key is correct and your API token has access."
            )

    def _resolve_issue_type(self, desired: str, fallbacks: list[str]) -> str:
        """Resolve the best available issue type, falling back if desired type doesn't exist."""
        if desired in self.available_issue_types:
            return desired
        for fb in fallbacks:
            if fb in self.available_issue_types:
                logger.warning(f"Issue type '{desired}' not available, using '{fb}' instead")
                return fb
        # Last resort: use the first available type
        if self.available_issue_types:
            first = next(iter(self.available_issue_types))
            logger.warning(f"No suitable issue type found, falling back to '{first}'")
            return first
        return desired  # Let Jira return the error

    @trace_span("JIRA_PUSH_ALL", agent="JiraClient")
    def push_tasks(self, tasks: list[ManagedTask]) -> list[JiraPushResult]:
        """
        Push all approved tasks to Jira.
        Hierarchy determines parent/child structure.
        Returns list of push results.
        """
        # Pre-flight validation
        try:
            self._validate_project()
        except ValueError as e:
            logger.error(str(e))
            return [JiraPushResult(task_id=t.id, success=False, error=str(e)) for t in tasks]

        logger.info(f"Pushing {len(tasks)} tasks to Jira with hierarchy {self.hierarchy.value}")
        results = []

        # Resolve issue types for this project
        task_type = self._resolve_issue_type("Task", ["Story", "Bug"])
        
        if self.hierarchy == JiraHierarchy.FLAT:
            for task in tasks:
                result = self._create_task(task, parent_key=None, issue_type=task_type)
                results.append(result)

        elif self.hierarchy == JiraHierarchy.EPIC_TASK:
            epic_type = self._resolve_issue_type("Epic", ["Story"])
            # Cache keyed by structural group_key (root ancestor node_id) so
            # tasks with the same section_title but different parents land in
            # different epics, and tasks across sibling nodes under one root
            # roll up to a single epic.
            epic_cache: dict[str, str] = {}

            for task in tasks:
                group_key, container_title = self._container_key_for(task)
                cache_key = group_key or f"__fallback__::{container_title}"

                if cache_key not in epic_cache:
                    epic_key = self._create_container(container_title, epic_type)
                    if epic_key:
                        epic_cache[cache_key] = epic_key

                parent_key = epic_cache.get(cache_key)
                # Next-Gen Fix: Always pass parent_key to _create_task to be used in 'parent' field
                result = self._create_task(task, parent_key=parent_key, issue_type=task_type)
                # H-11: a missing parent here means the Epic container create
                # failed. We still created the child, but flag the lost hierarchy
                # so the caller/UI doesn't read it as a clean, structured push.
                if parent_key is None and result.success:
                    self._mark_hierarchy_degraded(
                        result,
                        task,
                        f"Epic container '{container_title}' could not be created; "
                        f"task created flat (no parent)",
                    )
                results.append(result)

        elif self.hierarchy == JiraHierarchy.STORY_SUBTASK:
            story_type = self._resolve_issue_type("Story", ["Task"])
            subtask_type = self._resolve_issue_type("Sub-task", ["Sub-task", "Subtask", "Task"])
            story_cache: dict[str, str] = {}

            for task in tasks:
                group_key, container_title = self._container_key_for(task)
                cache_key = group_key or f"__fallback__::{container_title}"

                if cache_key not in story_cache:
                    story_key = self._create_container(container_title, story_type)
                    if story_key:
                        story_cache[cache_key] = story_key

                parent_key = story_cache.get(cache_key)
                # Next-Gen Fix: Works identically for stories and sub-tasks via 'parent' field
                result = self._create_task(task, parent_key=parent_key, issue_type=subtask_type)
                # H-11: a missing parent here means the Story container create
                # failed. We still created the child, but flag the lost hierarchy
                # so the caller/UI doesn't read it as a clean, structured push.
                if parent_key is None and result.success:
                    self._mark_hierarchy_degraded(
                        result,
                        task,
                        f"Story container '{container_title}' could not be created; "
                        f"sub-task created flat (no parent)",
                    )
                results.append(result)

        # Second pass: resolve task-to-task dependencies into Jira issue links.
        # Failures are logged + reflected in JiraPushResult.warning rather than
        # raised, so a flaky link API doesn't fail the whole push.
        try:
            self._create_dependency_links(tasks, results)
        except Exception as e:
            logger.warning(f"Dependency link pass crashed: {e}")

        logger.success(f"Push operation complete. {sum(1 for r in results if r.success)} succeeded.")
        return results

    def _create_dependency_links(
        self, tasks: list[ManagedTask], results: list[JiraPushResult]
    ) -> None:
        """
        After all tasks are pushed and have Jira keys, walk each task's
        dependencies and create Jira issue links for every target_ref that
        resolves to a known task title. Unresolved refs are logged. Per-link
        failures are non-fatal.

        Link types:
          kind="blocks"     → "Blocks"
          kind="duplicates" → "Duplicate"
          kind="relates_to" → "Relates"
        """
        # Build a normalized title -> jira_issue_key lookup. Only tasks that
        # were pushed successfully and have a key participate.
        key_by_id = {r.task_id: r.jira_issue_key for r in results if r.success and r.jira_issue_key}
        title_to_key: dict[str, str] = {}
        for t in tasks:
            key = key_by_id.get(t.id)
            if not key:
                continue
            title_to_key[(t.title or "").strip().lower()] = key

        if not title_to_key:
            return

        linked = 0
        unresolved = 0
        for task in tasks:
            if not task.dependencies:
                continue
            source_key = key_by_id.get(task.id)
            if not source_key:
                continue
            result = next((r for r in results if r.task_id == task.id), None)
            for dep in task.dependencies:
                ref_norm = (dep.target_ref or "").strip().lower()
                target_key = title_to_key.get(ref_norm)
                if not target_key:
                    unresolved += 1
                    logger.info(
                        f"Dependency target unresolved: '{dep.target_ref}' "
                        f"(from {source_key}); skipping link"
                    )
                    self.audit.log(
                        run_id=self.run_id,
                        agent="JiraClient",
                        action="DEP_UNRESOLVED",
                        task_id=str(task.id),
                        detail=f"{source_key} depends on '{dep.target_ref}' but no matching task found",
                    )
                    continue
                if target_key == source_key:
                    continue  # don't self-link
                # Resolve link type + direction per-kind (see LINK_DIRECTION).
                # The dependency is source DEPENDS-ON target; which issue takes
                # the outward role depends on the kind: for "blocks" the target
                # (blocker) is outward, for "duplicates"/"relates_to" the
                # declaring source is outward.
                link_type, target_is_outward = LINK_DIRECTION.get(
                    (dep.kind or "blocks").lower(), _DEFAULT_LINK_DIRECTION
                )
                outward_key, inward_key = (
                    (target_key, source_key)
                    if target_is_outward
                    else (source_key, target_key)
                )
                try:
                    # The jira SDK accepts both kwargs.
                    _with_retry(
                        lambda: self.jira.create_issue_link(
                            type=link_type,
                            outwardIssue=outward_key,
                            inwardIssue=inward_key,
                        ),
                        description=f"create_issue_link({outward_key}->{inward_key})",
                    )
                    linked += 1
                    self.audit.log(
                        run_id=self.run_id,
                        agent="JiraClient",
                        action="DEP_LINKED",
                        task_id=str(task.id),
                        detail=f"{source_key} -[{link_type}]-> {target_key} ({dep.reason})",
                    )
                except Exception as e:
                    err = f"Link {source_key} -[{link_type}]-> {target_key} failed: {e}"
                    logger.warning(err)
                    self.audit.log(
                        run_id=self.run_id,
                        agent="JiraClient",
                        action="DEP_LINK_FAILED",
                        task_id=str(task.id),
                        detail=err,
                    )
                    if result is not None:
                        existing = result.warning or ""
                        result.warning = (existing + " | " if existing else "") + err

        if linked or unresolved:
            logger.info(
                f"Dependency links: {linked} created, {unresolved} unresolved"
            )

    def _build_description(self, task: ManagedTask) -> str:
        """Build Jira-formatted description from task fields."""
        lines = []

        lines.append("h3. Short Description")
        lines.append(task.short_description or "_Not specified_")
        lines.append("")

        lines.append("h3. Use Case")
        lines.append(task.use_case or "_Not specified_")
        lines.append("")

        lines.append("h3. Acceptance Criteria")
        if task.acceptance_criteria:
            for ac in task.acceptance_criteria:
                lines.append(self._render_acceptance_criterion(ac))
        else:
            lines.append("⚠️ *Could not be determined — requires manual review*")
        lines.append("")

        lines.append("h3. Considerations & Constraints")
        if task.considerations_constraints:
            for cc in task.considerations_constraints:
                lines.append(f"* {cc}")
        else:
            lines.append("_None identified_")
        lines.append("")

        lines.append("h3. Deliverables")
        if task.deliverables:
            for d in task.deliverables:
                lines.append(f"* {d}")
        else:
            lines.append("_Not specified_")
        lines.append("")

        lines.append("h3. Mockup / Prototype")
        lines.append(task.mockup_prototype or "_N/A_")
        lines.append("")

        if task.source_refs:
            ref = task.source_refs[0]
            lines.append("h3. SOW Reference")
            lines.append(
                f"Section: *{ref.section_title}* | "
                f"Pages: {ref.page_start}–{ref.page_end} | "
                f"Node: {ref.node_id}"
            )

        return "\n".join(lines)

    def _render_acceptance_criterion(self, ac) -> str:
        """
        Render one AC as a Jira wiki-markup checklist bullet. Falls back
        to plain-string formatting for any legacy entries that survived
        normalization (shouldn't happen post-1B, but defensive).
        """
        # Legacy plain-string fallback
        if isinstance(ac, str):
            return f"* {ac}"
        if not isinstance(ac, AcceptanceCriterion):
            # Defensive: try to coerce, otherwise stringify.
            try:
                return f"* [ ] {getattr(ac, 'condition', str(ac))}"
            except Exception:
                return f"* [ ] {ac}"
        # Suppress the italic annotation when both metadata fields are at
        # their defaults — keeps the description uncluttered for simple ACs.
        is_default = (
            ac.type == AcceptanceCriterionType.FUNCTIONAL
            and (ac.verified_by or "test").lower() == "test"
        )
        if is_default:
            return f"* [ ] {ac.condition}"
        return (
            f"* [ ] {ac.condition} "
            f"_(type: {ac.type.value}, verified by: {ac.verified_by})_"
        )

    def _build_labels(self, task: ManagedTask) -> list[str]:
        """Convert task flags to Jira labels (Jira labels cannot have spaces)."""
        label_map = {
            TaskFlag.NO_ACCEPTANCE_CRITERIA: "sow-no-ac",
            TaskFlag.AMBIGUOUS_SCOPE: "sow-ambiguous",
            TaskFlag.INCOMPLETE: "sow-incomplete",
            TaskFlag.LOW_CONFIDENCE: "sow-low-confidence",
            TaskFlag.GAP_RECOVERED: "sow-gap-recovered",
            TaskFlag.POTENTIAL_DUPLICATE: "sow-potential-dup",
        }
        return [label_map[f] for f in task.flags if f in label_map]

    def _build_fields(self, task: ManagedTask, issue_type: str) -> dict:
        return {
            "project": {"key": self.project_key},
            "summary": task.title[:255],  # Jira summary max 255 chars
            "description": self._build_description(task),
            "issuetype": {"name": issue_type},
            "labels": self._build_labels(task),
        }

    # REMOVED _link_to_epic: Standard Agile API is incompatible with Next-Gen projects.
    # All linking is now handled via the 'parent' field in _create_task.

    def _mark_hierarchy_degraded(
        self, result: JiraPushResult, task: ManagedTask, reason: str
    ) -> None:
        """
        Flag a successfully-created-but-parentless child (audit H-11).

        When an Epic/Story container create fails (or a parent link is
        rejected), the child is still created flat. We surface that on the
        result so the caller/UI can see the requested hierarchy was not honored,
        instead of silently reporting a clean success.
        """
        result.hierarchy_degraded = True
        result.hierarchy_degraded_reason = reason
        logger.warning(f"Hierarchy degraded for task {task.id}: {reason}")
        self.audit.log(
            run_id=self.run_id,
            agent="JiraClient",
            action="HIERARCHY_DEGRADED",
            task_id=str(task.id),
            detail=reason,
        )

    @trace_span("JIRA_CREATE_ISSUE", agent="JiraClient")
    def _create_task(
        self, task: ManagedTask, parent_key: str | None, issue_type: str
    ) -> JiraPushResult:
        # Idempotency (audit C-11): a task that already carries a Jira issue key
        # was pushed before. Skip create_issue and report it as already-pushed
        # so a re-push doesn't duplicate the issue.
        if task.jira_issue_key:
            logger.info(
                f"Task {task.id} already pushed as {task.jira_issue_key}; skipping create"
            )
            self.audit.log(
                run_id=self.run_id,
                agent="JiraClient",
                action="PUSH_SKIPPED",
                task_id=str(task.id),
                detail=f"Already pushed as {task.jira_issue_key}; skipping create",
            )
            return JiraPushResult(
                task_id=task.id,
                success=True,
                jira_issue_key=task.jira_issue_key,
                jira_issue_url=f"{self.server}/browse/{task.jira_issue_key}",
            )

        fields = self._build_fields(task, issue_type)

        with tracer.start_as_current_span(f"PUSH_{task.title[:30]}") as span:
            span.set_attribute("task_id", str(task.id))
            span.set_attribute("issue_type", issue_type)
            if parent_key: span.set_attribute("parent_key", parent_key)

            # Unified 'parent' field for both Sub-tasks and Epics (Next-Gen & Modern Classic)
            if parent_key:
                fields["parent"] = {"key": parent_key}

            try:
                issue = _with_retry(
                    lambda: self.jira.create_issue(fields=fields),
                    description=f"create_issue({issue_type})",
                )
                logger.info(f"Created Jira {issue_type}: {issue.key}")
                # Record the key on the task so a re-push of this in-memory
                # batch skips it (idempotency, audit C-11).
                task.jira_issue_key = issue.key

                self.audit.log(
                    run_id=self.run_id,
                    agent="JiraClient",
                    action="PUSHED",
                    task_id=str(task.id),
                    detail=f"Created {issue.key}: {task.title[:60]}",
                )
                return JiraPushResult(
                    task_id=task.id,
                    success=True,
                    jira_issue_key=issue.key,
                    jira_issue_url=f"{self.server}/browse/{issue.key}",
                )
            except Exception as e:
                error_str = str(e)
                logger.warning(f"Jira push failed: {error_str[:100]}")

                # Attempt 2: parent caused a 400 — retry without it
                if parent_key and ("400" in error_str or "parent" in error_str.lower()):
                    logger.info("Retrying without parent field (fallback to flat)")
                    self.audit.log(
                        run_id=self.run_id,
                        agent="JiraClient",
                        task_id=str(task.id),
                        action="parent_fallback",
                        detail=f"Parent linking failed ({error_str[:100]}), retrying flat"
                    )
                    fields.pop("parent", None)
                    try:
                        issue = _with_retry(
                            lambda: self.jira.create_issue(fields=fields),
                            description=f"create_issue({issue_type}, flat-fallback)",
                        )
                        logger.success(f"Fallback created Jira {issue.key}")
                        # Record the key so a re-push skips it (idempotency).
                        task.jira_issue_key = issue.key
                        # H-11: the parent link was requested but rejected, so the
                        # child landed flat — that is a degraded hierarchy too.
                        fallback_result = JiraPushResult(
                            task_id=task.id,
                            success=True,
                            jira_issue_key=issue.key,
                            jira_issue_url=f"{self.server}/browse/{issue.key}",
                            warning="Created without parent (flat fallback)",
                        )
                        self._mark_hierarchy_degraded(
                            fallback_result,
                            task,
                            f"Parent link to '{parent_key}' rejected; "
                            f"task created flat (no parent)",
                        )
                        return fallback_result
                    except Exception as e2:
                        logger.error(f"Fallback failed too: {e2}")
                        return JiraPushResult(
                            task_id=task.id, success=False, error=str(e2)
                        )

                logger.error(f"Permanent push failure: {error_str}")
                self.audit.log(
                    run_id=self.run_id,
                    agent="JiraClient",
                    action="PUSH_FAILED",
                    task_id=str(task.id),
                    detail=error_str,
                )
                return JiraPushResult(task_id=task.id, success=False, error=error_str)

    @trace_span("JIRA_CREATE_CONTAINER", agent="JiraClient")
    def _create_container(self, section_title: str, issue_type: str) -> str | None:
        """Create a container issue (Epic, Story, etc.) for grouping tasks."""
        logger.info(f"Creating {issue_type} for section: {section_title}")
        try:
            fields = {
                "project": {"key": self.project_key},
                "summary": f"[SOW] {section_title[:240]}",
                "issuetype": {"name": issue_type},
            }
            issue = _with_retry(
                lambda: self.jira.create_issue(fields=fields),
                description=f"create_container({issue_type})",
            )
            logger.success(f"Created {issue_type}: {issue.key}")
            return issue.key
        except Exception as e:
            logger.error(f"Failed to create {issue_type}: {e}")
            self.audit.log(
                run_id=self.run_id,
                agent="JiraClient",
                action=f"{issue_type.upper()}_CREATE_FAILED",
                detail=f"Could not create {issue_type} for '{section_title}': {e}",
            )
            return None
