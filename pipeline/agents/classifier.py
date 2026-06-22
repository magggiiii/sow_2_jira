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

from pydantic import BaseModel

from audit.logger import AuditLogger
from core.agent_runner import AgentRunner, InstructorError
from core.agent_spec import AgentSpec
from models.schemas import UnitInterval
from pipeline.llm_client import LLMClient
from prompts import registry


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
    # CONF-1: clamped by the UnitInterval BeforeValidator; no Field ge/le so the
    # emitted schema has no minimum/maximum (Anthropic strict structured output).
    confidence: UnitInterval
    reason: str = ""


class ClassificationResult(BaseModel):
    node_id: str
    type: SectionType
    # CONF-1: clamped into [0,1] by the UnitInterval BeforeValidator (no ge/le).
    confidence: UnitInterval
    reason: str


CLASSIFIER_SYSTEM_PROMPT = registry.load("classifier.system.v1")


CLASSIFIER_PROMPT_TEMPLATE = registry.load("classifier.user.v1")


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
        # Route the single classifier LLM call through the AgentRunner's
        # Instructor-validated structured-output seam (complete_structured).
        self.runner = AgentRunner(llm_client)
        # STEP 3.6b: declare the structured call as an AgentSpec; run via the runner.
        self.spec = AgentSpec(
            name="SectionClassifier",
            system_prompt=CLASSIFIER_SYSTEM_PROMPT,
            prompt_template=CLASSIFIER_PROMPT_TEMPLATE,
            response_model=RawClassification,
        )
        self.audit = audit_logger
        self.run_id = run_id
        self.min_confidence = min_confidence
        self.max_section_chars = max_section_chars

    def classify(self, node: dict, section_text: str) -> ClassificationResult:
        node_id = node.get("node_id", "")
        title = node.get("title", "")

        snippet = (section_text or "").strip()
        if len(snippet) > self.max_section_chars:
            # A4: surface truncation rather than silently clipping the snippet.
            self.audit.log(
                run_id=self.run_id,
                agent="SectionClassifier",
                node_id=node_id,
                action="SECTION_TRUNCATED",
                detail=(
                    f"Section '{title}' snippet truncated {len(snippet)} → "
                    f"{self.max_section_chars} chars for classification"
                ),
            )
            snippet = snippet[: self.max_section_chars]

        # Empty / trivially short sections get a deterministic MIXED so the
        # extractor runs and applies its own short-section gate.
        if len(snippet) < 30:
            return self._default_mixed(node_id, reason="Section too short to classify")

        payload = {
            "section_title": title,
            "section_snippet": snippet,
        }

        # C-5: route through the Instructor-validated structured-output seam.
        # `raw` comes back as a fully-validated RawClassification — the section
        # type is a real SectionType and confidence is already clamped into
        # [0,1] by the model — so the manual dict/type/float parsing the regex
        # `complete_json` path needed is gone. Any structured-output failure
        # (call error, unparseable output, schema/enum violation instructor
        # could not satisfy) surfaces as a single InstructorError, which we
        # record and degrade to the safe MIXED default (extract anyway).
        try:
            raw = self.runner.run_structured(self.spec, payload, node_id=node_id)
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
