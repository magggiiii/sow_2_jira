# models/schemas.py

from __future__ import annotations
import contextvars
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4
from pydantic import BaseModel, Field
import datetime


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
    FLAT = "flat"                    # All Tasks, no parent
    EPIC_TASK = "epic_task"          # SOW sections → Epics, items → Tasks
    STORY_SUBTASK = "story_subtask"  # SOW sections → Stories, items → Sub-tasks


# ─── Source Reference ─────────────────────────────────────────────────────────

class SourceRef(BaseModel):
    node_id: str                    # PageIndex node ID
    section_title: str              # Section heading
    page_start: int                 # 1-indexed
    page_end: int                   # 1-indexed
    snippet: str = ""               # Short verbatim snippet from SOW (max 300 chars)


# ─── Raw Extraction Output (from LLM) ────────────────────────────────────────

class RawTask(BaseModel):
    """Exactly what the Task Extraction Agent LLM returns per task."""
    title: str
    short_description: str
    acceptance_criteria: Optional[list[str]] = None
    use_case: Optional[str] = None
    considerations_constraints: Optional[list[str]] = None
    deliverables: Optional[list[str]] = None
    mockup_prototype: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0)
    flags: list[str] = Field(default_factory=list)
    continues_to_next: bool = False


# ─── Managed Task (after State Agent assigns ID) ──────────────────────────────

class ManagedTask(BaseModel):
    """A task with a stable ID, tracked through the pipeline."""
    id: UUID = Field(default_factory=uuid4)
    title: str
    short_description: str
    acceptance_criteria: Optional[list[str]] = None
    use_case: Optional[str] = None
    considerations_constraints: Optional[list[str]] = None
    deliverables: Optional[list[str]] = None
    mockup_prototype: Optional[str] = None
    confidence: float
    flags: list[TaskFlag] = Field(default_factory=list)
    continues_to_next: bool = False
    status: TaskStatus = TaskStatus.OPEN
    source_refs: list[SourceRef] = Field(default_factory=list)  # Can span multiple nodes
    merged_from: list[UUID] = Field(default_factory=list)       # IDs merged into this task
    created_at: datetime.datetime = Field(default_factory=datetime.datetime.utcnow)
    updated_at: datetime.datetime = Field(default_factory=datetime.datetime.utcnow)


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
    decision: str   # "merge" | "keep_both" | "keep_first" | "keep_second"
    reason: str


# ─── Jira Push Result ────────────────────────────────────────────────────────

class JiraPushResult(BaseModel):
    task_id: UUID
    success: bool
    jira_issue_key: Optional[str] = None  # e.g. "PROJ-42"
    jira_issue_url: Optional[str] = None
    error: Optional[str] = None
    warning: Optional[str] = None