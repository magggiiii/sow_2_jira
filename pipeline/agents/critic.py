# pipeline/agents/critic.py
"""
Task Critic Agent — Self-critique pass per section.

After extraction produces ManagedTasks for a section, this agent runs a second
LLM pass that critiques each task for:
- Verb-first titles
- Testable / measurable acceptance criteria
- Atomic scope (not too broad)
- Likely duplicates

It auto-fixes low-risk issues (non-verb titles, untestable ACs, missing ACs)
when the critic's confidence is above threshold; otherwise it flags the task
via existing TaskFlag values (AMBIGUOUS_SCOPE, LOW_CONFIDENCE).

Designed to be called per-section between StateAgent.process and
coverage.mark_covered in the orchestrator (Wave 3 integration).

LLM seam: uses LLMClient.complete_json only. On any failure (LLM error,
non-list response, parse error) returns the input tasks unmodified plus an
empty report. The pipeline must never crash because the critic misbehaves.
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field, ValidationError

from audit.logger import AuditLogger
from core.agent_runner import AgentRunner
from core.guardrails import ConfidenceGate
from models.schemas import (
    AcceptanceCriterion,
    ManagedTask,
    TaskFlag,
    UnitInterval,
    normalize_acceptance_criteria,
)
from pipeline.llm_client import LLMClient


# ─── Schema ───────────────────────────────────────────────────────────────────

class CritiqueIssue(str, Enum):
    NON_VERB_TITLE = "non_verb_title"
    VAGUE_TITLE = "vague_title"
    UNTESTABLE_AC = "untestable_ac"
    TOO_BROAD = "too_broad"
    LIKELY_DUPLICATE = "likely_duplicate"
    MISSING_AC = "missing_ac"
    NOTHING_TO_FIX = "nothing_to_fix"


class TaskCritique(BaseModel):
    task_id: UUID
    issues: list[CritiqueIssue] = Field(default_factory=list)
    suggested_title: Optional[str] = None
    suggested_acceptance_criteria: Optional[list[AcceptanceCriterion]] = None
    confidence: UnitInterval = 0.0  # CONF-1: clamped into [0,1]
    reason: str = ""


class CritiqueReport(BaseModel):
    section_node_id: str
    reviewed_count: int = 0
    auto_fixed_count: int = 0
    flagged_count: int = 0
    critiques: list[TaskCritique] = Field(default_factory=list)


# ─── Prompts ──────────────────────────────────────────────────────────────────

CRITIC_SYSTEM_PROMPT = (
    "You are a senior Jira reviewer. You read a section of a Statement of Work "
    "and a list of extracted tasks, then critique each task for verb-first "
    "titles, testable acceptance criteria, atomic scope, and likely duplicates. "
    "Be conservative: prefer flagging over rewriting when in doubt. "
    "Return ONLY a valid JSON array. No prose. No markdown fences."
)

CRITIC_PROMPT_TEMPLATE = """You are reviewing extracted Jira tasks for one SOW section.

═══ ISSUES YOU CAN REPORT ═══
- "non_verb_title": Title does not start with a clear action verb (Create, Implement, Design, Configure, Integrate, Build, Set up, Develop, Define, Write, etc.).
- "vague_title": Title is too generic / non-specific (e.g. "User Authentication", "The dashboard").
- "untestable_ac": One or more acceptance criteria are unverifiable (e.g. "system works correctly", "users are happy").
- "too_broad": Scope spans multiple sprints or distinct deliverables — should be split.
- "likely_duplicate": Strongly overlaps another task in the same list.
- "missing_ac": Task has no acceptance_criteria at all.
- "nothing_to_fix": Task is fine.

═══ AUTO-FIX RULES ═══
- For "non_verb_title": provide `suggested_title` ONLY if you are confident the rewrite is faithful. Confidence ≥ 0.8 will be auto-applied.
- For "untestable_ac": provide `suggested_acceptance_criteria` (full replacement list, structured form). Confidence ≥ 0.8 will be auto-applied.
- For "missing_ac": provide `suggested_acceptance_criteria` (any confidence applies — the task currently has none).
- For "too_broad", "likely_duplicate", "vague_title": DO NOT provide a fix. Only report the issue.

═══ STRUCTURED ACCEPTANCE CRITERION SHAPE ═══
Each suggested_acceptance_criteria item:
  {{"condition": "<testable statement>", "type": "functional|nonfunctional|security|performance|usability", "verified_by": "test|review|demo|inspection"}}

═══ OUTPUT FORMAT ═══
Return a JSON array. One object per task you reviewed. Skip nothing — if a task is fine, return it with issues=["nothing_to_fix"].

Each object MUST have:
{{
  "task_id": "<the UUID string from the input>",
  "issues": ["<one or more issue names from the list above>"],
  "suggested_title": "<string or null>",
  "suggested_acceptance_criteria": [ ... ] or null,
  "confidence": 0.0 to 1.0,
  "reason": "<one short clause explaining your call>"
}}

Return ONLY the JSON array. No preamble.

═══ SECTION CONTEXT ═══
Section title: {section_title}
Section node_id: {node_id}

Section text (may be truncated):
{section_text}

═══ TASKS UNDER REVIEW ═══
{tasks_json}
"""


# ─── Agent ────────────────────────────────────────────────────────────────────

class TaskCritic:
    """
    Per-section task critic. One LLM call per section (not per task).

    Usage:
        critic = TaskCritic(llm_client, audit_logger, run_id, auto_fix_threshold=0.8)
        fixed_tasks, report = critic.critique(tasks, section_text, node)

    The returned `fixed_tasks` is a new list of ManagedTask objects with
    auto-fixes applied in-place on the same task instances. Tasks the critic
    chose only to flag get TaskFlag.AMBIGUOUS_SCOPE or TaskFlag.LOW_CONFIDENCE
    appended (no duplicates).
    """

    AGENT_NAME = "TaskCritic"
    MAX_SECTION_CHARS = 4000
    # Audit H-3: critiques below this confidence produce NO flag and NO mutation.
    FLAG_CONFIDENCE_FLOOR = 0.5

    def __init__(
        self,
        llm_client: LLMClient,
        audit_logger: AuditLogger,
        run_id: str,
        auto_fix_threshold: float = 0.8,
        flag_confidence_floor: float = FLAG_CONFIDENCE_FLOOR,
    ):
        self.llm = llm_client
        # Route the agent's single LLM call through the shared AgentRunner.
        # The runner is a verbatim passthrough over self.llm.complete_json, so
        # the underlying request (and observable behavior) is unchanged.
        self.runner = AgentRunner(llm_client)
        self.audit = audit_logger
        self.run_id = run_id
        self.auto_fix_threshold = auto_fix_threshold
        self.flag_confidence_floor = flag_confidence_floor
        # CONF-2: the flag-floor decision is delegated to the shared, pure
        # ConfidenceGate so this and coverage use one consolidated gate. Same
        # `>=` semantics and same numeric floor as the prior inline comparison.
        self._flag_gate = ConfidenceGate(
            field="confidence",
            floor=flag_confidence_floor,
            on_reject="flag_low_confidence",
        )

    # ─── Public API ──────────────────────────────────────────────────────────

    def critique(
        self,
        tasks: list[ManagedTask],
        section_text: str,
        node: dict,
    ) -> tuple[list[ManagedTask], CritiqueReport]:
        """
        Critique a section's tasks. Returns (tasks, report).

        - No LLM call is made for an empty task list.
        - On LLM or parse failure the input tasks are returned unmodified and
          the report has empty critiques.
        - Tasks are mutated in place (titles, ACs, flags) when auto-fix
          conditions are met. The same list reference is returned for the
          caller's convenience.
        """
        node_id = str(node.get("node_id") or "")
        report = CritiqueReport(
            section_node_id=node_id,
            reviewed_count=0,
            auto_fixed_count=0,
            flagged_count=0,
            critiques=[],
        )

        if not tasks:
            return tasks, report

        prompt = self._build_prompt(tasks, section_text, node)

        try:
            raw = self.runner.complete_json(
                prompt=prompt,
                system=CRITIC_SYSTEM_PROMPT,
                agent_name=self.AGENT_NAME,
                node_id=node_id,
            )
        except Exception as e:
            self.audit.log(
                run_id=self.run_id,
                agent=self.AGENT_NAME,
                node_id=node_id,
                action="CRITIQUE_LLM_ERROR",
                detail=f"LLM error during critique: {e}",
            )
            return tasks, report

        if not isinstance(raw, list):
            self.audit.log(
                run_id=self.run_id,
                agent=self.AGENT_NAME,
                node_id=node_id,
                action="CRITIQUE_PARSE_ERROR",
                detail=f"Critic returned non-list JSON (type={type(raw).__name__})",
            )
            return tasks, report

        # Index tasks by id for application.
        task_by_id: dict[str, ManagedTask] = {str(t.id): t for t in tasks}

        for entry in raw:
            critique = self._parse_critique(entry)
            if critique is None:
                continue
            target = task_by_id.get(str(critique.task_id))
            if target is None:
                # Unknown task id — log and skip
                self.audit.log(
                    run_id=self.run_id,
                    agent=self.AGENT_NAME,
                    node_id=node_id,
                    action="CRITIQUE_UNKNOWN_TASK",
                    detail=f"Critic referenced task_id {critique.task_id} not in input",
                )
                continue

            report.critiques.append(critique)
            report.reviewed_count += 1

            applied_fix = self._apply(target, critique, node_id)
            if applied_fix:
                report.auto_fixed_count += 1
            elif (
                critique.issues
                and not _only_nothing_to_fix(critique.issues)
                and self._flag_gate.admit(critique)
            ):
                # Audit H-3: only count as flagged when the critique cleared the
                # confidence floor (below-floor critiques are no-ops).
                report.flagged_count += 1

        self.audit.log(
            run_id=self.run_id,
            agent=self.AGENT_NAME,
            node_id=node_id,
            action="CRITIQUE_RUN",
            detail=(
                f"reviewed={report.reviewed_count} "
                f"auto_fixed={report.auto_fixed_count} "
                f"flagged={report.flagged_count}"
            ),
        )

        return tasks, report

    # ─── Internals ───────────────────────────────────────────────────────────

    def _build_prompt(
        self, tasks: list[ManagedTask], section_text: str, node: dict
    ) -> str:
        truncated = (section_text or "")[: self.MAX_SECTION_CHARS]
        if len(section_text or "") > self.MAX_SECTION_CHARS:
            truncated += "\n\n[NOTE: section truncated for critique]"

        tasks_payload = []
        for t in tasks:
            tasks_payload.append({
                "task_id": str(t.id),
                "title": t.title,
                "short_description": t.short_description,
                "acceptance_criteria": [
                    {
                        "condition": ac.condition,
                        "type": ac.type.value if hasattr(ac.type, "value") else str(ac.type),
                        "verified_by": ac.verified_by,
                    }
                    for ac in (t.acceptance_criteria or [])
                ],
            })

        tasks_json = json.dumps(tasks_payload, indent=2, ensure_ascii=False)

        return CRITIC_PROMPT_TEMPLATE.format(
            section_title=node.get("title", ""),
            node_id=str(node.get("node_id") or ""),
            section_text=truncated,
            tasks_json=tasks_json,
        )

    def _parse_critique(self, entry) -> Optional[TaskCritique]:
        """Parse one raw critique dict from the LLM. Return None on failure."""
        if not isinstance(entry, dict):
            return None
        try:
            # Issues come in as arbitrary strings; filter to known enum values.
            raw_issues = entry.get("issues") or []
            if not isinstance(raw_issues, list):
                raw_issues = [raw_issues]
            cleaned_issues: list[CritiqueIssue] = []
            for i in raw_issues:
                try:
                    cleaned_issues.append(CritiqueIssue(str(i).strip().lower()))
                except ValueError:
                    continue

            # Normalize suggested ACs (can be strings or dicts coming from LLM)
            suggested_acs = entry.get("suggested_acceptance_criteria")
            if suggested_acs is not None:
                suggested_acs = normalize_acceptance_criteria(suggested_acs)

            return TaskCritique(
                task_id=UUID(str(entry["task_id"])),
                issues=cleaned_issues,
                suggested_title=entry.get("suggested_title"),
                suggested_acceptance_criteria=suggested_acs,
                confidence=float(entry.get("confidence") or 0.0),
                reason=str(entry.get("reason") or ""),
            )
        except (KeyError, ValueError, TypeError, ValidationError):
            return None

    def _apply(
        self, task: ManagedTask, critique: TaskCritique, node_id: str
    ) -> bool:
        """
        Apply auto-fixes and flags. Returns True if any auto-fix was applied.

        Auto-fix policy:
        - NON_VERB_TITLE + suggested_title + confidence >= threshold -> replace title
        - UNTESTABLE_AC + suggested_acceptance_criteria + confidence >= threshold -> replace ACs
        - MISSING_AC + suggested_acceptance_criteria -> add ACs (any confidence)
        - TOO_BROAD -> never auto-fix; flag AMBIGUOUS_SCOPE
        - VAGUE_TITLE -> never auto-fix; flag LOW_CONFIDENCE

        Audit H-3: all flag mutations are gated on confidence >= flag_confidence_floor.
        A critique below the floor (incl. 0.0) produces NO flag and NO mutation.
        LIKELY_DUPLICATE is intentionally NOT handled — dedup owns duplicate detection.
        """
        issues = set(critique.issues)
        applied = False
        applied_issues: list[str] = []

        # Treat nothing_to_fix as a no-op (still counted as reviewed).
        if not issues or issues == {CritiqueIssue.NOTHING_TO_FIX}:
            return False

        # Audit H-3: below the confidence floor we neither mutate nor flag.
        # CONF-2: same `>=` floor, now delegated to the shared ConfidenceGate.
        if not self._flag_gate.admit(critique):
            return False

        # 1. Missing AC — any confidence, only if the task currently has none.
        if (
            CritiqueIssue.MISSING_AC in issues
            and critique.suggested_acceptance_criteria
            and not task.acceptance_criteria
        ):
            task.acceptance_criteria = list(critique.suggested_acceptance_criteria)
            applied = True
            applied_issues.append(CritiqueIssue.MISSING_AC.value)

        # 2. Untestable AC — confident replacement.
        if (
            CritiqueIssue.UNTESTABLE_AC in issues
            and critique.suggested_acceptance_criteria
            and critique.confidence >= self.auto_fix_threshold
        ):
            task.acceptance_criteria = list(critique.suggested_acceptance_criteria)
            applied = True
            applied_issues.append(CritiqueIssue.UNTESTABLE_AC.value)
        elif CritiqueIssue.UNTESTABLE_AC in issues:
            # Below threshold or no suggestion — flag low confidence.
            _add_flag(task, TaskFlag.LOW_CONFIDENCE)

        # 3. Non-verb title — confident rewrite.
        if (
            CritiqueIssue.NON_VERB_TITLE in issues
            and critique.suggested_title
            and critique.confidence >= self.auto_fix_threshold
        ):
            task.title = critique.suggested_title.strip()
            applied = True
            applied_issues.append(CritiqueIssue.NON_VERB_TITLE.value)
        elif CritiqueIssue.NON_VERB_TITLE in issues:
            _add_flag(task, TaskFlag.LOW_CONFIDENCE)

        # 4. Too broad — always flag, never fix.
        if CritiqueIssue.TOO_BROAD in issues:
            _add_flag(task, TaskFlag.AMBIGUOUS_SCOPE)

        # 5. Vague title — flag. (LIKELY_DUPLICATE is owned by dedup, not the critic.)
        if CritiqueIssue.VAGUE_TITLE in issues:
            _add_flag(task, TaskFlag.LOW_CONFIDENCE)

        if applied:
            self.audit.log(
                run_id=self.run_id,
                agent=self.AGENT_NAME,
                node_id=node_id,
                action="CRITIQUE_AUTO_FIX",
                task_id=str(task.id),
                detail=f"applied={','.join(applied_issues)} conf={critique.confidence:.2f}",
            )
        else:
            flagged = sorted(i.value for i in issues if i != CritiqueIssue.NOTHING_TO_FIX)
            if flagged:
                self.audit.log(
                    run_id=self.run_id,
                    agent=self.AGENT_NAME,
                    node_id=node_id,
                    action="CRITIQUE_FLAGGED",
                    task_id=str(task.id),
                    detail=f"issues={','.join(flagged)} conf={critique.confidence:.2f}",
                )

        return applied


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _add_flag(task: ManagedTask, flag: TaskFlag) -> None:
    if flag not in task.flags:
        task.flags.append(flag)


def _only_nothing_to_fix(issues: list[CritiqueIssue]) -> bool:
    return all(i == CritiqueIssue.NOTHING_TO_FIX for i in issues)
