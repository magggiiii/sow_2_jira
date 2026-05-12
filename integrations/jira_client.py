# integrations/jira_client.py

import json
import os
from pathlib import Path
from typing import Optional, Union
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

        link_type_map = {
            "blocks": "Blocks",
            "duplicates": "Duplicate",
            "relates_to": "Relates",
        }

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
                link_type = link_type_map.get((dep.kind or "blocks").lower(), "Relates")
                try:
                    # inwardIssue=source (this task is blocked by target),
                    # outwardIssue=target. The jira SDK accepts both kwargs.
                    self.jira.create_issue_link(
                        type=link_type,
                        inwardIssue=source_key,
                        outwardIssue=target_key,
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

    @trace_span("JIRA_CREATE_ISSUE", agent="JiraClient")
    def _create_task(
        self, task: ManagedTask, parent_key: str | None, issue_type: str
    ) -> JiraPushResult:
        fields = self._build_fields(task, issue_type)

        with tracer.start_as_current_span(f"PUSH_{task.title[:30]}") as span:
            span.set_attribute("task_id", str(task.id))
            span.set_attribute("issue_type", issue_type)
            if parent_key: span.set_attribute("parent_key", parent_key)

            # Unified 'parent' field for both Sub-tasks and Epics (Next-Gen & Modern Classic)
            if parent_key:
                fields["parent"] = {"key": parent_key}

            try:
                issue = self.jira.create_issue(fields=fields)
                logger.info(f"Created Jira {issue_type}: {issue.key}")
                
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
                    jira_issue_url=f"{self.server}/browse/{issue.key}"
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
                        issue = self.jira.create_issue(fields=fields)
                        logger.success(f"Fallback created Jira {issue.key}")
                        return JiraPushResult(
                            task_id=task.id,
                            success=True,
                            jira_issue_key=issue.key,
                            jira_issue_url=f"{self.server}/browse/{issue.key}",
                            warning="Created without parent (flat fallback)"
                        )
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
            issue = self.jira.create_issue(fields=fields)
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
