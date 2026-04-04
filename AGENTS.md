<!-- GSD:project-start source:PROJECT.md -->
## Project

**SOW-to-Jira Portable Extraction Engine**

SOW-to-Jira is a local-first system that converts Statement of Work documents into structured, reviewable Jira work items. It provides a web UI and API for upload, extraction, task review, and Jira push, with pluggable LLM providers through LiteLLM. Current focus is stabilizing provider switching and observability so production debugging is reliable.

**Core Value:** Given a complex SOW, the system must reliably produce actionable Jira-ready tasks with transparent run status and logs.

### Constraints

- **Runtime**: Python 3.11 + FastAPI + LiteLLM — maintain compatibility with current production image and dependencies
- **Deployment**: Docker-first local bring-up — observability must run through compose in a repeatable way
- **Security**: Persisted credentials must remain encrypted at rest — no plaintext fallback
- **Stability**: Pipeline behavior cannot regress for existing extraction and Jira push flows
- **Observability**: Logs and traces must be debuggable from terminal and Grafana/Bifrost in local environment
<!-- GSD:project-end -->

<!-- GSD:stack-start source:codebase/STACK.md -->
## Technology Stack

## Languages
- Python 3.11 - Application runtime and all backend logic in `main.py`, `ui/server.py`, `pipeline/*.py`, `integrations/*.py`, `models/schemas.py`, and `pageindex/*.py`; container base image in `Dockerfile`.
- HTML/CSS/JS (static frontend assets) - Served by FastAPI static mount in `ui/server.py`.
- YAML - Runtime and deployment config in `docker-compose.yml`, `docker-compose.ollama.yml`, `docker-compose.bifrost.yml`, and `pageindex/config.yaml`.
- Shell/PowerShell - Installation/bootstrap scripts in `install_mac.sh`, `install_ubuntu.sh`, `install_windows.ps1`.
## Runtime
- Python runtime: `python:3.11-slim` in `Dockerfile`.
- ASGI app runtime: FastAPI app object in `ui/server.py` served by Gunicorn+Uvicorn worker in `Dockerfile`.
- `pip` with `requirements.txt` (used in `Dockerfile` and `Makefile`).
- Lockfile: missing (no `poetry.lock`, `Pipfile.lock`, or `requirements.lock` detected).
## Frameworks
- FastAPI - HTTP API/UI server (`ui/server.py`) and endpoints under `/api/*`.
- Pydantic v2 - Schema models and request/response validation in `models/schemas.py` and `ui/server.py`.
- VectifyAI PageIndex (vendored local module) - PDF tree indexing via `pipeline/indexer.py` and `pageindex/page_index.py`.
- Not detected as a formal test framework in dependencies; ad-hoc test scripts exist in `test_jira_api.py` and `test_jira_mcp.py`.
- Uvicorn - local dev server command in `Makefile` (`make ui`) and import in `ui/server.py`.
- Gunicorn - production server command in `Dockerfile` (`ui.server:app`).
- Docker Compose - local orchestration in `docker-compose.yml`, `docker-compose.ollama.yml`, `docker-compose.bifrost.yml`.
- Make - developer task runner in `Makefile`.
## Key Dependencies
- `fastapi` - API server framework (`ui/server.py`, `requirements.txt`).
- `pydantic` - typed models and validation (`models/schemas.py`, `ui/server.py`, `requirements.txt`).
- `litellm` - LLM provider abstraction/routing (`pipeline/llm_client.py`, `pipeline/llm_router.py`, `requirements.txt`).
- `jira` - Jira issue creation/push (`integrations/jira_client.py`, `requirements.txt`).
- `opendataloader-pdf` - PDF parsing backend (`pipeline/parser.py`, `requirements.txt`).
- `mcp` - MCP client support for Atlassian remote server (`integrations/jira_mcp_client.py`, `requirements.txt`).
- `cryptography` - Fernet encryption for persisted secrets (`ui/server.py`, `pipeline/llm_router.py`, `requirements.txt`).
- `opentelemetry-*` - tracing and OTLP export (`pipeline/observability.py`, `requirements.txt`).
- `loguru` - structured logging (`pipeline/observability.py`, `requirements.txt`).
- `requests` - outbound HTTP for provider discovery and telemetry/log transport (`ui/server.py`, `pipeline/observability.py`, `pipeline/telemetry.py`, `requirements.txt`).
- `python-dotenv` - environment loading in `main.py` and `ui/server.py`.
## Configuration
- Environment variables are loaded via `load_dotenv()` in `main.py` and `ui/server.py`.
- Runtime settings are persisted in encrypted `data/settings.json` with key material at `data/.keyfile` (`ui/server.py`, `pipeline/llm_router.py`).
- `.env` and `.env.example` files are present at repo root (existence only; contents not read).
- Key configuration env variables consumed in code:
- `JIRA_SERVER`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, `JIRA_PROJECT_KEY` in `integrations/jira_client.py`, `integrations/jira_mcp_client.py`, and `ui/server.py`.
- `LITELLM_PROVIDER`, `LITELLM_MODEL`, `LITELLM_API_KEY`, `LITELLM_API_BASE` in `pipeline/llm_router.py`, `pipeline/llm_client.py`, and `ui/server.py`.
- `BIFROST_BASE_URL`, `BIFROST_API_KEY`, `BIFROST_TELEMETRY_URL`, `BIFROST_TELEMETRY_TOKEN`, `BIFROST_LOKI_URL` in `pipeline/llm_router.py` and `pipeline/observability.py`.
- `OLLAMA_BASE_URL`, `OLLAMA_MODEL` in `main.py`, `pipeline/llm_client.py`, `pipeline/llm_router.py`, and `docker-compose.ollama.yml`.
- Pipeline behavior config in `config/sow_config.json` (Jira issue type defaults, extraction/indexing limits).
- PageIndex defaults in `pageindex/config.yaml` (dynamic model plus token/page caps).
- Container build/runtime config in `Dockerfile` and compose manifests.
## Platform Requirements
- Python 3.11 environment (inferred from `Dockerfile` and dependency compatibility).
- `pip` install via `requirements.txt` (`Makefile` targets `venv` and `install`).
- Optional local services:
- Ollama at `http://localhost:11434` or compose service `ollama` (`docker-compose.ollama.yml`, `pipeline/llm_router.py`).
- Bifrost gateway on `localhost:8080` for routing/observability (`docker-compose.bifrost.yml`, `pipeline/observability.py`).
- Containerized deployment with Gunicorn/Uvicorn in `Dockerfile`.
- Default exposed HTTP service on port `8000` with healthcheck at `/api/status` in `docker-compose.yml`.
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->
## Conventions

## Naming Patterns
- Use `snake_case.py` module names across core code, including `pipeline/orchestrator.py`, `pipeline/observability.py`, `integrations/jira_client.py`, and `audit/logger.py`.
- Test-like scripts use the `test_*.py` prefix at repo root: `test_jira_api.py`, `test_jira_mcp.py`.
- Use `snake_case` for functions and methods, including private helpers with leading underscore (for example `_build_or_load_tree` in `pipeline/orchestrator.py`, `_build_description` in `integrations/jira_client.py`, `_load_settings` in `ui/server.py`).
- Route handlers in `ui/server.py` use verb-style names (`get_tasks`, `start_processing`, `push_to_jira`).
- Local variables use `snake_case` (`run_config`, `dedup_threshold`, `section_text`).
- Constants use `UPPER_SNAKE_CASE` (`TREE_CACHE_PATH` in `pipeline/orchestrator.py`, `EXTRACTION_SYSTEM_PROMPT` in `pipeline/agents/extraction.py`, `SETTINGS_PATH` in `ui/server.py`).
- Enum class names use `PascalCase` (`TaskStatus`, `TaskFlag`, `LLMMode`, `JiraHierarchy` in `models/schemas.py`).
- Pydantic model names use `PascalCase` (`RunConfig`, `ManagedTask`, `JiraPushResult` in `models/schemas.py`; `ProcessingStatus` and request models in `ui/server.py`).
## Code Style
- Tool used: Not detected (`pyproject.toml`, `setup.cfg`, `.flake8`, `ruff.toml`, and formatter configs are not present at repository root).
- Key settings: Not applicable; style is enforced by existing code patterns rather than config.
- Follow existing spacing and readability conventions: blank lines between logical blocks and concise inline comments where needed (for example in `pipeline/orchestrator.py`, `integrations/jira_client.py`, `ui/server.py`).
- Tool used: Not detected (no lint config files in repository root).
- Key rules: Implicitly follow current code patterns:
## Import Organization
- Not detected. Use direct package-relative imports like `from models.schemas import ...` and `from pipeline.orchestrator import PipelineOrchestrator` (`main.py`, `ui/server.py`).
## Error Handling
- Wrap network/API/IO boundaries in `try/except`, log failure, and return structured fallback data where possible.
- Raise domain-specific exceptions for blocking failures (for example `RuntimeError` in `main.py` and `pipeline/llm_client.py`, `ValueError` in `integrations/jira_client.py`).
- Convert backend failures to HTTP errors in API routes (`ui/server.py` uses `HTTPException` with explicit status codes).
- Preserve pipeline continuity by recording recoverable failures and returning empty/default values (`pipeline/agents/extraction.py`, `ui/server.py`).
## Logging
- Use contextual logging via `logger.contextualize(agent=..., run_id=...)` for correlated tracing (`pipeline/llm_client.py`, `integrations/jira_client.py`).
- Log lifecycle checkpoints and step progress in orchestrated flows (`pipeline/orchestrator.py`, `main.py`).
- Emit warnings for recoverable fallbacks and errors for terminal failures (`integrations/jira_client.py`, `pipeline/observability.py`, `ui/server.py`).
## Comments
- Add short comments for operational intent and non-obvious behavior:
- Not applicable.
- Python docstrings are used for classes and key methods (`models/schemas.py`, `pipeline/llm_client.py`, `audit/logger.py`).
## Function Design
- Core orchestration functions can be large and step-oriented (for example `PipelineOrchestrator.run` in `pipeline/orchestrator.py`).
- Helper methods stay focused on one responsibility (`_build_labels`, `_resolve_issue_type` in `integrations/jira_client.py`; `_extract_models_from_response` in `ui/server.py`).
- Use typed parameters and explicit defaults for behavior controls (`confidence_threshold` in `pipeline/agents/extraction.py`, `max_nodes` in `ui/server.py`, optional session IDs in API helpers).
- Prefer typed structured returns over raw tuples:
## Module Design
- Use direct symbol imports from module files; no central export map.
- Keep domain separation by module path:
- Limited usage. `pipeline/agents/__init__.py` exists but core imports generally target concrete modules directly.
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->
## Architecture

## Pattern Overview
- `pipeline/orchestrator.py` is the central coordinator that sequences indexing, extraction, state management, deduplication, gap recovery, and persistence.
- Domain contracts are strongly typed through Pydantic models in `models/schemas.py` and passed across all layers.
- Both CLI (`main.py`) and Web API (`ui/server.py`) drive the same pipeline core (`PipelineOrchestrator`), giving one processing engine with two execution surfaces.
## Layers
- Purpose: Accept user input, start/monitor runs, and expose task review/push APIs.
- Location: `main.py`, `ui/server.py`, `ui/index.html`, `ui/app.js`, `ui/styles.css`
- Contains: CLI wizard, FastAPI routes, session status endpoints, upload handling, browser UI.
- Depends on: `models/schemas.py`, `pipeline/orchestrator.py`, `audit/logger.py`, `integrations/jira_client.py`
- Used by: End users (terminal or browser), `uvicorn` runtime.
- Purpose: Define the end-to-end extraction workflow and step ordering.
- Location: `pipeline/orchestrator.py`
- Contains: `PipelineOrchestrator`, status callback updates, run checkpointing to `data/sessions/<run_id>/pipeline_output.json`.
- Depends on: `pipeline/indexer.py`, `pipeline/coverage.py`, `pipeline/llm_client.py`, `pipeline/agents/*.py`, `pipeline/telemetry.py`
- Used by: `main.py`, `ui/server.py`
- Purpose: Parse and structure source documents into pipeline-ready nodes.
- Location: `pipeline/indexer.py`, `pageindex/page_index.py`, `pageindex/utils.py`, `pageindex/page_index_md.py`, `pipeline/parser.py`
- Contains: PageIndex wrapper (`DocumentIndexer`), flattening tree nodes, optional OpenDataLoader parser path.
- Depends on: LiteLLM routing (`pipeline/llm_router.py`), observability (`pipeline/observability.py`)
- Used by: `pipeline/orchestrator.py`
- Purpose: Transform document nodes into managed tasks and clean final output.
- Location: `pipeline/agents/extraction.py`, `pipeline/agents/state.py`, `pipeline/agents/deduplication.py`, `pipeline/agents/gap_recovery.py`
- Contains: LLM extraction, deterministic task lifecycle logic, vector+LLM dedup, uncovered-node recovery.
- Depends on: `pipeline/llm_client.py`, `models/schemas.py`, `audit/logger.py`
- Used by: `pipeline/orchestrator.py`
- Purpose: Push approved tasks into Jira systems.
- Location: `integrations/jira_client.py`, `integrations/jira_mcp_client.py`
- Contains: Direct Jira REST client via `jira` SDK and MCP-based Jira client path.
- Depends on: `models/schemas.py`, environment credentials, `audit/logger.py`
- Used by: `ui/server.py` push flow and standalone test scripts (`test_jira_api.py`, `test_jira_mcp.py`)
- Purpose: Logging, tracing, telemetry buffering, and audit persistence.
- Location: `pipeline/observability.py`, `pipeline/telemetry.py`, `audit/logger.py`
- Contains: Loguru setup, OpenTelemetry tracer setup, Loki event emitter, SQLite audit store (`data/audit.db`).
- Depends on: environment config and network endpoints.
- Used by: All pipeline and integration modules.
## Data Flow
- In-memory run state: `active_runs` and `active_orchestrators` in `ui/server.py`.
- Durable run state: `data/sessions/<run_id>/metadata.json` and `data/sessions/<run_id>/pipeline_output.json` in `ui/server.py` and `pipeline/orchestrator.py`.
- Domain state transitions use `TaskStatus` enum in `models/schemas.py`.
## Key Abstractions
- Purpose: Canonical request and entity schema for pipeline + UI + integrations.
- Examples: `RunConfig`, `RawTask`, `ManagedTask`, `JiraPushResult` in `models/schemas.py`
- Pattern: Shared Pydantic contracts across modules instead of ad-hoc dicts.
- Purpose: Single transaction boundary for one extraction run.
- Examples: `PipelineOrchestrator.run()` in `pipeline/orchestrator.py`
- Pattern: Step-wise orchestration with explicit checkpointing and status callbacks.
- Purpose: Normalize model/provider routing and completion semantics.
- Examples: `LLMClient` (`pipeline/llm_client.py`), `configure_litellm_for_mode()` (`pipeline/llm_router.py`)
- Pattern: One client + router abstraction consumed by all LLM-dependent agents/modules.
- Purpose: Persist action-level trace of agent and integration decisions.
- Examples: `AuditLogger` in `audit/logger.py`, calls from `pipeline/agents/*.py`, `integrations/jira_client.py`
- Pattern: Central SQLite append log used by all execution components.
## Entry Points
- Location: `main.py`
- Triggers: `python main.py`
- Responsibilities: Prompt run config, initialize audit logger, invoke `PipelineOrchestrator.run()`.
- Location: `ui/server.py`
- Triggers: `uvicorn ui.server:app --reload` or `python ui/server.py`
- Responsibilities: Serve static UI, manage sessions/status, execute pipeline in background tasks, handle Jira push.
- Location: `pipeline/orchestrator.py`
- Triggers: Instantiated from `main.py` and `ui/server.py`
- Responsibilities: Execute extraction lifecycle and persist output artifacts.
## Error Handling
- Agent-level resilience returns empty/unchanged results on parse/model errors (`pipeline/agents/extraction.py`, `pipeline/agents/deduplication.py`, `pipeline/agents/gap_recovery.py`).
- API layer catches exceptions and converts to status/error payloads (`ui/server.py` in `run_pipeline_task`, `run_push_task`, route handlers).
- Integration fallback retries Jira issue creation without parent linkage after parent-related failures (`integrations/jira_client.py`).
## Cross-Cutting Concerns
<!-- GSD:architecture-end -->

<!-- GSD:workflow-start source:GSD defaults -->
## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:
- `/gsd:quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd:debug` for investigation and bug fixing
- `/gsd:execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->



<!-- GSD:profile-start -->
## Developer Profile

> Profile not yet configured. Run `/gsd:profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->
