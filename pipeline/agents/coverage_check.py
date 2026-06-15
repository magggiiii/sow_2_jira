# pipeline/agents/coverage_check.py
"""
Semantic coverage check (Wave 2A — Improvement #2).

Companion to the structural CoverageTracker in pipeline/coverage.py. Where the
tracker only knows "this node has ≥1 task" or "this node has zero tasks", this
agent asks the LLM the harder question: given a section's text plus the titles
and acceptance criteria of the tasks we DID extract, what *additional*
actionable deliverables are sitting in this section that we missed?

Dense sections with under-extraction (one task pulled out of three pages of
spec) are completely invisible to structural coverage — they look fully
covered. This check surfaces those misses so the orchestrator (Wave 3) can
either flag the section INCOMPLETE or trigger targeted gap recovery.

The checker is graceful: short sections, sections with no extracted tasks (the
existing gap_recovery agent owns that path), and any LLM/JSON failure all
return an empty report. The rest of the pipeline must not depend on this
agent producing a non-empty result.
"""

from __future__ import annotations

import datetime
import json
from typing import Optional

from pydantic import BaseModel, Field

from audit.logger import AuditLogger
from core.agent_runner import AgentRunner, InstructorError
from core.guardrails import ConfidenceGate
from models.schemas import (
    AcceptanceCriterion,
    ManagedTask,
    UnitInterval,
    normalize_acceptance_criteria,
)
from pipeline.llm_client import LLMClient


# ─── Output models (kept module-local, NOT in models/schemas.py) ──────────────

class MissedItem(BaseModel):
    """A concrete actionable deliverable the LLM thinks was dropped."""
    description: str                              # The missed deliverable, 1-2 sentences
    # CONF-1: clamped into [0,1] by the UnitInterval BeforeValidator; no Field
    # ge/le (emitting minimum/maximum breaks Anthropic strict structured output).
    confidence: UnitInterval                       # Checker confidence this IS a miss
    reason: str                                   # Why it's an actionable miss


class SectionCoverageReport(BaseModel):
    """Per-node semantic coverage outcome."""
    node_id: str
    extracted_count: int
    missed_items: list[MissedItem] = Field(default_factory=list)
    checker_confidence: UnitInterval = 0.0        # CONF-1: clamped into [0,1]
    checked_at: datetime.datetime = Field(default_factory=datetime.datetime.utcnow)


class CoverageAudit(BaseModel):
    """Top-level Instructor ``response_model`` (C-5).

    The coverage check emits a JSON array of misses, so the validated payload is
    wrapped in a single object with one ``missed_items`` list of MissedItem.
    Each MissedItem is schema-validated (description/reason present, confidence
    clamped into [0,1]); the agent then applies its own min_confidence filter —
    that's domain gating, not validation, so it stays in the agent.
    """
    missed_items: list[MissedItem] = Field(default_factory=list)


# ─── Confidence gate (Wave 3-F, audit C-4) ────────────────────────────────────

def should_flag_section_incomplete(
    report: SectionCoverageReport,
    min_confidence: float = 0.6,
) -> bool:
    """
    Decide whether a section's tasks should be flagged INCOMPLETE.

    Pure + side-effect-free so the gate is unit-testable without running the
    orchestrator. Returns True only when BOTH hold:
      - the checker is confident enough (report.checker_confidence >= min_confidence), and
      - the report has genuine missed_items.

    Audit C-4 ("the 100% INCOMPLETE bomb"): the orchestrator used to flag every
    task in a section the instant report.missed_items was non-empty, ignoring the
    checker's own confidence. Gating on checker_confidence stops low-confidence
    misses from poisoning an otherwise-complete run.

    NOTE: This only gates the per-section flag on confidence. The full post-dedup,
    run-wide INCOMPLETE restructure is a separate later step, gated by INV-4 (the
    eval cassette asserting INCOMPLETE-rate) before the runner flip.
    """
    if not report.missed_items:
        return False
    # CONF-2: same inclusive `>=` floor, delegated to the shared ConfidenceGate
    # so the critic and coverage share one consolidated confidence gate.
    gate = ConfidenceGate(field="checker_confidence", floor=min_confidence)
    return gate.admit_value(report.checker_confidence)


# ─── Prompt ───────────────────────────────────────────────────────────────────

COVERAGE_SYSTEM_PROMPT = """You are a meticulous Jira project auditor reviewing whether a
SOW section's actionable deliverables were fully captured as Jira tasks.
Return ONLY valid JSON. No explanation. No markdown fences. No preamble."""

COVERAGE_PROMPT_TEMPLATE = """You are auditing the extraction of Jira tasks from a Statement
of Work (SOW) section. Below you have the section text and the tasks our system
already extracted from it (titles + acceptance criteria). Your job: identify
concrete, actionable deliverables in the section text that are NOT covered by
those tasks.

═══ STRICT RULES ═══
- Report ONLY actionable, concrete work items. SKIP:
    - Background / context / company descriptions
    - Definitions, glossary, acronyms
    - Legal terms, payment terms, warranties, confidentiality
    - Signatures, approvals, dates
    - General assumptions and policy statements
- A "miss" is something a developer or designer could pick up and ship that is
  clearly described in the section but not represented in the extracted task
  titles or acceptance criteria.
- BE CONSERVATIVE. False positives are worse than false negatives — only flag
  items you are genuinely confident were dropped.
- If everything actionable in the section is already covered, return [].

═══ OUTPUT FORMAT ═══
Return a JSON array. Each element must have EXACTLY these fields:
{{
  "description": "string — 1-2 sentences describing the missed deliverable",
  "confidence": 0.0 to 1.0,
  "reason": "string — short clause explaining why this is an actionable miss"
}}

Return ONLY the JSON array. No preamble. No explanation. No markdown.

═══ SECTION ═══
Title: {section_title}
Pages: {page_start} to {page_end}

Section Text:
{section_text}

═══ ALREADY-EXTRACTED TASKS ═══
{extracted_summary}
"""


class CoverageChecker:
    """
    Per-section semantic coverage auditor.

    Usage:
        checker = CoverageChecker(llm_client, audit_logger, run_id)
        report = checker.check_section(node, section_text, extracted_tasks)
        if report.missed_items:
            # Wave 3 orchestrator: flag INCOMPLETE or trigger targeted gap recovery.
            ...
    """

    def __init__(
        self,
        llm_client: LLMClient,
        audit_logger: AuditLogger,
        run_id: str,
        min_confidence: float = 0.6,
        max_section_chars: int = 16000,
    ):
        self.llm = llm_client
        # Route the single coverage-check LLM call through the AgentRunner's
        # Instructor-validated structured-output seam (complete_structured).
        self.runner = AgentRunner(llm_client)
        self.audit = audit_logger
        self.run_id = run_id
        self.min_confidence = min_confidence
        self.max_section_chars = max_section_chars

    # ─── public API ─────────────────────────────────────────────────────────

    def check_section(
        self,
        node: dict,
        section_text: str,
        extracted_tasks: list[ManagedTask],
    ) -> SectionCoverageReport:
        """
        Returns a SectionCoverageReport. If section_text is short (< 100 chars)
        or extracted_tasks is empty, returns an empty report without an LLM call
        — gap_recovery owns the zero-task path.
        """
        node_id = node.get("node_id", "")
        empty = SectionCoverageReport(
            node_id=node_id,
            extracted_count=len(extracted_tasks),
        )

        if not section_text or len(section_text.strip()) < 100:
            self.audit.log(
                run_id=self.run_id,
                agent="CoverageChecker",
                node_id=node_id,
                action="COVERAGE_CHECK_SKIPPED",
                detail=f"node={node_id} reason=short_section len={len(section_text or '')}",
            )
            return empty

        if not extracted_tasks:
            # Zero-task case belongs to gap_recovery; we don't double up.
            self.audit.log(
                run_id=self.run_id,
                agent="CoverageChecker",
                node_id=node_id,
                action="COVERAGE_CHECK_SKIPPED",
                detail=f"node={node_id} reason=no_extracted_tasks",
            )
            return empty

        prompt = self._build_prompt(node, section_text, extracted_tasks)

        # C-5: route through the Instructor-validated structured-output seam.
        # The checker emits a JSON array of misses, so the response_model is a
        # CoverageAudit wrapper. Each MissedItem arrives schema-validated
        # (description/reason present, confidence clamped) so the per-item
        # dict-parsing dance is gone; the agent's min_confidence filter still
        # applies below. Any structured-output failure surfaces as one
        # InstructorError, recorded as COVERAGE_CHECK_ERROR and degraded to an
        # empty report — never a crash.
        try:
            audit_result = self.runner.complete_structured(
                prompt=prompt,
                response_model=CoverageAudit,
                system=COVERAGE_SYSTEM_PROMPT,
                agent_name="CoverageChecker",
                node_id=node_id,
            )
        except InstructorError as e:
            self.audit.log(
                run_id=self.run_id,
                agent="CoverageChecker",
                node_id=node_id,
                action="COVERAGE_CHECK_ERROR",
                detail=f"node={node_id} error={e}",
            )
            return empty

        parsed = self._filter_missed_items(audit_result.missed_items)
        report = SectionCoverageReport(
            node_id=node_id,
            extracted_count=len(extracted_tasks),
            missed_items=parsed,
            checker_confidence=self._overall_confidence(parsed),
        )

        self.audit.log(
            run_id=self.run_id,
            agent="CoverageChecker",
            node_id=node_id,
            action="COVERAGE_CHECK",
            detail=f"node={node_id} extracted={len(extracted_tasks)} missed={len(parsed)}",
        )
        for miss in parsed:
            self.audit.log(
                run_id=self.run_id,
                agent="CoverageChecker",
                node_id=node_id,
                action="COVERAGE_MISS",
                detail=f"node={node_id} desc={miss.description[:80]}",
            )

        return report

    # ─── internals ──────────────────────────────────────────────────────────

    def _build_prompt(
        self,
        node: dict,
        section_text: str,
        extracted_tasks: list[ManagedTask],
    ) -> str:
        text = section_text
        if len(text) > self.max_section_chars:
            # A4: surface truncation rather than silently clipping.
            self.audit.log(
                run_id=self.run_id,
                agent="CoverageChecker",
                node_id=node.get("node_id", ""),
                action="SECTION_TRUNCATED",
                detail=(
                    f"Section '{node.get('title', '')}' truncated {len(text)} → "
                    f"{self.max_section_chars} chars for coverage check"
                ),
            )
            text = text[: self.max_section_chars] + "\n\n[NOTE: section truncated for audit]"

        extracted_summary = self._summarize_extracted(extracted_tasks)
        return COVERAGE_PROMPT_TEMPLATE.format(
            section_title=node.get("title", ""),
            page_start=node.get("page_start", ""),
            page_end=node.get("page_end", ""),
            section_text=text,
            extracted_summary=extracted_summary,
        )

    def _summarize_extracted(self, extracted_tasks: list[ManagedTask]) -> str:
        """
        Flatten each task into {title, acceptance_criteria: [condition strings]}.
        Uses normalize_acceptance_criteria so legacy plain-string ACs (pre-1B
        checkpoints) and structured ACs (post-1B) are both rendered uniformly.
        """
        summary = []
        for task in extracted_tasks:
            ac_conditions: list[str] = []
            normalized = normalize_acceptance_criteria(task.acceptance_criteria)
            for ac in normalized or []:
                if isinstance(ac, AcceptanceCriterion):
                    ac_conditions.append(ac.condition)
            summary.append({
                "title": task.title,
                "acceptance_criteria": ac_conditions,
            })
        try:
            return json.dumps(summary, indent=2, ensure_ascii=False)
        except (TypeError, ValueError):
            # Defensive: should never happen with the shape above, but if a
            # weird task object slipped through, fall back to titles only.
            return json.dumps(
                [{"title": t.title, "acceptance_criteria": []} for t in extracted_tasks],
                indent=2,
                ensure_ascii=False,
            )

    def _filter_missed_items(self, items: list[MissedItem]) -> list[MissedItem]:
        """Apply the checker's confidence gate to Instructor-validated misses.

        Items are already schema-valid MissedItem instances (C-5), so the only
        domain step left is dropping misses below ``min_confidence`` — the same
        ``confidence < min_confidence`` cut the regex path applied per item.
        """
        return [m for m in items if m.confidence >= self.min_confidence]

    @staticmethod
    def _overall_confidence(items: list[MissedItem]) -> float:
        if not items:
            return 0.0
        return round(sum(i.confidence for i in items) / len(items), 3)
