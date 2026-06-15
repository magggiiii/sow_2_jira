# models/schemas.py

from __future__ import annotations
import contextvars
from enum import Enum
from typing import Annotated, Optional, Union
from uuid import UUID, uuid4
from pydantic import BaseModel, BeforeValidator, Field, field_validator
import datetime


# ─── Confidence / score bounding (CONF-1, audit data_model "bound confidence") ─

def clamp_unit_interval(value):
    """
    Coerce a numeric confidence/score into the closed unit interval [0.0, 1.0].

    LLM output regularly emits an out-of-range confidence (e.g. 1.5 or -0.3).
    Rather than reject the whole record (losing otherwise-usable data) or store
    the raw value (which poisons every downstream ``>= floor`` comparison and
    ``:.2f`` render), we clamp: ``>1 -> 1.0``, ``<0 -> 0.0``, in-range unchanged.

    This mirrors the ``_NormalizedStrEnum`` philosophy of absorbing
    dirty-but-numeric LLM output. ``None`` passes through untouched so it can be
    composed with Optional fields. Genuinely non-numeric input is handed back
    unchanged for Pydantic's normal coercion/validation to reject.
    """
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        # Let Pydantic's normal float coercion/validation handle non-numerics.
        return value
    if f < 0.0:
        return 0.0
    if f > 1.0:
        return 1.0
    return f


# Reusable Annotated type for confidence/score fields. The BeforeValidator
# clamps an out-of-range LLM value into [0,1] so it stays valid instead of being
# rejected. Bounds are enforced by this clamp ALONE — fields deliberately do NOT
# add ``Field(ge=..., le=...)``: the clamp already guarantees the range, and
# emitting ``minimum``/``maximum`` into the JSON schema breaks providers' strict
# structured-output modes (Anthropic via OpenRouter rejects number bounds).
UnitInterval = Annotated[float, BeforeValidator(clamp_unit_interval)]


# ─── LLM Config ────────────────────────────────────────────────────────────────

class ProviderConfig(BaseModel):
    provider: str
    model: str
    api_key: str = ""
    api_base: str = ""
    azure_api_version: str = ""
    azure_deployment_name: str = ""


current_provider_config: contextvars.ContextVar[Optional[ProviderConfig]] = contextvars.ContextVar("current_provider_config", default=None)


# ─── Enums ────────────────────────────────────────────────────────────────────

class _NormalizedStrEnum(str, Enum):
    """
    Base for closed-set string fields (H-23 domain-model hardening).

    A `(str, Enum)` member already compares equal to its raw value, so existing
    `x == "merge"` style comparisons across the codebase keep working unchanged.
    On top of that this base adds two things:

    1. `__str__` returns the *value*, so text rendered into Jira descriptions /
       audit logs stays `"merge"` rather than regressing to `"DedupDecisionType.MERGE"`.
    2. `_missing_` coerces dirty-but-known inputs (case, surrounding whitespace,
       and separator drift — spaces/hyphens -> underscore) plus an optional
       per-enum `_aliases()` map of normalized-string -> member. Genuinely
       unknown values return None so Pydantic raises a clear validation error
       (fail loudly on unknown, coerce on dirty-but-known).

    Dependency-free: stdlib `enum` + `str` only.
    """

    def __str__(self) -> str:  # pragma: no cover - exercised via f-strings
        return str(self.value)

    @classmethod
    def _aliases(cls) -> dict:
        """Override per-enum to map normalized aliases -> member. Default: none.

        Implemented as a classmethod (not a class attribute) so it does not
        accidentally become an enum member.
        """
        return {}

    @classmethod
    def _missing_(cls, value):
        if not isinstance(value, str):
            return None
        norm = value.strip().lower().replace("-", "_").replace(" ", "_")
        while "__" in norm:
            norm = norm.replace("__", "_")
        if not norm:
            return None
        for member in cls:
            if member.value == norm:
                return member
        return cls._aliases().get(norm)


class TaskStatus(str, Enum):
    OPEN = "OPEN"            # Newly extracted, may span into next section
    CLOSED = "CLOSED"        # Fully extracted, ready for dedup/review
    MERGED = "MERGED"        # Merged into another task during dedup
    REJECTED = "REJECTED"    # Human rejected in UI
    APPROVED = "APPROVED"    # Human approved in UI
    PUSHED = "PUSHED"        # Successfully pushed to Jira


class TaskFlag(str, Enum):
    NO_ACCEPTANCE_CRITERIA = "NO_ACCEPTANCE_CRITERIA"
    AMBIGUOUS_SCOPE = "AMBIGUOUS_SCOPE"
    INCOMPLETE = "INCOMPLETE"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    NO_MOCKUP = "NO_MOCKUP"            # Informational — mockup field is absent
    POTENTIAL_DUPLICATE = "POTENTIAL_DUPLICATE"
    GAP_RECOVERED = "GAP_RECOVERED"   # Was found by Gap Recovery Agent


class LLMMode(str, Enum):
    API = "api"          # Maxim Bifrost → z.ai GLM
    LOCAL = "local"      # Maxim Bifrost → Ollama local
    CUSTOM = "custom"    # Any litellm provider (e.g., anthropic, gpt, groq)


class JiraHierarchy(str, Enum):
    """
    Output shape for Jira push. Container grouping is structural — driven by
    SourceRef.parent_id (and parent_chain for multi-level rollup) rather than
    by section_title strings. STORY_SUBTASK can honor a real 3-level structure
    when the PageIndex tree has at least two levels of depth.
    """
    FLAT = "flat"                    # All Tasks, no parent
    EPIC_TASK = "epic_task"          # SOW sections → Epics, items → Tasks
    STORY_SUBTASK = "story_subtask"  # SOW sections → Stories, items → Sub-tasks


class AcceptanceCriterionType(str, Enum):
    FUNCTIONAL = "functional"
    NONFUNCTIONAL = "nonfunctional"
    SECURITY = "security"
    PERFORMANCE = "performance"
    USABILITY = "usability"


class VerifiedBy(_NormalizedStrEnum):
    """How an acceptance criterion is verified. Coerces dirty LLM casing."""
    TEST = "test"
    REVIEW = "review"
    DEMO = "demo"
    INSPECTION = "inspection"


class DependencyKind(_NormalizedStrEnum):
    """Relationship a TaskDependency expresses to its target_ref."""
    BLOCKS = "blocks"
    RELATES_TO = "relates_to"
    DUPLICATES = "duplicates"


class DedupDecisionType(_NormalizedStrEnum):
    """Verdict the dedup LLM returns for a candidate task pair.

    Member values are the EXACT lowercase strings the dedup agent compares
    against in pipeline/agents/deduplication.py (`decision in ("merge",
    "keep_first")`, `== "keep_second"`, `DEDUP_{decision.upper()}`), so
    str-enum equality keeps every existing comparison working.
    """
    MERGE = "merge"
    KEEP_BOTH = "keep_both"
    KEEP_FIRST = "keep_first"
    KEEP_SECOND = "keep_second"

    @classmethod
    def _aliases(cls) -> dict:
        # Dirty-but-known forms the base normalizer can't reach on its own.
        # `_missing_` already collapses case, whitespace, and hyphen/space ->
        # underscore (so "KEEP_BOTH", "keep both", "keep-both" coerce), but a
        # no-separator blob like "keepboth" or a verbose "keepallboth" does not.
        # These map the post-normalization string -> member.
        return {
            "keepboth": cls.KEEP_BOTH,
            "keep_all_both": cls.KEEP_BOTH,
            "both": cls.KEEP_BOTH,
            "keepfirst": cls.KEEP_FIRST,
            "first": cls.KEEP_FIRST,
            "keepsecond": cls.KEEP_SECOND,
            "second": cls.KEEP_SECOND,
            "duplicate": cls.MERGE,
        }


# ─── Acceptance Criterion & Dependency ────────────────────────────────────────

class AcceptanceCriterion(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4())[:8])
    condition: str                                   # The testable statement
    type: AcceptanceCriterionType = AcceptanceCriterionType.FUNCTIONAL
    verified_by: VerifiedBy = VerifiedBy.TEST        # test | review | demo | inspection


class TaskDependency(BaseModel):
    target_ref: str                                  # Sibling task title or external ref
    reason: str                                      # One-line why
    kind: DependencyKind = DependencyKind.BLOCKS     # blocks | relates_to | duplicates


def normalize_acceptance_criteria(
    items: Optional[list],
) -> Optional[list[AcceptanceCriterion]]:
    """
    Convert a mixed list of strings, dicts, and AcceptanceCriterion objects
    into a uniform list[AcceptanceCriterion]. This is the backward-compat seam:
    legacy LLM output and old `pipeline_output.json` checkpoints used plain
    strings; new output is structured.

    Strings become AcceptanceCriterion(condition=<string>) with default
    type=FUNCTIONAL and verified_by="test". Dicts get parsed via Pydantic.
    None/empty returns None.
    """
    if not items:
        return None
    out: list[AcceptanceCriterion] = []
    for item in items:
        if isinstance(item, AcceptanceCriterion):
            out.append(item)
        elif isinstance(item, str):
            condition = item.strip()
            if not condition:
                continue
            out.append(AcceptanceCriterion(condition=condition))
        elif isinstance(item, dict):
            try:
                out.append(AcceptanceCriterion(**item))
            except Exception:
                # If a dict can't be parsed, fall back to stringifying its
                # condition field if present; otherwise skip.
                cond = item.get("condition") if isinstance(item, dict) else None
                if cond:
                    out.append(AcceptanceCriterion(condition=str(cond)))
        # Anything else (None, numbers, etc.) is silently dropped.
    return out or None


# ─── Source Reference ─────────────────────────────────────────────────────────

class SourceRef(BaseModel):
    node_id: str                    # PageIndex node ID
    section_title: str              # Section heading
    page_start: int                 # 1-indexed
    page_end: int                   # 1-indexed
    snippet: str = ""               # Short verbatim snippet from SOW (max 300 chars)
    parent_id: Optional[str] = None             # Immediate parent node_id (None for root sections)
    parent_chain: list[str] = Field(default_factory=list)  # Ancestor node_ids, root first
    depth: int = 0                              # 0 for root; matches PageIndex tree depth


# ─── Raw Extraction Output (from LLM) ────────────────────────────────────────

class RawTask(BaseModel):
    """Exactly what the Task Extraction Agent LLM returns per task."""
    title: str
    short_description: str
    # Accepts both the new structured form (AcceptanceCriterion/dict) and
    # legacy plain strings for backward compat. ExtractionAgent normalizes
    # this to list[AcceptanceCriterion] before handing the task downstream.
    acceptance_criteria: Optional[list[Union[AcceptanceCriterion, str]]] = None
    use_case: Optional[str] = None
    considerations_constraints: Optional[list[str]] = None
    deliverables: Optional[list[str]] = None
    mockup_prototype: Optional[str] = None
    # CONF-1: clamped into [0,1] by the UnitInterval BeforeValidator — an
    # out-of-range LLM value is coerced, not rejected. No Field(ge/le): the
    # clamp already bounds it, and emitting minimum/maximum into the JSON schema
    # breaks Anthropic's strict structured-output mode.
    confidence: UnitInterval
    flags: list[str] = Field(default_factory=list)
    continues_to_next: bool = False
    dependencies: list[TaskDependency] = Field(default_factory=list)


# ─── Managed Task (after State Agent assigns ID) ──────────────────────────────

class ManagedTask(BaseModel):
    """A task with a stable ID, tracked through the pipeline."""
    id: UUID = Field(default_factory=uuid4)
    title: str
    short_description: str
    # Always structured downstream of TaskStateAgent. The field validator
    # below normalizes legacy string entries so old checkpoints still load.
    acceptance_criteria: Optional[list[AcceptanceCriterion]] = None
    use_case: Optional[str] = None
    considerations_constraints: Optional[list[str]] = None
    deliverables: Optional[list[str]] = None
    mockup_prototype: Optional[str] = None
    confidence: UnitInterval  # CONF-1: clamped into [0,1]
    flags: list[TaskFlag] = Field(default_factory=list)
    continues_to_next: bool = False
    status: TaskStatus = TaskStatus.OPEN
    jira_issue_key: Optional[str] = None  # Set once pushed; presence makes re-push idempotent (skip create)
    source_refs: list[SourceRef] = Field(default_factory=list)  # Can span multiple nodes
    merged_from: list[UUID] = Field(default_factory=list)       # IDs merged into this task
    dependencies: list[TaskDependency] = Field(default_factory=list)
    created_at: datetime.datetime = Field(default_factory=datetime.datetime.utcnow)
    updated_at: datetime.datetime = Field(default_factory=datetime.datetime.utcnow)

    @field_validator("acceptance_criteria", mode="before")
    @classmethod
    def _normalize_ac(cls, v):
        """
        Lets legacy `pipeline_output.json` files with plain string ACs
        (e.g. `["[ ] foo"]`) deserialize cleanly. Returns the structured
        form or None.
        """
        if v is None:
            return None
        if isinstance(v, list):
            return normalize_acceptance_criteria(v)
        return v


# ─── Run Configuration (from startup wizard) ─────────────────────────────────

class RunConfig(BaseModel):
    sow_pdf_path: str
    llm_mode: LLMMode
    jira_hierarchy: JiraHierarchy
    jira_project_key: str
    skip_indexing: bool = False
    max_nodes: int = 200
    run_id: str = Field(default_factory=lambda: str(uuid4())[:8])
    provider_config: Optional[ProviderConfig] = None


# ─── Audit Log Entry ──────────────────────────────────────────────────────────

class AuditEntry(BaseModel):
    run_id: str
    timestamp: datetime.datetime = Field(default_factory=datetime.datetime.utcnow)
    agent: str                  # e.g. "ExtractionAgent", "StateAgent"
    node_id: Optional[str]
    action: str                 # e.g. "EXTRACTED", "MERGED", "CLOSED", "GAP_RECOVERED"
    task_id: Optional[str]
    detail: str                 # Human-readable description
    llm_tokens_used: int = 0
    llm_model: str = ""


# ─── Dedup Decision (from LLM) ────────────────────────────────────────────────

class DedupDecision(BaseModel):
    task_id_a: str
    task_id_b: str
    decision: DedupDecisionType   # "merge" | "keep_both" | "keep_first" | "keep_second"
    reason: str


# ─── Jira Push Result ────────────────────────────────────────────────────────

class JiraPushResult(BaseModel):
    task_id: UUID
    success: bool
    jira_issue_key: Optional[str] = None  # e.g. "PROJ-42"
    jira_issue_url: Optional[str] = None
    error: Optional[str] = None
    warning: Optional[str] = None
    # JIRA-4 (audit H-11): set True when an Epic/Story container create failed
    # and this child was created flat (parentless) instead of under its intended
    # container. The push still succeeds, but the requested hierarchy was not
    # honored, so the caller/UI can surface it. `hierarchy_degraded_reason`
    # carries a short human-readable explanation when degraded.
    hierarchy_degraded: bool = False
    hierarchy_degraded_reason: Optional[str] = None