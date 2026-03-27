# pipeline/agents/gap_recovery.py

from models.schemas import RawTask, TaskFlag
from pipeline.llm_client import LLMClient
from audit.logger import AuditLogger

GAP_SYSTEM_PROMPT = """You are a senior Jira project manager reviewing a SOW section that was initially skipped.
Return ONLY valid JSON. No explanation. No markdown fences. No preamble."""

GAP_PROMPT_TEMPLATE = """You are reviewing a section of a Statement of Work that our system found NO tasks in.

This may be because:
1. The section genuinely contains no actionable work items (context, background, legal terms, definitions)
2. Our system missed tasks

Carefully re-read the section. If you find actionable work items, extract them as Jira tickets:

═══ RULES ═══
- Only real, actionable work — not context or boilerplate
- Each task must be atomic: completable by one person in 1-2 sprints
- Title MUST start with a verb: Create, Implement, Design, Configure, Integrate, Build, Set up
- Acceptance criteria MUST be testable checklist items: "[ ] Condition that can be verified"
- If there truly are no tasks, return an empty array []

Use the same JSON schema:
{{
  "title": "string — verb-first, max 80 chars",
  "short_description": "string — 1-2 sentences, what this task delivers",
  "acceptance_criteria": ["[ ] testable condition", ...] or null,
  "use_case": "string — As a [role], I want [goal] so that [benefit]" or null,
  "considerations_constraints": ["string", ...] or null,
  "deliverables": ["string — concrete output", ...] or null,
  "mockup_prototype": "string" or null,
  "confidence": 0.0 to 1.0,
  "flags": ["GAP_RECOVERED", ...],
  "continues_to_next": false
}}

Return ONLY a valid JSON array. No preamble.

SOW Section Title: {section_title}
SOW Pages: {page_start} to {page_end}

Section Text:
{section_text}
"""


class GapRecoveryAgent:

    def __init__(
        self,
        llm_client: LLMClient,
        audit_logger: AuditLogger,
        run_id: str,
        max_iterations: int = 2,
    ):
        self.llm = llm_client
        self.audit = audit_logger
        self.run_id = run_id
        self.max_iterations = max_iterations

    def _build_prompt(self, node: dict, section_text: str) -> str:
        return GAP_PROMPT_TEMPLATE.format(
            section_title=node["title"],
            page_start=node["page_start"],
            page_end=node["page_end"],
            section_text=section_text[:16000],
        )

    def recover(
        self,
        uncovered_nodes: list[dict],
        indexer,
    ) -> list[tuple[RawTask, dict]]:
        """
        Returns list of (RawTask, source_node) pairs — NOT just RawTask list.
        This preserves actual SOW section attribution for every recovered task.
        """
        results = []
        for node in uncovered_nodes[:self.max_iterations * 5]:
            section_text = indexer.get_node_text(node)
            if len(section_text.strip()) < 100:
                continue
                
            try:
                raw_tasks = self.llm.complete_json(
                    prompt=self._build_prompt(node, section_text),
                    system=GAP_SYSTEM_PROMPT,
                    agent_name="GapRecoveryAgent",
                    node_id=node["node_id"]
                )
                
                if not isinstance(raw_tasks, list):
                    continue
                    
                for raw in raw_tasks:
                    try:
                        raw_obj = RawTask(**raw)
                        raw_obj.flags.append(TaskFlag.GAP_RECOVERED)
                        results.append((raw_obj, node))   # ← tuple, not just task
                        self.audit.log(
                            run_id=self.run_id,
                            agent="GapRecoveryAgent",
                            node_id=node["node_id"],
                            action="RECOVERED_TASK",
                            detail=f"Recovered: '{raw_obj.title}'",
                        )
                    except Exception as e:
                        self.audit.log(
                            run_id=self.run_id,
                            agent="GapRecoveryAgent",
                            node_id=node["node_id"],
                            action="RECOVERY_PARSE_ERROR",
                            detail=str(e),
                        )
            except Exception as e:
                self.audit.log(
                    run_id=self.run_id,
                    agent="GapRecoveryAgent",
                    node_id=node["node_id"],
                    action="RECOVERY_ERROR",
                    detail=str(e),
                )
                
        if results:
            self.audit.log(
                run_id=self.run_id,
                agent="GapRecoveryAgent",
                action="RECOVERY_COMPLETE",
                detail=f"Recovered {len(results)} additional tasks from gap nodes",
            )
        else:
            self.audit.log(
                run_id=self.run_id,
                agent="GapRecoveryAgent",
                action="NO_GAPS_RECOVERED",
                detail="No actionable tasks found in gap nodes",
            )
            
        return results