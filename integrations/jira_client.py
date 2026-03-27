# integrations/jira_client.py

import os
from typing import Union
from jira import JIRA
from models.schemas import ManagedTask, JiraHierarchy, JiraPushResult, TaskFlag
from audit.logger import AuditLogger
from pipeline.observability import logger, tracer, trace_span

class JiraClient:

    def __init__(self, hierarchy: JiraHierarchy, audit: AuditLogger, run_id: str, project_key: str):
        self.hierarchy = hierarchy
        self.audit = audit
        self.run_id = run_id
        self.project_key = project_key
        self.server = os.environ["JIRA_SERVER"]
        self.available_issue_types: set[str] = set()

        with logger.contextualize(agent="JiraClient", run_id=self.run_id):
            logger.info(f"Initializing JiraClient for project {self.project_key}")
        
        self.jira = JIRA(
            server=self.server,
            basic_auth=(os.environ["JIRA_EMAIL"], os.environ["JIRA_API_TOKEN"]),
        )

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
            epic_cache: dict[str, str] = {}

            for task in tasks:
                section = task.source_refs[0].section_title if task.source_refs else "General"

                if section not in epic_cache:
                    epic_key = self._create_container(section, epic_type)
                    if epic_key:
                        epic_cache[section] = epic_key

                parent_key = epic_cache.get(section)
                result = self._create_task(task, parent_key=parent_key, issue_type=task_type)
                
                # Link to epic using the Jira API (more reliable than parent field)
                if result.success and parent_key and result.jira_issue_key:
                    self._link_to_epic(result.jira_issue_key, parent_key)
                
                results.append(result)

        elif self.hierarchy == JiraHierarchy.STORY_SUBTASK:
            story_type = self._resolve_issue_type("Story", ["Task"])
            subtask_type = self._resolve_issue_type("Sub-task", ["Sub-task", "Subtask", "Task"])
            story_cache: dict[str, str] = {}

            for task in tasks:
                section = task.source_refs[0].section_title if task.source_refs else "General"

                if section not in story_cache:
                    story_key = self._create_container(section, story_type)
                    if story_key:
                        story_cache[section] = story_key

                parent_key = story_cache.get(section)
                result = self._create_task(task, parent_key=parent_key, issue_type=subtask_type)
                results.append(result)

        logger.success(f"Push operation complete. {sum(1 for r in results if r.success)} succeeded.")
        return results

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
                lines.append(f"* {ac}")
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

    def _link_to_epic(self, issue_key: str, epic_key: str):
        """Link an issue to an epic using the Jira API (more reliable than parent field)."""
        try:
            self.jira.add_issues_to_epic(epic_key, [issue_key])
            logger.info(f"Linked {issue_key} → Epic {epic_key}")
        except Exception as e:
            logger.warning(f"Could not link {issue_key} to Epic {epic_key}: {e}")
            # Non-fatal: the task was still created, just not linked

    @trace_span("JIRA_CREATE_ISSUE", agent="JiraClient")
    def _create_task(
        self, task: ManagedTask, parent_key: str | None, issue_type: str
    ) -> JiraPushResult:
        fields = self._build_fields(task, issue_type)

        with tracer.start_as_current_span(f"PUSH_{task.title[:30]}") as span:
            span.set_attribute("task_id", str(task.id))
            span.set_attribute("issue_type", issue_type)
            if parent_key: span.set_attribute("parent_key", parent_key)

            # For Sub-tasks, use the parent field; for Tasks under Epics, use linking
            if parent_key and issue_type in ("Sub-task", "Subtask"):
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
