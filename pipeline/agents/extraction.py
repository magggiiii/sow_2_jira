# pipeline/agents/extraction.py

import json
from models.schemas import RawTask, TaskFlag
from pipeline.llm_client import LLMClient
from audit.logger import AuditLogger

EXTRACTION_SYSTEM_PROMPT = """You are a senior Jira project manager extracting actionable work items from a Statement of Work (SOW).
You think in terms of real Jira boards: Epics, Stories, Tasks, and Sub-tasks.
Return ONLY valid JSON. No explanation. No markdown fences. No preamble."""

EXTRACTION_PROMPT_TEMPLATE = """You are extracting actionable Jira tickets from a Statement of Work (SOW) section.

═══ TASK GRANULARITY ═══
- Each task MUST be a single, atomic unit of work completable by one person in 1-2 sprints (2-4 weeks).
- If a section describes a large system or module, DECOMPOSE it into multiple focused tasks.
- NEVER create a single task like "Implement the entire reporting module" — break it down.
- Think: "Could a developer pick this up on Monday and demo it in the sprint review?"

═══ TITLE FORMAT ═══
- MUST start with an action verb: Create, Implement, Design, Configure, Integrate, Build, Set up, Develop, Define, Write
- MUST follow: "[Verb] [specific object] [optional context]"
- Max 80 characters
- GOOD: "Create user authentication API with JWT tokens"
- GOOD: "Design database schema for order management"  
- GOOD: "Integrate payment gateway with Stripe API"
- BAD: "User Authentication" (no verb, too vague)
- BAD: "The system should handle payments" (not actionable)
- BAD: "Implement the entire backend system" (too broad)

═══ ACCEPTANCE CRITERIA ═══
- MUST be testable, measurable conditions — not descriptions of features.
- Use checklist format: "[ ] Condition that can be verified"
- GOOD: "[ ] API returns 200 OK with user profile JSON when valid token is provided"
- GOOD: "[ ] Dashboard loads within 3 seconds on 4G connection"
- GOOD: "[ ] Error message is displayed when invalid email format is entered"
- BAD: "The system works correctly" (not testable)
- BAD: "Users can log in" (too vague — HOW do we verify?)
- If you cannot determine testable criteria from the text, set to null and add "NO_ACCEPTANCE_CRITERIA" to flags.

═══ WHAT TO EXTRACT ═══
- Functional requirements → development tasks
- Integration points → integration tasks
- Data migration needs → migration tasks
- Configuration/setup work → setup tasks
- Testing requirements explicitly mentioned → testing tasks

═══ WHAT TO SKIP ═══
- Background context, company descriptions, project overviews
- Legal terms, payment terms, warranties, confidentiality clauses
- Definitions, glossary items, acronyms
- General assumptions (unless they imply work)
- Signatures, approval sections
- If the ENTIRE section is non-actionable, return an empty array []

═══ FLAGS ═══
- NO_ACCEPTANCE_CRITERIA: Cannot determine testable acceptance criteria
- AMBIGUOUS_SCOPE: The scope is vague, contradictory, or could be interpreted multiple ways
- INCOMPLETE: Key information is missing (e.g., mentions an API but not what it should do)
- LOW_CONFIDENCE: You are less than 60% sure this is a real task

═══ OUTPUT FORMAT ═══
Each object in the returned array must have EXACTLY these fields:
{{
  "title": "string — verb-first, max 80 chars",
  "short_description": "string — 1-2 sentences, what this task delivers",
  "acceptance_criteria": ["[ ] testable condition", ...] or null,
  "use_case": "string — As a [role], I want [goal] so that [benefit]" or null,
  "considerations_constraints": ["string", ...] or null,
  "deliverables": ["string — concrete output", ...] or null,
  "mockup_prototype": "string — reference to mockup/prototype" or null,
  "confidence": 0.0 to 1.0,
  "flags": ["FLAG_NAME", ...],
  "continues_to_next": true or false
}}

Return ONLY a valid JSON array. No preamble. No explanation. No markdown.

{hierarchy_context}
SOW Section Title: {section_title}
SOW Pages: {page_start} to {page_end}

Section Text:
{section_text}
"""

HIERARCHY_CONTEXT = {
    "flat": """═══ HIERARCHY CONTEXT ═══
Target: FLAT (standalone Tasks, no parent).
Extract medium-grained, self-contained tasks. Each task should make sense on its own without parent context.
""",
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
        self.audit = audit_logger
        self.run_id = run_id
        self.confidence_threshold = confidence_threshold
        self.max_section_chars = max_section_chars

    def extract(self, node: dict, section_text: str, hierarchy: str = "epic_task", status_callback=None) -> list[RawTask]:
        """
        Runs extraction on a single PageIndex node.
        Returns a list of RawTask objects.
        hierarchy: one of 'flat', 'epic_task', 'story_subtask' — adjusts extraction granularity.
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

        if len(section_text) > self.max_section_chars:
            section_text = section_text[:self.max_section_chars]
            truncation_notice = (
                f"\n\n[NOTE: This section was truncated at {self.max_section_chars} characters. "
                f"Review the original SOW section '{node['title']}' pages "
                f"{node['page_start']}-{node['page_end']} for any tasks not captured here.]"
            )
            section_text += truncation_notice

        prompt = EXTRACTION_PROMPT_TEMPLATE.format(
            section_title=node["title"],
            page_start=node["page_start"],
            page_end=node["page_end"],
            section_text=section_text,
            hierarchy_context=HIERARCHY_CONTEXT.get(hierarchy, HIERARCHY_CONTEXT["epic_task"]),
        )

        try:
            raw_list = self.llm.complete_json(
                prompt=prompt,
                system=EXTRACTION_SYSTEM_PROMPT,
                agent_name="ExtractionAgent",
                node_id=node["node_id"],
            )
        except (ValueError, RuntimeError) as e:
            self.audit.log(
                run_id=self.run_id,
                agent="ExtractionAgent",
                node_id=node["node_id"],
                action="EXTRACTION_ERROR",
                detail=str(e),
            )
            return []

        if not isinstance(raw_list, list):
            self.audit.log(
                run_id=self.run_id,
                agent="ExtractionAgent",
                node_id=node["node_id"],
                action="EXTRACTION_ERROR",
                detail="LLM returned non-list JSON",
            )
            return []

        tasks = []
        for raw in raw_list:
            try:
                task = RawTask(**raw)
                # Auto-flag low confidence
                if task.confidence < self.confidence_threshold:
                    if "LOW_CONFIDENCE" not in task.flags:
                        task.flags.append("LOW_CONFIDENCE")
                tasks.append(task)
            except Exception as e:
                self.audit.log(
                    run_id=self.run_id,
                    agent="ExtractionAgent",
                    node_id=node["node_id"],
                    action="TASK_PARSE_ERROR",
                    detail=f"Could not parse task: {e} | raw: {str(raw)[:200]}",
                )

        self.audit.log(
            run_id=self.run_id,
            agent="ExtractionAgent",
            node_id=node["node_id"],
            action="EXTRACTED",
            detail=f"Extracted {len(tasks)} tasks from section '{node['title']}'",
        )

        return tasks