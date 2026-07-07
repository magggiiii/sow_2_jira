# pipeline/agents/extraction.py

from pydantic import BaseModel, Field

from audit.logger import AuditLogger
from core.agent_runner import AgentRunner, InstructorError
from core.agent_spec import AgentSpec
from models.schemas import RawTask, normalize_acceptance_criteria
from pipeline.llm_client import LLMClient
from prompts import registry


class ExtractionResult(BaseModel):
    """Instructor ``response_model`` (C-5) — the extractor's output object.

    Mirrors the ``{"scratchpad": ..., "tasks": [...]}`` wrapper the prompt asks
    for. ``tasks`` is a list of the existing :class:`RawTask` (validated, with
    its permissive Union ACs and clamped confidence), so the per-task dict-parse
    loop the regex path needed is replaced by iterating already-validated tasks.
    The legacy bare-array shape the agent used to special-case is now Instructor's
    concern: the runner always hands back a validated ExtractionResult.
    """
    scratchpad: str = ""
    tasks: list[RawTask] = Field(default_factory=list)

EXTRACTION_SYSTEM_PROMPT = registry.load("extraction.system.v1")

EXTRACTION_PROMPT_TEMPLATE = registry.load("extraction.user.v1")

HIERARCHY_CONTEXT = {
    "flat": (
        "═══ HIERARCHY CONTEXT ═══\n"
        "Target: FLAT (standalone Tasks, no parent).\n"
        "Extract medium-grained, self-contained tasks. Each task should make "
        "sense on its own without parent context.\n"
    ),
    "epic_task": """═══ HIERARCHY CONTEXT ═══
Target: EPIC → TASK hierarchy.
This SOW section will become an Epic. Extract atomic Tasks that belong under it.
Each task should be completable by one developer in 1-2 sprints.
Do not duplicate the section's high-level goal — focus on concrete implementation work.
""",
    "story_subtask": """═══ HIERARCHY CONTEXT ═══
Target: STORY → SUB-TASK hierarchy.
This SOW section will become a Story. Extract fine-grained Sub-tasks.
Break work down into the smallest meaningful units (a few hours to a few days each).
Multiple sub-tasks per feature area is expected. Be specific and granular.
""",
}


class TaskExtractionAgent:

    def __init__(self, llm_client: LLMClient, audit_logger: AuditLogger,
                 run_id: str, confidence_threshold: float = 0.6, max_section_chars: int = 16000):
        self.llm = llm_client
        # Route the single LLM call through the AgentRunner's Instructor-validated
        # structured-output seam (complete_structured); see extract().
        self.runner = AgentRunner(llm_client)
        # STEP 3.6b: this agent's single structured call declared as an AgentSpec
        # and routed via runner.run_structured (byte-identical kwargs).
        self.spec = AgentSpec(
            name="ExtractionAgent",
            system_prompt=EXTRACTION_SYSTEM_PROMPT,
            prompt_template=EXTRACTION_PROMPT_TEMPLATE,
            response_model=ExtractionResult,
        )
        self.audit = audit_logger
        self.run_id = run_id
        self.confidence_threshold = confidence_threshold
        self.max_section_chars = max_section_chars
        # GUARDRAIL-3: additive, read-only counter of recoverable extraction
        # errors across this run. Incremented on the existing error paths only;
        # it never changes control flow or return values. The orchestrator reads
        # it to derive an extraction StageHealth for the RunHealthReport.
        self.error_count = 0

    def extract(
        self,
        node: dict,
        section_text: str,
        hierarchy: str = "epic_task",
        status_callback=None,
    ) -> list[RawTask]:
        """
        Runs extraction on a single PageIndex node.
        Returns a list of RawTask objects.
        hierarchy: one of 'flat', 'epic_task', 'story_subtask' — adjusts extraction
        granularity.
        Auto-adds LOW_CONFIDENCE flag to tasks below threshold.
        Returns empty list if section_text is too short (< 50 chars).
        """
        # Set the callback on the LLM client temporarily for this call if provided
        if status_callback and hasattr(self.llm, "status_callback"):
            self.llm.status_callback = status_callback
        if len(section_text.strip()) < 50:
            self.audit.log(
                run_id=self.run_id,
                agent="ExtractionAgent",
                node_id=node["node_id"],
                action="SKIPPED_SHORT_SECTION",
                detail=f"Section '{node['title']}' too short to extract",
            )
            return []

        # A4: surface truncation instead of silently dropping tasks on dense
        # pages. We record a SECTION_TRUNCATED audit row here and flag every task
        # extracted from this section with TRUNCATION below, so a reviewer knows
        # the section was clipped and may have lost work. Raise max_section_chars
        # (config) for big docs to avoid clipping at all.
        section_truncated = len(section_text) > self.max_section_chars
        if section_truncated:
            original_len = len(section_text)
            section_text = section_text[:self.max_section_chars]
            truncation_notice = (
                f"\n\n[NOTE: This section was truncated at {self.max_section_chars} characters. "
                f"Review the original SOW section '{node['title']}' pages "
                f"{node['page_start']}-{node['page_end']} for any tasks not captured here.]"
            )
            section_text += truncation_notice
            self.audit.log(
                run_id=self.run_id,
                agent="ExtractionAgent",
                node_id=node["node_id"],
                action="SECTION_TRUNCATED",
                detail=(
                    f"Section '{node['title']}' truncated {original_len} → "
                    f"{self.max_section_chars} chars; extracted tasks flagged TRUNCATION"
                ),
            )

        payload = {
            "section_title": node["title"],
            "page_start": node["page_start"],
            "page_end": node["page_end"],
            "section_text": section_text,
            "hierarchy_context": HIERARCHY_CONTEXT.get(hierarchy, HIERARCHY_CONTEXT["epic_task"]),
        }

        # C-5: route through the Instructor-validated structured-output seam.
        # The runner returns a validated ExtractionResult — the {scratchpad,
        # tasks} wrapper, with each task already a schema-valid RawTask. That
        # replaces the manual dict/list shape juggling and the per-task
        # RawTask(**raw) parse loop. Any structured-output failure (call error,
        # unparseable output, a tasks field Instructor could not coerce into a
        # list of RawTask) surfaces as one InstructorError, recorded as
        # EXTRACTION_ERROR (error_count++) and degraded to an empty list.
        try:
            result = self.runner.run_structured(
                self.spec, payload, node_id=node["node_id"]
            )
        except InstructorError as e:
            self.error_count += 1
            self.audit.log(
                run_id=self.run_id,
                agent="ExtractionAgent",
                node_id=node["node_id"],
                action="EXTRACTION_ERROR",
                detail=str(e),
            )
            return []

        # Audit-log the scratchpad so reviewers can see what the model was
        # thinking, but never propagate it into RawTask.
        scratchpad = result.scratchpad or ""
        if scratchpad:
            self.audit.log(
                run_id=self.run_id,
                agent="ExtractionAgent",
                node_id=node["node_id"],
                action="EXTRACTION_SCRATCHPAD",
                detail=scratchpad[:1000],
            )

        tasks = []
        for task in result.tasks:
            # `task` is already a validated RawTask. Apply the same downstream
            # touches the regex path did: auto-flag low confidence, then
            # normalize ACs to the structured form so the rest of the pipeline
            # only deals with AcceptanceCriterion objects.
            if task.confidence < self.confidence_threshold:
                if "LOW_CONFIDENCE" not in task.flags:
                    task.flags.append("LOW_CONFIDENCE")
            # A4: mark tasks from a clipped section so the loss is visible.
            if section_truncated and "TRUNCATION" not in task.flags:
                task.flags.append("TRUNCATION")
            task.acceptance_criteria = normalize_acceptance_criteria(
                task.acceptance_criteria
            )
            tasks.append(task)

        self.audit.log(
            run_id=self.run_id,
            agent="ExtractionAgent",
            node_id=node["node_id"],
            action="EXTRACTED",
            detail=f"Extracted {len(tasks)} tasks from section '{node['title']}'",
        )

        return tasks