from typing import Optional
# pipeline/agents/state.py
# This agent is RULE-BASED. No LLM calls. Pure logic.

from uuid import uuid4
from difflib import SequenceMatcher
from models.schemas import ManagedTask, RawTask, TaskStatus, SourceRef, TaskFlag
from audit.logger import AuditLogger


TITLE_SIMILARITY_THRESHOLD = 0.75  # SequenceMatcher ratio to consider "continuation"


class TaskStateAgent:
    """
    Manages task lifecycle across chunks.
    Decides: NEW | CONTINUE | CLOSE for each task.
    No LLM — uses string similarity + continues_to_next flag.
    """

    def __init__(self, audit_logger: AuditLogger, run_id: str):
        self.audit = audit_logger
        self.run_id = run_id

    def process(
        self,
        new_raw_tasks: list[RawTask],
        open_tasks: list[ManagedTask],
        node: dict,
    ) -> tuple[list[ManagedTask], list[ManagedTask]]:
        """
        Returns: (updated_open_tasks, newly_closed_tasks)

        Logic:
        1. For each new_raw_task:
           a. Check if it continues an open task (title similarity + open task has continues_to_next=True)
           b. If yes → MERGE into open task (append descriptions, extend lists)
           c. If no → create new ManagedTask with status=OPEN
        2. For open tasks not continued this round AND continues_to_next=False → close them
        3. For open tasks not continued this round AND continues_to_next=True → keep open (flagged INCOMPLETE)
        """
        source_ref = SourceRef(
            node_id=node["node_id"],
            section_title=node["title"],
            page_start=node["page_start"],
            page_end=node["page_end"],
            snippet="",
        )

        newly_closed: list[ManagedTask] = []
        continued_open_ids: set[str] = set()
        updated_managed: list[ManagedTask] = []

        for raw in new_raw_tasks:
            matched = self._find_continuation(raw, open_tasks)

            if matched:
                # Merge raw into existing open task
                merged = self._merge(matched, raw, source_ref)
                updated_managed.append(merged)
                continued_open_ids.add(str(matched.id))
                self.audit.log(
                    run_id=self.run_id,
                    agent="StateAgent",
                    node_id=node["node_id"],
                    action="CONTINUED",
                    task_id=str(merged.id),
                    detail=f"Task '{raw.title}' merged into existing task {merged.id}",
                )
            else:
                # New task
                managed = self._promote(raw, source_ref)
                updated_managed.append(managed)
                self.audit.log(
                    run_id=self.run_id,
                    agent="StateAgent",
                    node_id=node["node_id"],
                    action="NEW_TASK",
                    task_id=str(managed.id),
                    detail=f"New task: '{managed.title}'",
                )

        # Close or keep open tasks not continued
        still_open: list[ManagedTask] = []
        for open_task in open_tasks:
            if str(open_task.id) in continued_open_ids:
                continue  # already handled above
            if open_task.continues_to_next:
                # Expected continuation but didn't arrive → flag INCOMPLETE, keep open
                if TaskFlag.INCOMPLETE not in open_task.flags:
                    open_task.flags.append(TaskFlag.INCOMPLETE)
                still_open.append(open_task)
                self.audit.log(
                    run_id=self.run_id,
                    agent="StateAgent",
                    node_id=node["node_id"],
                    action="KEPT_OPEN_INCOMPLETE",
                    task_id=str(open_task.id),
                    detail=f"Task '{open_task.title}' expected continuation but none found",
                )
            else:
                open_task.status = TaskStatus.CLOSED
                newly_closed.append(open_task)
                self.audit.log(
                    run_id=self.run_id,
                    agent="StateAgent",
                    node_id=node["node_id"],
                    action="CLOSED",
                    task_id=str(open_task.id),
                    detail=f"Task '{open_task.title}' closed normally",
                )

        # New open tasks from this round replace the still-open ones
        next_open = still_open + [t for t in updated_managed if t.status == TaskStatus.OPEN]

        return next_open, newly_closed

    def _find_continuation(
        self, raw: RawTask, open_tasks: list[ManagedTask]
    ) -> ManagedTask | None:
        """
        Returns the best matching open task if:
        - open_task.continues_to_next is True
        - Title similarity ratio >= TITLE_SIMILARITY_THRESHOLD
        """
        best_match = None
        best_ratio = 0.0

        for open_task in open_tasks:
            if not open_task.continues_to_next:
                continue
            ratio = SequenceMatcher(
                None,
                raw.title.lower(),
                open_task.title.lower(),
            ).ratio()
            if ratio > best_ratio and ratio >= TITLE_SIMILARITY_THRESHOLD:
                best_ratio = ratio
                best_match = open_task

        return best_match

    def _merge(
        self, existing: ManagedTask, incoming: RawTask, source_ref: SourceRef
    ) -> ManagedTask:
        """Merge incoming RawTask data into existing ManagedTask."""
        # Append description
        existing.short_description += " " + incoming.short_description

        # Extend lists (deduplicate)
        if incoming.acceptance_criteria:
            existing.acceptance_criteria = list(set(
                (existing.acceptance_criteria or []) + incoming.acceptance_criteria
            ))
        if incoming.considerations_constraints:
            existing.considerations_constraints = list(set(
                (existing.considerations_constraints or []) + incoming.considerations_constraints
            ))
        if incoming.deliverables:
            existing.deliverables = list(set(
                (existing.deliverables or []) + incoming.deliverables
            ))

        # Take higher confidence
        existing.confidence = max(existing.confidence, incoming.confidence)

        # Merge flags
        for flag in incoming.flags:
            try:
                tf = TaskFlag(flag)
                if tf not in existing.flags:
                    existing.flags.append(tf)
            except ValueError:
                pass

        # Update continuation status
        existing.continues_to_next = incoming.continues_to_next

        # Add source ref
        existing.source_refs.append(source_ref)

        # If mockup found in continuation, update
        if incoming.mockup_prototype and not existing.mockup_prototype:
            existing.mockup_prototype = incoming.mockup_prototype

        import datetime
        existing.updated_at = datetime.datetime.utcnow()

        return existing

    def _promote(self, raw: RawTask, source_ref: SourceRef) -> ManagedTask:
        """Convert RawTask to ManagedTask with new UUID."""
        flags = []
        for f in raw.flags:
            try:
                flags.append(TaskFlag(f))
            except ValueError:
                pass

        return ManagedTask(
            id=uuid4(),
            title=raw.title,
            short_description=raw.short_description,
            acceptance_criteria=raw.acceptance_criteria,
            use_case=raw.use_case,
            considerations_constraints=raw.considerations_constraints,
            deliverables=raw.deliverables,
            mockup_prototype=raw.mockup_prototype,
            confidence=raw.confidence,
            flags=flags,
            continues_to_next=raw.continues_to_next,
            status=TaskStatus.OPEN,
            source_refs=[source_ref],
        )

    def close_all_remaining(self, open_tasks: list[ManagedTask]) -> list[ManagedTask]:
        """Called at end of pipeline to force-close any still-open tasks."""
        for task in open_tasks:
            task.status = TaskStatus.CLOSED
            if TaskFlag.INCOMPLETE not in task.flags:
                task.flags.append(TaskFlag.INCOMPLETE)
        return open_tasks