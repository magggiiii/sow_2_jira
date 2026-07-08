# Architecture

**Analysis Date:** 2026-05-11

## Pattern Overview

**Overall:** Single-engine pipeline with dual execution surfaces (CLI + Web API), coordinated by a central orchestrator with explicit step boundaries and Pydantic-typed contracts between layers.

**Key Characteristics:**
- `PipelineOrchestrator` in `pipeline/orchestrator.py` is the single extraction engine. It is invoked identically by the CLI entry (`main.py`) and the FastAPI background runner (`ui/server.py`). There is exactly one pipeline implementation; both surfaces share it.
- Domain contracts are strongly typed Pydantic v2 models in `models/schemas.py` (`RunConfig`, `RawTask`, `ManagedTask`, `TaskStatus`, `JiraPushResult`, etc.) and are passed across every layer instead of ad-hoc dicts.
- Cross-cutting infrastructure (LLM routing, observability, audit logging) is exposed through narrow abstractions (`LLMClient`, `configure_litellm_for_mode`, `AuditLogger`) consumed uniformly by the agents and integrations.
- Pipeline state is checkpointed to durable JSON under `data/sessions/<run_id>/` so a run can be inspected post-hoc and the UI can rehydrate task review state without re-running the LLM steps.

## Layers

**API / UI Layer:**
- Purpose: Accept SOW uploads, drive runs, surface status, support task review, and trigger Jira push.
- Location: `main.py`, `ui/server.py`, `ui/index.html`, `ui/app.js`, `ui/styles.css`
- Contains: FastAPI app object, REST endpoints under `/api/*`, static asset mount, CLI wizard prompts, encrypted settings persistence helpers.
- Depends on: `models/schemas.py`, `pipeline/orchestrator.py`, `integrations/jira_client.py`, `integrations/jira_mcp_client.py`, `audit/logger.py`, `pipeline/llm_router.py`, `pipeline/observability.py`.
- Used by: End users (browser or terminal), Uvicorn/Gunicorn runtimes.

**Orchestration Layer:**
- Purpose: Sequence the extraction lifecycle — index, extract, manage state, dedupe, recover gaps, persist.
- Location: `pipeline/orchestrator.py`
- Contains: `PipelineOrchestrator.run()`, step-level status callbacks, checkpointing to `data/sessions/<run_id>/pipeline_output.json`.
- Depends on: `pipeline/indexer.py`, `pipeline/coverage.py`, `pipeline/llm_client.py`, `pipeline/agents/*.py`, `pipeline/telemetry.py`, `audit/logger.py`.
- Used by: `main.py`, `ui/server.py`.

**Indexing Layer:**
- Purpose: Convert source PDFs into a flat node list suitable for agentic extraction.
- Location: `pipeline/indexer.py`, `pipeline/parser.py`, and the vendored PageIndex module at `pageindex/page_index.py`, `pageindex/page_index_md.py`, `pageindex/utils.py`, `pageindex/config.yaml`.
- Contains: `DocumentIndexer` wrapper around vendored PageIndex tree building, OpenDataLoader-based parser path, tree caching to `data/document_tree.json`.
- Depends on: `pipeline/llm_router.py`, `pipeline/observability.py`.
- Used by: `pipeline/orchestrator.py`.

**Agents Layer:**
- Purpose: Transform indexed nodes into reviewed `ManagedTask` instances.
- Location: `pipeline/agents/extraction.py`, `pipeline/agents/state.py`, `pipeline/agents/deduplication.py`, `pipeline/agents/gap_recovery.py`.
- Contains: LLM-driven task extraction with confidence thresholds, deterministic state lifecycle transitions, vector+LLM deduplication, uncovered-node recovery sweep.
- Depends on: `pipeline/llm_client.py`, `models/schemas.py`, `audit/logger.py`.
- Used by: `pipeline/orchestrator.py`.

**Integrations Layer:**
- Purpose: Push approved tasks to Jira via REST or remote MCP.
- Location: `integrations/jira_client.py`, `integrations/jira_mcp_client.py`.
- Contains: Direct Jira SDK client (`jira` package), MCP-based Atlassian client, hierarchy/parent linkage logic, label resolution.
- Depends on: `models/schemas.py`, environment credentials, `audit/logger.py`.
- Used by: `ui/server.py` push routes, standalone ad-hoc scripts (`test_jira_api.py`, `test_jira_mcp.py`).

**Observability & Audit Layer:**
- Purpose: Logging, tracing, telemetry buffering, and structured audit persistence.
- Location: `pipeline/observability.py`, `pipeline/telemetry.py`, `audit/logger.py`.
- Contains: Loguru sinks, OpenTelemetry tracer setup, OTLP export to Bifrost/Tempo, Loki event emission, telemetry queue spooling to `data/telemetry_queue.jsonl`, SQLite `AuditLogger` writing to `data/audit.db`.
- Depends on: `requests`, `opentelemetry-*`, `loguru`, env config.
- Used by: All pipeline modules and integrations.

**Evaluation Layer:**
- Purpose: Score pipeline outputs against golden datasets using LLM judges.
- Location: `pipeline/evals/judges.py`, `models/eval_schemas.py`, `scripts/run_eval_dataset.py`, `scripts/seed_*_dataset.py`, `infra/admin/evaluator/`.
- Contains: Judge prompts/scoring, hierarchical-eval dataset seeders, admin evaluator service.
- Depends on: `pipeline/llm_client.py`, `models/schemas.py`.
- Used by: Operators running eval scripts, admin compose stack.

## Data Flow

**SOW → Jira Pipeline:**

1. User uploads a PDF via `POST /api/upload` (`ui/server.py`) or supplies a path to `main.py`.
2. `ui/server.py` registers an entry in the in-memory `active_runs` map and constructs a `RunConfig` (`models/schemas.py`).
3. A background task instantiates `PipelineOrchestrator` (held in `active_orchestrators`) and calls `run()`.
4. Orchestrator builds or loads the PageIndex tree via `DocumentIndexer` (`pipeline/indexer.py`), caching to `data/document_tree.json`.
5. Extraction agent (`pipeline/agents/extraction.py`) emits `RawTask` items per node above the confidence threshold.
6. State agent (`pipeline/agents/state.py`) promotes raw tasks into `ManagedTask` records and applies `TaskStatus` transitions.
7. Deduplication agent (`pipeline/agents/deduplication.py`) merges semantically similar tasks.
8. Gap-recovery agent (`pipeline/agents/gap_recovery.py`) revisits uncovered nodes for missed coverage.
9. Orchestrator persists final state to `data/sessions/<run_id>/pipeline_output.json` and updates `metadata.json`.
10. UI polls `GET /api/status/<run_id>` and `/api/tasks/<run_id>` to render the review board.
11. User triggers `POST /api/push` which `integrations/jira_client.py` (or `jira_mcp_client.py`) executes, returning a `JiraPushResult` per task.

**State Management:**
- In-memory run state: `active_runs` and `active_orchestrators` dicts in `ui/server.py`, keyed by `run_id`.
- Durable run state: `data/sessions/<run_id>/metadata.json` and `data/sessions/<run_id>/pipeline_output.json` written by `pipeline/orchestrator.py` and read by `ui/server.py`.
- Audit trail: append-only SQLite at `data/audit.db` via `audit/logger.py`, plus `data/audit.jsonl` mirror.
- Telemetry queue: `data/telemetry_queue.jsonl` buffered by `pipeline/telemetry.py` when the Bifrost endpoint is offline.
- Encrypted settings: `data/settings.json` with Fernet key at `data/.keyfile` managed by `ui/server.py` and `pipeline/llm_router.py`.

## Key Abstractions

**Domain Schemas:**
- Purpose: Canonical typed contracts for runs, tasks, and push results.
- Examples: `RunConfig`, `RawTask`, `ManagedTask`, `TaskStatus`, `TaskFlag`, `LLMMode`, `JiraHierarchy`, `JiraPushResult` in `models/schemas.py`; `ProcessingStatus` and request models in `ui/server.py`; eval contracts in `models/eval_schemas.py`.
- Pattern: Pydantic v2 models flow across API, orchestration, agents, integrations, and persistence.

**LLM Client:**
- Purpose: Normalize provider/model routing and completion semantics under LiteLLM.
- Examples: `LLMClient` in `pipeline/llm_client.py`, `configure_litellm_for_mode()` in `pipeline/llm_router.py`.
- Pattern: One client + router pair consumed by every LLM-dependent component (indexer, extraction, dedup, gap recovery, judges).

**Pipeline Orchestrator:**
- Purpose: Single transaction boundary for one extraction run.
- Examples: `PipelineOrchestrator.run()` in `pipeline/orchestrator.py`.
- Pattern: Step-wise orchestration with explicit status callbacks, run-scoped logging context, and durable checkpointing.

**Audit Logger:**
- Purpose: Persist action-level trace of agent and integration decisions.
- Examples: `AuditLogger` in `audit/logger.py`, invoked from `pipeline/agents/*.py`, `pipeline/orchestrator.py`, `integrations/jira_client.py`.
- Pattern: Central SQLite append log shared by every execution component.

## Entry Points

**CLI Entry:**
- Location: `main.py`
- Triggers: `python main.py`
- Responsibilities: Prompt the user for run config interactively, initialize `AuditLogger`, instantiate `PipelineOrchestrator`, and invoke `run()`.

**Web API Entry (dev):**
- Location: `ui/server.py`
- Triggers: `uvicorn ui.server:app --reload` (or `python ui/server.py`, or `make ui`).
- Responsibilities: Serve `ui/index.html` + static assets, expose `/api/*` endpoints, manage sessions, run the pipeline in FastAPI background tasks, handle Jira push.

**Web API Entry (production):**
- Location: `ui/server.py` served by Gunicorn with Uvicorn workers.
- Triggers: `gunicorn ui.server:app -k uvicorn.workers.UvicornWorker` in `Dockerfile`; `docker compose up` via `docker-compose.yml`.
- Responsibilities: Same as dev entry, with healthcheck at `/api/status` on port 8000.

**Orchestrator Programmatic Entry:**
- Location: `pipeline/orchestrator.py`
- Triggers: Instantiated from `main.py` and `ui/server.py`.
- Responsibilities: Execute the extraction lifecycle and persist output artifacts.

## Error Handling

**Strategy:** Layer-appropriate resilience — agents return safe defaults on parse/model failure, the API converts exceptions into structured HTTP responses, and integrations retry with degraded modes before surfacing failure to the user.

**Patterns:**
- Agent-level resilience: extraction, deduplication, and gap recovery wrap LLM/JSON parsing in `try/except`, log the failure via Loguru, and return empty or unchanged collections so the pipeline continues (`pipeline/agents/extraction.py`, `pipeline/agents/deduplication.py`, `pipeline/agents/gap_recovery.py`).
- API-layer translation: `ui/server.py` route handlers and the background `run_pipeline_task`/`run_push_task` functions catch broad exceptions, update the in-memory run status to `error`, and convert raised exceptions into `HTTPException` with explicit status codes for callers.
- Domain-blocking errors raise specific exceptions: `RuntimeError` in `main.py` and `pipeline/llm_client.py` for unrecoverable LLM configuration issues, `ValueError` in `integrations/jira_client.py` for invalid push input.
- Jira fallback: `integrations/jira_client.py` retries issue creation without parent linkage when the parent-bearing call fails, allowing degraded push to succeed and recording the fallback in the audit log.
- Recoverable persistence failures (e.g., tree cache load) log a warning and fall back to recomputing rather than aborting the run (`pipeline/orchestrator.py`).

## Cross-Cutting Concerns

**Logging:** Loguru configured in `pipeline/observability.py`; every LLM call and integration wraps its work in `logger.contextualize(agent=..., run_id=...)` so log records correlate to a single run (`pipeline/llm_client.py`, `integrations/jira_client.py`).

**Tracing:** OpenTelemetry tracer initialized in `pipeline/observability.py`; spans wrap orchestrator steps and LLM calls. OTLP exports target the local Bifrost gateway / Tempo (`infra/user/docker-compose.user.yml`, `config/user/tempo.yaml`).

**Telemetry buffering:** `pipeline/telemetry.py` spools events to `data/telemetry_queue.jsonl` when the remote sink is unreachable and drains on the next successful flush.

**Validation:** Pydantic v2 validators on every domain model in `models/schemas.py`, plus FastAPI request/response models in `ui/server.py`.

**Authentication / Secrets:** Persisted settings are Fernet-encrypted at rest (`ui/server.py`, `pipeline/llm_router.py`); environment-driven Jira and LiteLLM credentials are loaded via `python-dotenv` in `main.py` and `ui/server.py`.

**Audit:** Every state transition, dedup decision, and Jira push is recorded by `audit/logger.py` to `data/audit.db` and `data/audit.jsonl`.

---

*Architecture analysis: 2026-05-11*
