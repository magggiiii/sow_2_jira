# Coding Conventions

**Analysis Date:** 2026-05-11

## Naming Patterns

**Files:**
- Use `snake_case.py` for all Python modules: `pipeline/orchestrator.py`, `pipeline/observability.py`, `pipeline/llm_client.py`, `pipeline/llm_router.py`, `integrations/jira_client.py`, `integrations/jira_mcp_client.py`, `audit/logger.py`, `models/schemas.py`, `ui/server.py`, `config/settings.py`.
- Pytest tests under `tests/` follow `test_<area>.py`: `tests/test_routing.py`, `tests/test_phase2_runtime_reliability.py`, `tests/test_phase11_evals.py`, `tests/test_hierarchical_judge.py`.
- Standalone manual sanity scripts at repo root use the same `test_*.py` prefix but are NOT pytest suites: `test_jira_api.py`, `test_jira_mcp.py`, `test_discovery.py`, `test_settings.py`. Keep new automated tests inside `tests/` to avoid this ambiguity.

**Functions:**
- Use `snake_case` for all functions and methods, including pipeline helpers (`_build_or_load_tree` in `pipeline/orchestrator.py`), integration helpers (`_build_description`, `_build_labels`, `_resolve_issue_type` in `integrations/jira_client.py`), and server helpers (`_load_settings`, `_apply_settings_to_env_legacy`, `_extract_models_from_response` in `ui/server.py`).
- Prefix internal/private helpers with a leading underscore. Examples: `_is_non_retryable_llm_error`, `_is_cancelled_error`, `_sleep_with_cancel`, `_extract_headers` in `pipeline/llm_client.py`; `_validate_project`, `_create_task`, `_create_container` in `integrations/jira_client.py`.
- FastAPI route handlers in `ui/server.py` use verb-style names that describe the HTTP action: `get_tasks`, `start_processing`, `push_to_jira`, `get_provider_models`, `startup_event`.

**Variables:**
- Local variables use `snake_case`: `run_config`, `dedup_threshold`, `section_text`, `epic_cache`, `parent_key`, `issue_type`.
- Module-level mutable state in `ui/server.py` (e.g. `active_runs`, `active_orchestrators`, `MODEL_CACHE`) uses `snake_case` for instance-style state, `UPPER_SNAKE_CASE` for constants/caches.

**Constants:**
- Use `UPPER_SNAKE_CASE` for module-level constants:
  - `TREE_CACHE_PATH` in `pipeline/orchestrator.py`
  - `EXTRACTION_SYSTEM_PROMPT`, `EXTRACTION_PROMPT_TEMPLATE`, `HIERARCHY_CONTEXT` in `pipeline/agents/extraction.py`
  - `SETTINGS_PATH`, `DATA_DIR`, `UI_DIR` in `ui/server.py`
  - `SYNC_ENABLED`, `INSTANCE_ID`, `DEFAULT_JOB_NAME` in `pipeline/observability.py`
  - `DB_PATH` (class attribute) in `audit/logger.py`

**Types (classes / enums / Pydantic models):**
- Use `PascalCase` for all classes:
  - Enums: `TaskStatus`, `TaskFlag`, `LLMMode`, `JiraHierarchy` in `models/schemas.py`
  - Pydantic models: `RunConfig`, `RawTask`, `ManagedTask`, `ProviderConfig`, `SourceRef`, `JiraPushResult` in `models/schemas.py`; `ProcessingStatus`, `ModelDiscoveryRequest` in `ui/server.py`
  - Service classes: `PipelineOrchestrator`, `LLMClient`, `JiraClient`, `JiraMCPClient`, `TaskExtractionAgent`, `AuditLogger`, `SettingsManager`, `TelemetryEmitter`, `HierarchicalJudge`
  - Dataclasses: `RetryHint` in `pipeline/llm_client.py`

## Code Style

**Formatting:**
- Tool: Not detected. No `pyproject.toml`, `setup.cfg`, `ruff.toml`, `.flake8`, `black`, or other formatter config files exist at the repository root.
- Follow the style of the file you are editing. Existing files use:
  - 4-space indentation
  - Blank lines separating logical blocks inside functions
  - Section dividers using box-drawing characters (e.g. `# ─── LLM Config ───` in `models/schemas.py`, `# ─── ARGUS BACKBONE ───` in `pipeline/observability.py`) for grouping related declarations

**Linting:**
- Tool: Not detected. No lint configuration present.
- Implicit rules to follow:
  - Prefer explicit typing on function signatures where it improves clarity (`def push_tasks(self, tasks: list[ManagedTask]) -> list[JiraPushResult]` in `integrations/jira_client.py`).
  - Avoid bare `except:` — always catch a specific exception class or `Exception` and log/record context.
  - Do not introduce a formatter/linter without checking with the maintainer; the project intentionally has no enforced toolchain today.

## Import Organization

**Order (observed pattern):**
1. Standard library imports (`os`, `json`, `re`, `time`, `pathlib`, `typing`, `datetime`, `asyncio`, `threading`)
2. Third-party imports (`fastapi`, `pydantic`, `litellm`, `jira`, `loguru`, `httpx`, `opentelemetry.*`)
3. First-party imports from project packages (`models.schemas`, `pipeline.*`, `integrations.*`, `audit.logger`, `config.settings`)

Reference layouts: `pipeline/llm_client.py` (lines 1-22), `ui/server.py` (lines 1-35), `integrations/jira_client.py` (lines 1-9).

**Path Aliases:**
- No aliases. Use direct package paths: `from models.schemas import RunConfig, LLMMode`, `from pipeline.orchestrator import PipelineOrchestrator`.
- Scripts that may be executed directly insert the project root into `sys.path` before importing first-party modules:
  - `ui/server.py` uses `sys.path.insert(0, str(UI_DIR.parent))`
  - `tests/test_phase2_runtime_reliability.py` uses `sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))`
- Prefer importing concrete symbols from concrete modules (e.g. `from audit.logger import AuditLogger`). `pipeline/agents/__init__.py` exists but is not used as a central export surface — import agents directly from their module files.

## Error Handling

**At I/O and network boundaries:** wrap calls in `try`/`except`, log the failure, and return a structured fallback or raise a domain-specific error.

- HTTP/SDK boundaries (Jira): catch broad `Exception`, log via `loguru`, and raise `ValueError` with actionable guidance — see `_validate_project` in `integrations/jira_client.py` (raises `ValueError` with the original error and remediation hint).
- LLM retry semantics: classify errors before deciding to retry. `pipeline/llm_client.py` exposes `_is_non_retryable_llm_error`, `is_retryable_remote_error`, `extract_retry_hint`, and raises `RuntimeError("retry budget exhausted")` when the budget is consumed.
- Cancellation: surface user-initiated cancellation as a `RuntimeError("... cancelled by user")` rather than swallowing it (see `_sleep_with_cancel` in `pipeline/llm_client.py`).

**Terminal failures:** raise a domain exception:
- `RuntimeError` for system-level conditions that should halt the run (`pipeline/llm_client.py` retry budget exhaustion, `config/settings.py` corrupted settings — verified by `test_corrupted_settings_throws_error` in `test_settings.py`).
- `ValueError` for invalid domain inputs/configuration (Jira project not found in `integrations/jira_client.py`).

**Recoverable agent failures:** record and continue. Return empty lists, default objects, or unchanged inputs rather than propagating:
- `pipeline/agents/extraction.py` returns `[]` when section text is below threshold or parsing fails.
- `pipeline/agents/deduplication.py` and `pipeline/agents/gap_recovery.py` follow the same pattern — log and return unchanged data so the pipeline keeps moving.
- Integrations: `integrations/jira_client.py` retries `_create_task` without `parent_key` when the parent linkage causes failure.

**API layer:** translate backend exceptions to HTTP errors in `ui/server.py`:
- Use `HTTPException(status_code=..., detail=...)` for client-facing errors.
- Background tasks (`run_pipeline_task`, `run_push_task`) wrap the orchestrator call in `try/except`, set the run's `ProcessingStatus.status = "error"`, and store the message — do not let exceptions escape into FastAPI background workers.

## Logging

**Framework:** `loguru` is the project standard. Import `logger` from `pipeline.observability`, never instantiate a new `loguru` logger or use the stdlib `logging` directly for application logs.

```python
from pipeline.observability import logger
```

**Contextual binding:** use `logger.contextualize(agent=..., run_id=...)` to attach correlation fields to a block of log calls. Reference patterns:

```python
# integrations/jira_client.py
with logger.contextualize(agent="JiraClient", run_id=self.run_id):
    logger.info(f"Initializing JiraClient for project {self.project_key}")
```

Apply the same pattern at the boundaries of any new agent or integration so traces in Grafana/Bifrost stay correlated.

**Severity guidance:**
- `logger.info` — lifecycle checkpoints (run start/finish, pipeline step transitions, successful pushes). Used liberally in `pipeline/orchestrator.py` and `main.py`.
- `logger.warning` — recoverable fallbacks (Jira issue type fallback in `_resolve_issue_type`, missing parent links, observability sync skipped).
- `logger.error` — terminal failures, settings load errors, network/API exceptions that abort the current request.

**Tracing:** spans are created via the `trace_span` decorator and `tracer` context manager imported from `pipeline.observability` (see `@trace_span("JIRA_PUSH_ALL", agent="JiraClient")` in `integrations/jira_client.py`). Span attributes (`agent`, `run_id`) align with log context for cross-correlation.

**Stdlib `logging` interop:** allowed for filtering noisy framework logs (e.g. the uvicorn access filter in `ui/server.py` strips `/api/status` polling). Do not use it for application messages.

**Audit log:** orthogonal to loguru. Use `AuditLogger.log(...)` from `audit/logger.py` whenever an agent or integration makes a tracked decision (extraction skipped, task merged, Jira issue created). It persists to `data/audit.db` and is consumed by the review UI.

## Comments

**When to comment:**
- Operational intent above non-obvious workflow steps (see `# Pre-flight validation` and `# Next-Gen Fix: Always pass parent_key ...` in `integrations/jira_client.py`).
- Section dividers using box drawing characters (e.g. `# ─── Enums ───`) to visually group related declarations in long files like `models/schemas.py` and `pipeline/observability.py`.
- Inline rationale for retry/cancellation logic where the behavior is timing-sensitive (`pipeline/llm_client.py`).

**Docstrings (triple-quoted strings):**
- Required on public classes that represent a domain concept: `RawTask`, `ManagedTask`, `AuditLogger`.
- Required on key methods that encapsulate a pipeline step or non-trivial behavior: `JiraClient.push_tasks`, `JiraClient._validate_project`, `TaskExtractionAgent.extract`, `LLMClient.complete`.
- Short helper functions and trivial getters/setters do not require docstrings.
- Prompt templates (e.g. `EXTRACTION_SYSTEM_PROMPT`, `EXTRACTION_PROMPT_TEMPLATE`) live as module-level string constants with their own structural commentary inside the prompt body — do not duplicate that content in a docstring.

**Style:**
- Plain English, present tense, focused on *why* the code does something or what externally-observable behavior it enforces. Avoid restating the code.

## Function Design

**Size:**
- Orchestrator-style functions can be long and step-oriented when each block is a clear pipeline stage (`PipelineOrchestrator.run` in `pipeline/orchestrator.py`, `LLMClient.complete` in `pipeline/llm_client.py`). Use blank lines and inline comments to keep them readable.
- Helper methods are kept focused on one responsibility: `_build_labels`, `_resolve_issue_type`, `_build_description` in `integrations/jira_client.py`; `_extract_models_from_response` in `ui/server.py`.

**Parameters:**
- Type the parameters and return values on public methods (`def push_tasks(self, tasks: list[ManagedTask]) -> list[JiraPushResult]`).
- Use explicit default values to encode behavior toggles: `confidence_threshold: float = 0.6`, `max_section_chars: int = 16000` (`pipeline/agents/extraction.py`); `max_nodes: int = ...` in `ui/server.py`; optional session IDs in API helpers defaulting to `None`.
- Pass cross-cutting dependencies (audit logger, run_id, stop_event, status_callback) through the constructor or method args rather than reading them from globals.

**Return values:**
- Prefer typed structured returns over raw tuples: return `JiraPushResult`, `RawTask`, `ManagedTask`, or other Pydantic models rather than ad-hoc dicts.
- For recoverable failures, return the same shape (e.g. an empty `list[RawTask]`) rather than `None` so callers do not need extra null checks.

## Module Design

**Exports:**
- No `__all__` declarations. Modules expose their public surface implicitly — import the specific symbols you need from the concrete module path.
- `pipeline/agents/__init__.py` exists as a marker; importers should still target `pipeline.agents.extraction`, `pipeline.agents.deduplication`, etc. directly.

**Domain separation:**
- `models/` — shared Pydantic schemas and enums. No I/O, no side effects.
- `pipeline/` — pipeline core, agents (`pipeline/agents/`), LLM client/router, observability, telemetry, evals (`pipeline/evals/`).
- `integrations/` — outbound system clients (Jira REST and MCP).
- `audit/` — persistence of audit events (SQLite).
- `ui/` — FastAPI server, static assets, frontend JS/CSS.
- `config/` — settings management and provider registry.
- `pageindex/` — vendored PageIndex module for PDF tree indexing.
- `scripts/` — operational scripts (e.g. `scripts/run_eval_dataset.py`).
- `tests/` — pytest test suite. Root-level `test_*.py` are *not* part of this suite; treat them as manual scripts.

**Barrel files:** not used. Importing from the concrete module file is the standard.

---

*Convention analysis: 2026-05-11*
