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
from models.schemas import (
    AcceptanceCriterion,
    ManagedTask,
    normalize_acceptance_criteria,
)
from pipeline.llm_client import LLMClient


# ─── Output models (kept module-local, NOT in models/schemas.py) ──────────────

class MissedItem(BaseModel):
    """A concrete actionable deliverable the LLM thinks was dropped."""
    description: str                              # The missed deliverable, 1-2 sentences
    confidence: float = Field(ge=0.0, le=1.0)     # Checker confidence this IS a miss
    reason: str                                   # Why it's an actionable miss


class SectionCoverageReport(BaseModel):
    """Per-node semantic coverage outcome."""
    node_id: str
    extracted_count: int
    missed_items: list[MissedItem] = Field(default_factory=list)
    checker_confidence: float = 0.0               # Overall confidence in the report
    checked_at: datetime.datetime = Field(default_factory=datetime.datetime.utcnow)


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

        try:
            raw = self.llm.complete_json(
                prompt=prompt,
                system=COVERAGE_SYSTEM_PROMPT,
                agent_name="CoverageChecker",
                node_id=node_id,
            )
        except (ValueError, RuntimeError) as e:
            self.audit.log(
                run_id=self.run_id,
                agent="CoverageChecker",
                node_id=node_id,
                action="COVERAGE_CHECK_ERROR",
                detail=f"node={node_id} error={e}",
            )
            return empty

        if not isinstance(raw, list):
            self.audit.log(
                run_id=self.run_id,
                agent="CoverageChecker",
                node_id=node_id,
                action="COVERAGE_CHECK_ERROR",
                detail=f"node={node_id} reason=non_list_response type={type(raw).__name__}",
            )
            return empty

        parsed = self._parse_missed_items(raw, node_id)
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

    def _parse_missed_items(self, raw_list: list, node_id: str) -> list[MissedItem]:
        items: list[MissedItem] = []
        for entry in raw_list:
            if not isinstance(entry, dict):
                continue
            try:
                miss = MissedItem(**entry)
            except Exception as e:
                self.audit.log(
                    run_id=self.run_id,
                    agent="CoverageChecker",
                    node_id=node_id,
                    action="COVERAGE_MISS_PARSE_ERROR",
                    detail=f"node={node_id} error={e} raw={str(entry)[:160]}",
                )
                continue
            if miss.confidence < self.min_confidence:
                continue
            items.append(miss)
        return items

    @staticmethod
    def _overall_confidence(items: list[MissedItem]) -> float:
        if not items:
            return 0.0
        return round(sum(i.confidence for i in items) / len(items), 3)
