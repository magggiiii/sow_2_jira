# pipeline/agents/gap_recovery.py

from typing import Optional, Union

from pydantic import BaseModel, Field

from core.agent_runner import AgentRunner, InstructorError
from models.schemas import AcceptanceCriterion, RawTask, TaskDependency, TaskFlag
from pipeline.llm_client import LLMClient
from audit.logger import AuditLogger
from prompts import registry


class RawRecoveredTask(BaseModel):
    """Permissive recovered-task item — the Instructor batch element (C-5).

    Every field is optional with a default so a partial/dirty task can be
    represented without failing the whole batch. The agent then validates each
    item into a strict :class:`RawTask`, skipping (and auditing
    RECOVERY_PARSE_ERROR for) any that fail — preserving the per-task recovery
    resilience the regex path had (a malformed task never drops its valid
    siblings). ``confidence`` is left a plain Optional[float]; the strict RawTask
    re-validation clamps it into [0,1].
    """
    title: str = ""
    short_description: str = ""
    acceptance_criteria: Optional[list[Union[AcceptanceCriterion, str]]] = None
    use_case: Optional[str] = None
    considerations_constraints: Optional[list[str]] = None
    deliverables: Optional[list[str]] = None
    mockup_prototype: Optional[str] = None
    confidence: Optional[float] = None
    flags: list[str] = Field(default_factory=list)
    continues_to_next: bool = False
    dependencies: list[TaskDependency] = Field(default_factory=list)


class GapRecoveryResult(BaseModel):
    """Top-level Instructor ``response_model``: the recovered-task array wrapped
    in a single object with one ``tasks`` field."""
    tasks: list[RawRecoveredTask] = Field(default_factory=list)

GAP_SYSTEM_PROMPT = registry.load("gap.system.v1")

GAP_PROMPT_TEMPLATE = registry.load("gap.user.v1")


class GapRecoveryAgent:

    def __init__(
        self,
        llm_client: LLMClient,
        audit_logger: AuditLogger,
        run_id: str,
        max_iterations: int = 2,
    ):
        self.llm = llm_client
        # Route this agent's single LLM call through the AgentRunner's
        # Instructor-validated structured-output seam (complete_structured);
        # see recover() for the per-node failure/degrade handling.
        self.runner = AgentRunner(llm_client)
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
                
            # C-5: route through the Instructor-validated structured-output seam.
            # The runner returns a validated GapRecoveryResult whose `tasks` are
            # permissive RawRecoveredTask items. A whole-call failure surfaces as
            # one InstructorError, recorded as RECOVERY_ERROR and skipped for this
            # node (recover() still returns whatever other nodes produced).
            try:
                result = self.runner.complete_structured(
                    prompt=self._build_prompt(node, section_text),
                    response_model=GapRecoveryResult,
                    system=GAP_SYSTEM_PROMPT,
                    agent_name="GapRecoveryAgent",
                    node_id=node["node_id"],
                )
            except InstructorError as e:
                self.audit.log(
                    run_id=self.run_id,
                    agent="GapRecoveryAgent",
                    node_id=node["node_id"],
                    action="RECOVERY_ERROR",
                    detail=str(e),
                )
                continue

            for item in result.tasks:
                # Re-validate each permissive item into a strict RawTask. A dirty
                # item (e.g. missing confidence) is dropped + audited here so one
                # bad task never loses its valid siblings — preserving the
                # per-task recovery resilience the regex path had.
                try:
                    raw_obj = RawTask.model_validate(item.model_dump())
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