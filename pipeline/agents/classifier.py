# pipeline/agents/classifier.py

"""
Cheap section classifier that gates the expensive extraction call.

Today every PageIndex node is fed through the full extraction prompt, even
sections that are pure context, legal boilerplate, or definitions. That wastes
tokens and pollutes the task list with junk. The SectionClassifier reads a
short snippet of each section, asks the LLM what kind of section it is, and
returns a typed ClassificationResult. The orchestrator can then skip
extraction on non-actionable sections.

Bias is intentional: the classifier prompt is told to lean toward ACTIONABLE
on ambiguity, and `should_extract` returns True for MIXED, ACTIONABLE, AND
for low-confidence calls of any type. False negatives (skipping a real
actionable section) are worse than false positives (running extraction on
context — the extractor will return []).
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from audit.logger import AuditLogger
from core.agent_runner import AgentRunner, InstructorError
from models.schemas import UnitInterval
from pipeline.llm_client import LLMClient


class SectionType(str, Enum):
    ACTIONABLE = "actionable"      # Contains real work items
    CONTEXT = "context"            # Background, overview, problem statement
    LEGAL = "legal"                # Terms, warranties, liability
    DEFINITIONS = "definitions"    # Glossary, acronyms
    SIGNATURE = "signature"        # Approvals, sign-off pages
    MIXED = "mixed"                # Looks like a mix — extract conservatively


class RawClassification(BaseModel):
    """Exactly what the classifier LLM returns — the Instructor response_model.

    The domain :class:`ClassificationResult` additionally carries ``node_id``
    (assigned by the agent, not produced by the model), so the LLM-facing schema
    is this narrower triple. ``confidence`` is a clamped UnitInterval so an
    out-of-range model value (e.g. 1.7) coerces into range instead of failing
    validation, matching ClassificationResult's CONF-1 invariant.
    """
    type: SectionType
    confidence: UnitInterval = Field(ge=0.0, le=1.0)
    reason: str = ""


class ClassificationResult(BaseModel):
    node_id: str
    type: SectionType
    # CONF-1: clamped into [0,1] before ge/le, so an out-of-range value coerces.
    confidence: UnitInterval = Field(ge=0.0, le=1.0)
    reason: str


CLASSIFIER_SYSTEM_PROMPT = (
    "You are a precise section classifier for Statement of Work (SOW) documents. "
    "Return ONLY a single valid JSON object. No explanation. No markdown fences."
)


CLASSIFIER_PROMPT_TEMPLATE = """You classify a single SOW section into one of:

- "actionable": contains concrete work items, deliverables, features to build, integrations, migrations, tests to write, or configurations to set up.
- "context": background, overview, project history, problem statement, business goals, executive summary.
- "legal": legal terms, payment terms, warranties, indemnity, confidentiality, liability, termination clauses.
- "definitions": glossary, acronyms, terminology definitions.
- "signature": signature lines, approval blocks, sign-off pages, contact tables.
- "mixed": clearly contains BOTH non-actionable content AND at least one work item.

═══ RULES ═══
- If the section describes WHAT to build / integrate / migrate / configure / test → "actionable".
- If you are unsure between actionable and one of the others → pick "actionable" or "mixed" (false positives are cheap, false negatives are expensive).
- Use "context" only when the section is purely descriptive with no work implied.
- Be especially careful with sections titled "Scope", "Requirements", "Approach", "Solution" — these are almost always actionable.

═══ OUTPUT FORMAT ═══
Return EXACTLY one JSON object with these fields:
{{
  "type": "actionable" | "context" | "legal" | "definitions" | "signature" | "mixed",
  "confidence": 0.0 to 1.0,
  "reason": "one short clause (max 20 words) explaining the call"
}}

Section title: {section_title}

Section snippet (first portion of the section):
{section_snippet}
"""


class SectionClassifier:
    """
    Lightweight classifier. Reads a snippet (default 4000 chars) of the
    section and returns a typed ClassificationResult. On any LLM error or
    parse failure it returns MIXED with confidence 0 so the caller still runs
    extraction (safe default).
    """

    def __init__(
        self,
        llm_client: LLMClient,
        audit_logger: AuditLogger,
        run_id: str,
        min_confidence: float = 0.7,
        max_section_chars: int = 4000,
    ):
        self.llm = llm_client
        # Route the single classifier LLM call through the AgentRunner seam
        # (behavior-preserving passthrough over the same LLMProvider).
        self.runner = AgentRunner(llm_client)
        self.audit = audit_logger
        self.run_id = run_id
        self.min_confidence = min_confidence
        self.max_section_chars = max_section_chars

    def classify(self, node: dict, section_text: str) -> ClassificationResult:
        node_id = node.get("node_id", "")
        title = node.get("title", "")

        snippet = (section_text or "").strip()
        if len(snippet) > self.max_section_chars:
            snippet = snippet[: self.max_section_chars]

        # Empty / trivially short sections get a deterministic MIXED so the
        # extractor runs and applies its own short-section gate.
        if len(snippet) < 30:
            return self._default_mixed(node_id, reason="Section too short to classify")

        prompt = CLASSIFIER_PROMPT_TEMPLATE.format(
            section_title=title,
            section_snippet=snippet,
        )

        # C-5: route through the Instructor-validated structured-output seam.
        # `raw` comes back as a fully-validated RawClassification — the section
        # type is a real SectionType and confidence is already clamped into
        # [0,1] by the model — so the manual dict/type/float parsing the regex
        # `complete_json` path needed is gone. Any structured-output failure
        # (call error, unparseable output, schema/enum violation instructor
        # could not satisfy) surfaces as a single InstructorError, which we
        # record and degrade to the safe MIXED default (extract anyway).
        try:
            raw = self.runner.complete_structured(
                prompt=prompt,
                response_model=RawClassification,
                system=CLASSIFIER_SYSTEM_PROMPT,
                agent_name="SectionClassifier",
                node_id=node_id,
            )
        except InstructorError as e:
            self.audit.log(
                run_id=self.run_id,
                agent="SectionClassifier",
                node_id=node_id,
                action="CLASSIFY_ERROR",
                detail=f"Structured output failed: {e}",
            )
            return self._default_mixed(node_id, reason=f"LLM error: {e}")

        result = ClassificationResult(
            node_id=node_id,
            type=raw.type,
            confidence=raw.confidence,
            reason=str(raw.reason or "")[:300],
        )
        self.audit.log(
            run_id=self.run_id,
            agent="SectionClassifier",
            node_id=node_id,
            action="CLASSIFIED",
            detail=f"type={result.type.value} confidence={result.confidence:.2f} reason={result.reason}",
        )
        return result

    def should_extract(self, result: ClassificationResult) -> bool:
        """
        True when extraction should still run.

        Extract when:
          - type is ACTIONABLE or MIXED (clearly has work), OR
          - confidence is below `min_confidence` (we're unsure — be safe).

        Skip when the model is BOTH confident AND says the section is one of
        the non-actionable kinds (context, legal, definitions, signature).
        """
        if result.type in (SectionType.ACTIONABLE, SectionType.MIXED):
            return True
        if result.confidence < self.min_confidence:
            return True
        return False

    # ─── Internals ────────────────────────────────────────────────────────

    def _default_mixed(self, node_id: str, reason: str) -> ClassificationResult:
        return ClassificationResult(
            node_id=node_id,
            type=SectionType.MIXED,
            confidence=0.0,
            reason=reason,
        )
