# Codebase Structure

**Analysis Date:** 2026-03-30

## Directory Layout

```text
sow_to_jira/
├── main.py                  # CLI entry point for extraction workflow
├── ui/                      # FastAPI server + static frontend assets
├── pipeline/                # Core orchestration and processing modules
├── pipeline/agents/         # Extraction, state, dedup, and gap-recovery agents
├── models/                  # Shared Pydantic schemas and enums
├── integrations/            # Jira push integrations (SDK and MCP)
├── pageindex/               # Embedded PageIndex document-structuring engine
├── audit/                   # SQLite audit logger
├── config/                  # Pipeline/runtime JSON config
├── data/                    # Runtime artifacts (sessions, uploads, parser output)
├── logs/                    # Runtime log outputs
├── docs/                    # Handoff/support documentation
└── .planning/codebase/      # Generated mapping documents
```

## Directory Purposes

**`ui/`:**
- Purpose: Hosts backend HTTP surface and browser UI assets.
- Contains: `ui/server.py` (FastAPI app), `ui/index.html`, `ui/app.js`, `ui/styles.css`.
- Key files: `ui/server.py`, `ui/app.js`

**`pipeline/`:**
- Purpose: Implements extraction runtime and shared pipeline services.
- Contains: orchestrator (`pipeline/orchestrator.py`), LLM wrappers (`pipeline/llm_client.py`, `pipeline/llm_router.py`), observability (`pipeline/observability.py`), telemetry (`pipeline/telemetry.py`), indexing (`pipeline/indexer.py`), coverage (`pipeline/coverage.py`), parser (`pipeline/parser.py`).
- Key files: `pipeline/orchestrator.py`, `pipeline/indexer.py`, `pipeline/llm_client.py`

**`pipeline/agents/`:**
- Purpose: Encapsulates task intelligence steps.
- Contains: extraction (`pipeline/agents/extraction.py`), state machine (`pipeline/agents/state.py`), dedup (`pipeline/agents/deduplication.py`), gap recovery (`pipeline/agents/gap_recovery.py`).
- Key files: `pipeline/agents/extraction.py`, `pipeline/agents/state.py`

**`models/`:**
- Purpose: Defines shared domain contracts used by CLI, API, pipeline, and integrations.
- Contains: enums and models (`models/schemas.py`).
- Key files: `models/schemas.py`

**`integrations/`:**
- Purpose: Pushes approved tasks to Jira.
- Contains: direct Jira SDK client (`integrations/jira_client.py`) and MCP client (`integrations/jira_mcp_client.py`).
- Key files: `integrations/jira_client.py`

**`pageindex/`:**
- Purpose: Local PageIndex implementation for hierarchical PDF sectioning.
- Contains: core engine (`pageindex/page_index.py`), utilities (`pageindex/utils.py`), markdown indexing (`pageindex/page_index_md.py`), engine config (`pageindex/config.yaml`).
- Key files: `pageindex/page_index.py`, `pageindex/utils.py`

**`audit/`:**
- Purpose: Persistence of run audit events.
- Contains: SQLite logger implementation in `audit/logger.py`.
- Key files: `audit/logger.py`

**`config/`:**
- Purpose: Runtime behavior and extraction thresholds.
- Contains: `config/sow_config.json`.
- Key files: `config/sow_config.json`

## Key File Locations

**Entry Points:**
- `main.py`: CLI startup wizard and pipeline trigger.
- `ui/server.py`: Web server entry point for extraction/review/push APIs.

**Configuration:**
- `config/sow_config.json`: Pipeline defaults (`max_gap_recovery_iterations`, node/token limits, skip headers).
- `pageindex/config.yaml`: PageIndex tuning used by `pipeline/indexer.py`.
- `.env.example`: Environment variable template (file exists at repo root).

**Core Logic:**
- `pipeline/orchestrator.py`: End-to-end run sequencing.
- `pipeline/agents/extraction.py`: LLM-based task extraction.
- `pipeline/agents/state.py`: Deterministic task lifecycle handling.
- `pipeline/agents/deduplication.py`: Embedding + LLM dedup workflow.
- `integrations/jira_client.py`: Jira issue creation and hierarchy mapping.

**Testing:**
- `test_jira_api.py`: Direct Jira API connectivity test script.
- `test_jira_mcp.py`: Jira MCP connectivity test script.

## Naming Conventions

**Files:**
- `snake_case.py` for Python modules (examples: `pipeline/orchestrator.py`, `integrations/jira_mcp_client.py`).
- Lowercase asset filenames for static UI (examples: `ui/index.html`, `ui/app.js`, `ui/styles.css`).

**Directories:**
- Lowercase, singular/plural by role (`pipeline/`, `models/`, `integrations/`, `pageindex/`, `ui/`).
- Submodule grouping by concern (`pipeline/agents/` for agent classes).

## Where to Add New Code

**New Feature:**
- Primary code:
  - New extraction step/logic: `pipeline/` or `pipeline/agents/` (follow existing split by concern).
  - New API endpoint/session behavior: `ui/server.py` (or split into `ui/` helper module if endpoint count grows).
  - New domain fields/status enums: `models/schemas.py` first, then propagate through pipeline/UI.
- Tests:
  - Existing pattern is root-level scripts (`test_jira_api.py`, `test_jira_mcp.py`).
  - Add new automated tests as `test_*.py` at repo root unless a dedicated `tests/` directory is introduced.

**New Component/Module:**
- Implementation:
  - LLM-based agent: `pipeline/agents/<new_agent>.py` and wire in `pipeline/orchestrator.py`.
  - New external system integration: `integrations/<service>_client.py`, triggered from `ui/server.py`.

**Utilities:**
- Shared helpers:
  - Pipeline-scoped helper: `pipeline/` module near consumer.
  - PageIndex-specific helper: `pageindex/utils.py` or new module under `pageindex/`.
  - Avoid adding generic helper code to `main.py`; keep `main.py` as orchestration entry only.

## Special Directories

**`data/`:**
- Purpose: Runtime storage for sessions, uploads, parser output, settings keyfile, and audit DB.
- Generated: Yes
- Committed: No (not tracked in `git ls-files`)

**`logs/`:**
- Purpose: Runtime and per-run logs generated by observability setup.
- Generated: Yes
- Committed: No (not tracked in `git ls-files`)

**`venv/`:**
- Purpose: Local Python virtual environment.
- Generated: Yes
- Committed: No

**`.planning/codebase/`:**
- Purpose: Generated architecture/convention/concern maps consumed by GSD planning commands.
- Generated: Yes
- Committed: Yes (intended planning artifact directory)

---

*Structure analysis: 2026-03-30*
