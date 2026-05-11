# Codebase Structure

**Analysis Date:** 2026-05-11

## Directory Layout

```
sow_to_jira/
├── main.py                        # CLI entry point for the extraction pipeline
├── ui/                            # FastAPI web server, static UI assets
│   ├── server.py                  # FastAPI app, /api/* routes, session/run management
│   ├── index.html                 # Single-page UI shell
│   ├── app.js                     # Browser-side run/review logic
│   └── styles.css                 # UI styling
├── pipeline/                      # Core extraction engine
│   ├── orchestrator.py            # PipelineOrchestrator — single run engine
│   ├── indexer.py                 # DocumentIndexer wrapper over vendored PageIndex
│   ├── parser.py                  # OpenDataLoader PDF parsing helpers
│   ├── llm_client.py              # LLMClient — LiteLLM-backed completion abstraction
│   ├── llm_router.py              # Provider/mode routing + encrypted settings access
│   ├── coverage.py                # Coverage tracking across indexed nodes
│   ├── telemetry.py               # Telemetry queueing/drain to Bifrost/Loki
│   ├── observability.py           # Loguru + OpenTelemetry setup
│   ├── agents/                    # Per-stage LLM and deterministic agents
│   │   ├── extraction.py          # Node → RawTask extraction
│   │   ├── state.py               # RawTask → ManagedTask lifecycle
│   │   ├── deduplication.py       # Semantic + LLM dedup
│   │   └── gap_recovery.py        # Uncovered-node recovery sweep
│   └── evals/                     # Evaluation judges for golden datasets
│       └── judges.py
├── integrations/                  # External system clients
│   ├── jira_client.py             # Direct Jira REST/SDK push
│   └── jira_mcp_client.py         # MCP-based Atlassian remote client
├── models/                        # Pydantic v2 contracts
│   ├── schemas.py                 # RunConfig, RawTask, ManagedTask, enums, push results
│   └── eval_schemas.py            # Evaluation/judge contracts
├── pageindex/                     # VENDORED VectifyAI PageIndex (third-party, in-tree)
│   ├── page_index.py              # PDF tree indexer
│   ├── page_index_md.py           # Markdown variant
│   ├── utils.py
│   └── config.yaml                # PageIndex token/page caps + dynamic model
├── audit/                         # Audit logging subsystem
│   └── logger.py                  # AuditLogger — SQLite + JSONL append log
├── config/                        # Runtime + deployment configuration
│   ├── sow_config.json            # Pipeline thresholds, Jira issue type defaults
│   ├── settings.py                # Settings loader helpers
│   ├── admin/                     # Admin-stack observability configs
│   │   ├── argus-collector-admin.yaml
│   │   ├── argus-dashboard.json
│   │   ├── bifrost.admin.yaml
│   │   └── prometheus.admin.yml
│   └── user/                      # User-stack observability configs
│       ├── argus-collector-edge.yaml
│       └── tempo.yaml
├── scripts/                       # Operational scripts (not part of pytest)
│   ├── prod-check.sh              # Production readiness check
│   ├── run_eval_dataset.py        # Run evaluations against a dataset
│   ├── seed_full_langfuse_dataset.py
│   ├── seed_hierarchical_eval_dataset.py
│   ├── seed_langfuse_dataset.py
│   ├── verify-telemetry.py
│   └── install/
│       └── install.sh             # Unified installer
├── infra/                         # Docker compose stacks for local infra
│   ├── admin/
│   │   ├── docker-compose.admin.yml
│   │   └── evaluator/             # Sidecar evaluator service
│   │       ├── Dockerfile
│   │       ├── main.py
│   │       └── requirements.txt
│   └── user/
│       └── docker-compose.user.yml
├── tests/                         # Pytest suite
│   ├── test_hierarchical_judge.py
│   ├── test_phase11_evals.py
│   ├── test_phase2_runtime_reliability.py
│   └── test_routing.py
├── data/                          # GITIGNORED runtime state (sessions, audit, settings)
├── logs/                          # GITIGNORED runtime logs and raw LLM dumps
├── .planning/                     # GSD planning workspace (PROJECT.md, phases, codebase docs)
├── docs/                          # Long-form docs and GSD reports
├── Dockerfile                     # Production container build
├── docker-compose.yml             # Default local app compose
├── docker-compose.ollama.yml      # Ollama overlay
├── docker-compose.bifrost.yml     # Bifrost observability overlay
├── requirements.txt               # Pinned Python deps (no lockfile)
├── Makefile                       # venv, install, ui, run targets
├── install_mac.sh                 # macOS bootstrap
├── install_ubuntu.sh              # Ubuntu bootstrap
├── install_windows.ps1            # Windows bootstrap
├── CLAUDE.md                      # Project instructions for Claude
├── AGENTS.md                      # Agent guidance
├── README.md
├── CONTRIBUTING.md
├── CHANGELOG.md
├── VERSION
├── LICENSE
├── test_jira_api.py               # Root-level ad-hoc script (NOT collected by pytest)
├── test_jira_mcp.py               # Root-level ad-hoc script (NOT collected by pytest)
├── test_discovery.py              # Root-level ad-hoc script (NOT collected by pytest)
└── test_settings.py               # Root-level ad-hoc script (NOT collected by pytest)
```

## Directory Purposes

**`ui/`:**
- Purpose: FastAPI web server and static assets that drive the browser review experience.
- Contains: `server.py` (app object, routes, session/run state), HTML/CSS/JS frontend.
- Key files: `ui/server.py`, `ui/index.html`, `ui/app.js`.

**`pipeline/`:**
- Purpose: Core extraction engine — orchestrator plus shared infrastructure (LLM routing, observability, telemetry, indexing).
- Contains: Orchestrator, indexer, LLM client/router, parser, coverage tracker, telemetry buffer, Loguru/OTel setup.
- Key files: `pipeline/orchestrator.py`, `pipeline/llm_client.py`, `pipeline/llm_router.py`, `pipeline/indexer.py`, `pipeline/observability.py`, `pipeline/telemetry.py`.

**`pipeline/agents/`:**
- Purpose: Per-stage agents that mutate the task list across the run lifecycle.
- Contains: Extraction, state lifecycle, deduplication, gap recovery agents.
- Key files: `pipeline/agents/extraction.py`, `pipeline/agents/state.py`, `pipeline/agents/deduplication.py`, `pipeline/agents/gap_recovery.py`.

**`pipeline/evals/`:**
- Purpose: LLM judges and scoring for golden-dataset evaluation runs.
- Key files: `pipeline/evals/judges.py`.

**`integrations/`:**
- Purpose: Outbound clients for external systems (Jira REST and Atlassian MCP).
- Key files: `integrations/jira_client.py`, `integrations/jira_mcp_client.py`.

**`models/`:**
- Purpose: Pydantic v2 typed contracts shared by every layer.
- Key files: `models/schemas.py` (core domain), `models/eval_schemas.py` (eval domain).

**`pageindex/`:**
- Purpose: VENDORED third-party PageIndex library (VectifyAI) for PDF tree indexing. Treat as in-tree dependency — avoid invasive edits and prefer wrapping behavior in `pipeline/indexer.py`.
- Key files: `pageindex/page_index.py`, `pageindex/page_index_md.py`, `pageindex/utils.py`, `pageindex/config.yaml`.

**`audit/`:**
- Purpose: Append-only audit subsystem capturing every agent and integration decision.
- Key files: `audit/logger.py` (writes `data/audit.db` and `data/audit.jsonl`).

**`config/`:**
- Purpose: Runtime configuration plus observability stack configs split by deployment role.
- Subdirs: `config/admin/` (admin-side telemetry collector, Bifrost, Prometheus, dashboard), `config/user/` (user-side collector, Tempo).
- Key files: `config/sow_config.json`, `config/settings.py`.

**`scripts/`:**
- Purpose: Operator-facing scripts for production checks, evaluation seeding/running, and telemetry verification. Not collected by pytest.
- Subdirs: `scripts/install/` (unified `install.sh` bootstrap script).
- Key files: `scripts/run_eval_dataset.py`, `scripts/seed_hierarchical_eval_dataset.py`, `scripts/prod-check.sh`.

**`infra/`:**
- Purpose: Docker compose stacks for local observability and admin services, split by role.
- Subdirs: `infra/admin/` (admin compose + evaluator sidecar service), `infra/user/` (user compose).
- Key files: `infra/admin/docker-compose.admin.yml`, `infra/user/docker-compose.user.yml`, `infra/admin/evaluator/main.py`.

**`tests/`:**
- Purpose: Pytest suite — the canonical automated test location.
- Key files: `tests/test_routing.py`, `tests/test_phase2_runtime_reliability.py`, `tests/test_phase11_evals.py`, `tests/test_hierarchical_judge.py`.

**`data/`:**
- Purpose: GITIGNORED runtime state. Contains sessions, audit DB, encrypted settings, uploads, telemetry queue, parser output. Generated, not committed.
- Key paths: `data/sessions/<run_id>/`, `data/audit.db`, `data/audit.jsonl`, `data/settings.json`, `data/.keyfile`, `data/telemetry_queue.jsonl`, `data/uploads/`, `data/parser_output/`.

**`logs/`:**
- Purpose: GITIGNORED runtime logs and raw LLM response dumps. Generated, not committed.
- Key paths: `logs/combined.log`, `logs/error.log`, `logs/raw_*.json`.

**`.planning/`:**
- Purpose: GSD workspace — project briefs, phase plans, codebase analysis docs, handoff notes.
- Key paths: `.planning/PROJECT.md`, `.planning/REQUIREMENTS.md`, `.planning/ROADMAP.md`, `.planning/STATE.md`, `.planning/HANDOFF.json`, `.planning/phases/`, `.planning/codebase/`.

**`docs/`:**
- Purpose: Long-form documentation, GSD reports, and superpowers/handoff notes (committed).
- Subdirs: `docs/gsd/reports/`, `docs/handoff/`, `docs/superpowers/specs/`.

## Key File Locations

**Entry Points:**
- `main.py`: CLI runner — interactive prompts → `PipelineOrchestrator.run()`.
- `ui/server.py`: FastAPI app served via Uvicorn (dev) or Gunicorn (prod).
- `Dockerfile`: Production container, runs `gunicorn ui.server:app -k uvicorn.workers.UvicornWorker`.

**Configuration:**
- `config/sow_config.json`: Pipeline thresholds and Jira defaults.
- `pageindex/config.yaml`: PageIndex token/page caps and dynamic model.
- `.env` / `.env.example`: Environment variables (Jira, LiteLLM, Bifrost, Ollama). Never read `.env` contents.
- `data/settings.json` + `data/.keyfile`: Fernet-encrypted runtime settings managed by the UI.

**Core Logic:**
- `pipeline/orchestrator.py`: Single extraction engine.
- `pipeline/agents/*.py`: Stage-specific agents.
- `pipeline/llm_client.py`, `pipeline/llm_router.py`: LLM abstraction.
- `integrations/jira_client.py`, `integrations/jira_mcp_client.py`: External push.

**Testing:**
- `tests/`: Pytest suite — only files here are collected automatically.

**Audit & Observability:**
- `audit/logger.py`: Audit writer.
- `pipeline/observability.py`: Loguru + OTel setup.
- `pipeline/telemetry.py`: Telemetry buffering.

## Naming Conventions

**Files:**
- `snake_case.py` for all Python modules (e.g., `pipeline/orchestrator.py`, `integrations/jira_client.py`).
- Pytest files use `test_*.py` inside `tests/`. Root-level `test_*.py` files are ad-hoc scripts, not part of the pytest run.

**Directories:**
- Lowercase, single-word where possible (`pipeline`, `agents`, `integrations`, `models`).
- Role-split subdirs use `admin/` and `user/` (e.g., `config/admin/`, `config/user/`, `infra/admin/`, `infra/user/`).

## Where to Add New Code

**New Pipeline Stage / Agent:**
- Implementation: `pipeline/agents/<stage>.py`.
- Wire into the run: `pipeline/orchestrator.py`.
- Schema additions: `models/schemas.py`.
- Tests: `tests/test_<stage>.py`.

**New API Endpoint:**
- Route handler: `ui/server.py`.
- Request/response models: define alongside the route in `ui/server.py` or extend `models/schemas.py`.
- Frontend wiring: `ui/app.js`, `ui/index.html`.

**New External Integration:**
- Client module: `integrations/<service>_client.py`.
- Reuse `pipeline/llm_router.py` patterns for credential loading if it is an LLM provider.
- Add credentials to `.env.example`.

**New Domain Model:**
- Pydantic class: `models/schemas.py` (core) or `models/eval_schemas.py` (eval).
- Import directly via `from models.schemas import <Name>`.

**New Evaluation:**
- Judge: `pipeline/evals/judges.py`.
- Dataset seeder: `scripts/seed_*_dataset.py`.
- Runner: extend `scripts/run_eval_dataset.py`.

**New Operational Script:**
- Place under `scripts/`. Use shell for system checks, Python for dataset work.

**New Infra Service:**
- Compose under `infra/admin/` or `infra/user/` depending on role.
- Service code (if any) in a subdirectory alongside the compose file (mirror `infra/admin/evaluator/`).

**New Test:**
- File: `tests/test_<area>.py`. Do NOT add ad-hoc `test_*.py` at the repo root — those are not collected.

## Special Directories

**`data/`:**
- Purpose: All runtime state — sessions, audit DB, encrypted settings, uploaded PDFs, telemetry queue.
- Generated: Yes (created on first run by `ui/server.py` and `pipeline/orchestrator.py`).
- Committed: No (`.gitignore`).

**`logs/`:**
- Purpose: Loguru log sinks and raw LLM JSON dumps per run.
- Generated: Yes.
- Committed: No (`.gitignore`).

**`pageindex/`:**
- Purpose: Vendored third-party library (VectifyAI PageIndex) — kept in-tree to pin behavior.
- Generated: No.
- Committed: Yes. Treat edits cautiously; prefer wrapping in `pipeline/indexer.py`.

**`.planning/`:**
- Purpose: GSD workspace artifacts. Read by `/gsd:*` commands.
- Generated: Yes (by GSD commands).
- Committed: Yes.

**Root-level ad-hoc scripts (`test_jira_api.py`, `test_jira_mcp.py`, `test_discovery.py`, `test_settings.py`):**
- Purpose: Hand-run debugging scripts for Jira REST, Jira MCP, provider discovery, and settings encryption.
- Generated: No.
- Committed: Yes.
- IMPORTANT: These are NOT part of the pytest run. Pytest only collects under `tests/`. Run them manually with `python <file>.py`.

---

*Structure analysis: 2026-05-11*
