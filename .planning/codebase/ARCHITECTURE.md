# Architecture

**Analysis Date:** 2026-03-30

## Pattern Overview

**Overall:** Layered pipeline with orchestrator-centric workflow

**Key Characteristics:**
- `pipeline/orchestrator.py` is the central coordinator that sequences indexing, extraction, state management, deduplication, gap recovery, and persistence.
- Domain contracts are strongly typed through Pydantic models in `models/schemas.py` and passed across all layers.
- Both CLI (`main.py`) and Web API (`ui/server.py`) drive the same pipeline core (`PipelineOrchestrator`), giving one processing engine with two execution surfaces.

## Layers

**Interface Layer (CLI + HTTP + Static UI):**
- Purpose: Accept user input, start/monitor runs, and expose task review/push APIs.
- Location: `main.py`, `ui/server.py`, `ui/index.html`, `ui/app.js`, `ui/styles.css`
- Contains: CLI wizard, FastAPI routes, session status endpoints, upload handling, browser UI.
- Depends on: `models/schemas.py`, `pipeline/orchestrator.py`, `audit/logger.py`, `integrations/jira_client.py`
- Used by: End users (terminal or browser), `uvicorn` runtime.

**Orchestration Layer:**
- Purpose: Define the end-to-end extraction workflow and step ordering.
- Location: `pipeline/orchestrator.py`
- Contains: `PipelineOrchestrator`, status callback updates, run checkpointing to `data/sessions/<run_id>/pipeline_output.json`.
- Depends on: `pipeline/indexer.py`, `pipeline/coverage.py`, `pipeline/llm_client.py`, `pipeline/agents/*.py`, `pipeline/telemetry.py`
- Used by: `main.py`, `ui/server.py`

**Document Processing Layer:**
- Purpose: Parse and structure source documents into pipeline-ready nodes.
- Location: `pipeline/indexer.py`, `pageindex/page_index.py`, `pageindex/utils.py`, `pageindex/page_index_md.py`, `pipeline/parser.py`
- Contains: PageIndex wrapper (`DocumentIndexer`), flattening tree nodes, optional OpenDataLoader parser path.
- Depends on: LiteLLM routing (`pipeline/llm_router.py`), observability (`pipeline/observability.py`)
- Used by: `pipeline/orchestrator.py`

**Agent Layer (Task Intelligence):**
- Purpose: Transform document nodes into managed tasks and clean final output.
- Location: `pipeline/agents/extraction.py`, `pipeline/agents/state.py`, `pipeline/agents/deduplication.py`, `pipeline/agents/gap_recovery.py`
- Contains: LLM extraction, deterministic task lifecycle logic, vector+LLM dedup, uncovered-node recovery.
- Depends on: `pipeline/llm_client.py`, `models/schemas.py`, `audit/logger.py`
- Used by: `pipeline/orchestrator.py`

**Integration Layer:**
- Purpose: Push approved tasks into Jira systems.
- Location: `integrations/jira_client.py`, `integrations/jira_mcp_client.py`
- Contains: Direct Jira REST client via `jira` SDK and MCP-based Jira client path.
- Depends on: `models/schemas.py`, environment credentials, `audit/logger.py`
- Used by: `ui/server.py` push flow and standalone test scripts (`test_jira_api.py`, `test_jira_mcp.py`)

**Cross-Cutting Infrastructure Layer:**
- Purpose: Logging, tracing, telemetry buffering, and audit persistence.
- Location: `pipeline/observability.py`, `pipeline/telemetry.py`, `audit/logger.py`
- Contains: Loguru setup, OpenTelemetry tracer setup, Loki event emitter, SQLite audit store (`data/audit.db`).
- Depends on: environment config and network endpoints.
- Used by: All pipeline and integration modules.

## Data Flow

**Extraction Run Flow (CLI/UI -> Tasks):**

1. `main.py` or `ui/server.py` builds `RunConfig` (`models/schemas.py`) and instantiates `PipelineOrchestrator` (`pipeline/orchestrator.py`).
2. `PipelineOrchestrator` calls `DocumentIndexer.build_tree()` (`pipeline/indexer.py`) which executes PageIndex (`pageindex/page_index.py`) and returns flattened nodes.
3. For each node, orchestrator calls `TaskExtractionAgent.extract()` (`pipeline/agents/extraction.py`) then `TaskStateAgent.process()` (`pipeline/agents/state.py`) and marks coverage via `CoverageTracker` (`pipeline/coverage.py`).
4. After node loop, orchestrator closes remaining open tasks, runs `DeduplicationAgent.deduplicate()` (`pipeline/agents/deduplication.py`), and optionally `GapRecoveryAgent.recover()` (`pipeline/agents/gap_recovery.py`).
5. Final tasks + config + coverage are written to `data/sessions/<run_id>/pipeline_output.json` from `pipeline/orchestrator.py`.

**Review + Push Flow (UI -> Jira):**

1. `ui/app.js` calls review endpoints in `ui/server.py` (`/api/tasks`, `/api/tasks/add`, `/api/tasks` update).
2. `ui/server.py` persists edits back into session JSON via `save_data()`.
3. `/api/push` in `ui/server.py` loads approved `ManagedTask` items and invokes `JiraClient.push_tasks()` in `integrations/jira_client.py`.
4. Jira responses map back into task statuses (`APPROVED` -> `PUSHED`) and are saved to session JSON.

**State Management:**
- In-memory run state: `active_runs` and `active_orchestrators` in `ui/server.py`.
- Durable run state: `data/sessions/<run_id>/metadata.json` and `data/sessions/<run_id>/pipeline_output.json` in `ui/server.py` and `pipeline/orchestrator.py`.
- Domain state transitions use `TaskStatus` enum in `models/schemas.py`.

## Key Abstractions

**RunConfig / Task Domain Models:**
- Purpose: Canonical request and entity schema for pipeline + UI + integrations.
- Examples: `RunConfig`, `RawTask`, `ManagedTask`, `JiraPushResult` in `models/schemas.py`
- Pattern: Shared Pydantic contracts across modules instead of ad-hoc dicts.

**PipelineOrchestrator:**
- Purpose: Single transaction boundary for one extraction run.
- Examples: `PipelineOrchestrator.run()` in `pipeline/orchestrator.py`
- Pattern: Step-wise orchestration with explicit checkpointing and status callbacks.

**LLM Access Boundary:**
- Purpose: Normalize model/provider routing and completion semantics.
- Examples: `LLMClient` (`pipeline/llm_client.py`), `configure_litellm_for_mode()` (`pipeline/llm_router.py`)
- Pattern: One client + router abstraction consumed by all LLM-dependent agents/modules.

**Audit Trail:**
- Purpose: Persist action-level trace of agent and integration decisions.
- Examples: `AuditLogger` in `audit/logger.py`, calls from `pipeline/agents/*.py`, `integrations/jira_client.py`
- Pattern: Central SQLite append log used by all execution components.

## Entry Points

**CLI Entry Point:**
- Location: `main.py`
- Triggers: `python main.py`
- Responsibilities: Prompt run config, initialize audit logger, invoke `PipelineOrchestrator.run()`.

**Web API Entry Point:**
- Location: `ui/server.py`
- Triggers: `uvicorn ui.server:app --reload` or `python ui/server.py`
- Responsibilities: Serve static UI, manage sessions/status, execute pipeline in background tasks, handle Jira push.

**Pipeline Core Entry Point:**
- Location: `pipeline/orchestrator.py`
- Triggers: Instantiated from `main.py` and `ui/server.py`
- Responsibilities: Execute extraction lifecycle and persist output artifacts.

## Error Handling

**Strategy:** Localized fail-safe handling with error capture + status propagation.

**Patterns:**
- Agent-level resilience returns empty/unchanged results on parse/model errors (`pipeline/agents/extraction.py`, `pipeline/agents/deduplication.py`, `pipeline/agents/gap_recovery.py`).
- API layer catches exceptions and converts to status/error payloads (`ui/server.py` in `run_pipeline_task`, `run_push_task`, route handlers).
- Integration fallback retries Jira issue creation without parent linkage after parent-related failures (`integrations/jira_client.py`).

## Cross-Cutting Concerns

**Logging:** Central Loguru logger from `pipeline/observability.py`, consumed across modules.
**Validation:** Pydantic models in `models/schemas.py` plus FastAPI request models in `ui/server.py`.
**Authentication:** Environment-backed credentials and encrypted settings storage in `ui/server.py` + `pipeline/llm_router.py`.

---

*Architecture analysis: 2026-03-30*
