# Technology Stack

**Analysis Date:** 2026-05-11

## Languages

**Primary:**
- Python 3.11 — All backend logic (`main.py`, `ui/server.py`, `pipeline/**/*.py`, `integrations/*.py`, `models/*.py`, `audit/logger.py`, `config/settings.py`, vendored `pageindex/*.py`); container base image is `python:3.11-slim` in `Dockerfile`.

**Secondary:**
- HTML / CSS / JavaScript — Static review UI in `ui/index.html`, `ui/app.js`, `ui/styles.css`, served via FastAPI `StaticFiles` mount in `ui/server.py`.
- YAML — Runtime and deployment config in `infra/admin/docker-compose.admin.yml`, `infra/user/docker-compose.user.yml`, `config/admin/*.yaml`, `config/user/*.yaml`, `pageindex/config.yaml`.
- Shell (Bash) — Unified installer in `scripts/install/install.sh`; production check helper at `scripts/prod-check.sh`.
- Python (utility scripts) — Telemetry verification (`scripts/verify-telemetry.py`) and Langfuse dataset seeding (`scripts/seed_langfuse_dataset.py`, `scripts/seed_full_langfuse_dataset.py`, `scripts/seed_hierarchical_eval_dataset.py`, `scripts/run_eval_dataset.py`).

## Runtime

**Environment:**
- Python 3.11 (declared via `FROM python:3.11-slim` in `Dockerfile`).
- Native deps installed at image build time: `build-essential`, `python3-dev`, `libmupdf-dev`, `libfreetype6-dev` (`Dockerfile` builder stage).

**ASGI Runtime:**
- Production: Gunicorn + `uvicorn.workers.UvicornWorker`, 2 workers, bound `0.0.0.0:8000` — `CMD` in `Dockerfile`.
- Local dev: Uvicorn with `--reload --port 8000` via `make ui` (`Makefile`).
- CLI runtime: `python main.py` (also `make run`).

**Package Manager:**
- `pip` installing from `requirements.txt` (`Makefile`, `Dockerfile`).
- Lockfile: **missing** — no `requirements.lock`, `Pipfile.lock`, or `poetry.lock`. Versions are floor-pinned (`>=`) only; reproducible installs are not guaranteed.

**Application Version:**
- `VERSION` file tracks app version (currently `v1.1.14`); used by `scripts/install/install.sh` to pin the published image tag `ghcr.io/magggiiii/sow_2_jira:${S2J_VERSION}`.

## Frameworks

**Core / Web:**
- FastAPI — HTTP + WebSocket API server (`ui/server.py`) exposing `/api/*` routes and serving the static UI.
- Pydantic v2 (`pydantic>=2.0.0,<3.0.0`) — Schema models in `models/schemas.py` (e.g. `RunConfig`, `RawTask`, `ManagedTask`, `JiraPushResult`, `ProviderConfig`, `LLMMode`, `JiraHierarchy`) and `models/eval_schemas.py`.
- Uvicorn / Gunicorn — ASGI server (see Runtime).
- `python-multipart` — Required by FastAPI to handle PDF uploads via `UploadFile` (`requirements.txt`).
- `tenacity` — Retry decorators imported in `ui/server.py` (`@retry`, `stop_after_attempt`, `wait_exponential`, `retry_if_exception_type`). Note: present in code but **not pinned in `requirements.txt`** — relies on transitive resolution.

**LLM / ML:**
- LiteLLM (`litellm>=1.82.0`) — Unified provider router (`pipeline/llm_client.py`, `pipeline/llm_router.py`).
- OpenAI SDK (`openai>=2.8.0`) — Pulled in transitively for LiteLLM compat and required by `make verify`.
- `sentence-transformers>=2.7.0` — Embedding model `all-MiniLM-L6-v2` for deduplication agent (`pipeline/agents/deduplication.py`).
- `traceloop-sdk>=0.33.0` — OpenLLMetry / Traceloop OTel instrumentation for LLM spans (`pipeline/observability.py`).
- LangChain (`langchain`, `langchain-openai`) — Used by `pipeline/evals/judges.py` (LLM-as-a-judge) and admin evaluator (`infra/admin/evaluator/requirements.txt`). Note: **not in root `requirements.txt`**; only installed inside the admin evaluator container.

**PDF Parsing & Indexing:**
- `opendataloader-pdf` — Primary PDF parser (`pipeline/parser.py`).
- `pymupdf>=1.26.0` — Used by vendored PageIndex.
- `PyPDF2>=3.0.1` — Used by vendored PageIndex.
- Vendored PageIndex module (VectifyAI) — Lives in `pageindex/` (`pageindex/page_index.py`, `pageindex/utils.py`, `pageindex/page_index_md.py`); wrapped by `pipeline/indexer.py` (`DocumentIndexer`).

**Integrations:**
- `jira>=3.8.0` — Direct Jira REST client (`integrations/jira_client.py`).
- `mcp` — Model Context Protocol SDK used for the official Atlassian Rovo MCP path (`integrations/jira_mcp_client.py`).

**Observability:**
- `loguru>=0.7.0` — Structured console + JSON audit logging (`pipeline/observability.py`).
- `opentelemetry-api>=1.24.0`, `opentelemetry-sdk>=1.24.0` — Core OTel API.
- `opentelemetry-exporter-otlp>=1.24.0` — OTLP exporter for traces/metrics/logs.
- `opentelemetry-instrumentation-fastapi>=0.45b0` — Auto-instrumentation for FastAPI (`ui/server.py` imports `FastAPIInstrumentor`).
- `opentelemetry-instrumentation-logging>=0.45b0` — Injects `trace_id`/`span_id` into log records (`pipeline/observability.py`).

**Security:**
- `cryptography>=42.0.0` — Fernet symmetric encryption for persisted provider secrets (`config/settings.py` `SettingsManager`, key at `data/.keyfile` or env `SOW_FERNET_KEY`).

**Testing:**
- `pytest>=8.0.0` — Real test suite under `tests/` (`tests/test_phase2_runtime_reliability.py`, `tests/test_routing.py`, `tests/test_hierarchical_judge.py`, `tests/test_phase11_evals.py`). pytest cache lives at `.pytest_cache/`.
- `pytest-asyncio>=0.23.0` — Async test support.
- Legacy ad-hoc test scripts at repo root: `test_jira_api.py`, `test_jira_mcp.py`, `test_settings.py`, `test_discovery.py`.

**HTTP / Utilities:**
- `httpx>=0.27.0` — Async HTTP client used in `ui/server.py` (provider model discovery).
- `requests>=2.31.0` — Sync HTTP client (provider discovery, install helpers).
- `python-dotenv>=1.0.0` — `.env` loading in `main.py` and `ui/server.py`.
- `rich>=13.7.0` — Console/CLI rendering in `main.py`, `pipeline/llm_client.py`, `pageindex/page_index.py`.
- `pyyaml>=6.0` — Reads `pageindex/config.yaml` via `pageindex/utils.py::ConfigLoader`.
- `numpy>=1.26.0` — Embedding vector math in deduplication agent.

**Build / Dev:**
- `make` — Developer task runner: `make venv`, `make install`, `make run`, `make ui`, `make verify`, `make clean` (`Makefile`).
- Docker / Docker Compose — Local stacks at `infra/user/docker-compose.user.yml` (end-user stack: app, edge collector, bifrost, loki, tempo, grafana) and `infra/admin/docker-compose.admin.yml` (admin/HQ stack: bifrost, argus-collector, langfuse + Postgres, loki, tempo, prometheus, grafana, evaluator).

## Key Dependencies

**Critical (core pipeline cannot run without these):**
- `fastapi` — API + UI server (`ui/server.py`).
- `pydantic` — Typed models everywhere (`models/schemas.py`, `models/eval_schemas.py`, `ui/server.py`, agents).
- `litellm` — All LLM calls flow through it (`pipeline/llm_client.py`, `pipeline/llm_router.py`, indirectly in `pipeline/agents/extraction.py`, `pipeline/agents/deduplication.py`, `pipeline/agents/gap_recovery.py`).
- `jira` — Direct push path (`integrations/jira_client.py`).
- `opendataloader-pdf` — PDF ingestion (`pipeline/parser.py`).
- `sentence-transformers` + `numpy` — Semantic dedup (`pipeline/agents/deduplication.py`).
- `cryptography` (Fernet) — Encrypts persisted API tokens at `data/settings.json` (`config/settings.py`).

**Infrastructure / Observability:**
- `loguru` — Console + `data/system.log` + `data/audit.jsonl` (`pipeline/observability.py`).
- `opentelemetry-*` — Traces & metrics OTLP-exported to the local Argus edge collector at `ARGUS_COLLECTOR_URL` (default `http://localhost:4317`).
- `traceloop-sdk` (Traceloop / OpenLLMetry) — Auto-instruments LLM calls into OTel spans when `ARGUS_SYNC_ENABLED=true` (`pipeline/observability.py::init_argus`).

**Integration SDKs:**
- `mcp` — Stdio MCP client for the official `@atlassian/mcp-remote` Rovo proxy (`integrations/jira_mcp_client.py`).

## Configuration

**Environment Loading:**
- `load_dotenv()` is called early in `main.py` and `ui/server.py`.
- `.env` and `.env.example` exist at repo root (existence only; contents not read here).

**Persisted Runtime Settings:**
- `data/settings.json` — Provider + Jira config selected via the UI; secrets encrypted via Fernet (`config/settings.py::SettingsManager`).
- `data/.keyfile` — 32-byte Fernet key material, 0600 perms; can be overridden by env `SOW_FERNET_KEY`.

**Pipeline Behavior Config:**
- `config/sow_config.json` — Known section headers, skip sections, Jira default issue types (`Task`/`Story`/`Epic`/`Sub-task`), pipeline caps (`max_gap_recovery_iterations`, `pageindex_max_pages_per_node`, `pageindex_max_tokens_per_node`, `max_section_chars`).
- `pageindex/config.yaml` — PageIndex defaults: `toc_check_page_num: 20`, `max_page_num_each_node: 10`, `max_token_num_each_node: 20000`, node summary/text toggles. The `model` field is set dynamically by `pipeline/indexer.py::DocumentIndexer`.

**Provider Registry (in code):**
- `config/settings.py::PROVIDER_REGISTRY` — Defines base URLs and visibility for: `openai`, `anthropic`, `google`, `ollama`, `openrouter`, `groq`, `mistral`, `together`, `cohere`, `azure`, `zai`.

**Key Environment Variables Consumed in Code:**
- Jira: `JIRA_SERVER`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, `JIRA_PROJECT_KEY`, `JIRA_MCP_API` (`integrations/jira_client.py`, `integrations/jira_mcp_client.py`, `ui/server.py`).
- LiteLLM legacy bridge: `LITELLM_PROVIDER`, `LITELLM_MODEL`, `LITELLM_API_KEY`, `LITELLM_API_BASE`, `AZURE_API_VERSION`, `AZURE_DEPLOYMENT_NAME` (`ui/server.py::_apply_settings_to_env_legacy`).
- z.ai (API mode default route through Bifrost): `ZAI_API_KEY`, `ZAI_MODEL` (`pipeline/llm_client.py`, `pipeline/llm_router.py`).
- Ollama local mode: `OLLAMA_BASE_URL` (default `http://localhost:11434`), `OLLAMA_MODEL` (default `qwen2.5:7b`) — `main.py`, `pipeline/llm_router.py`, `pipeline/llm_client.py`.
- Bifrost (LLM gateway): `BIFROST_BASE_URL`, `BIFROST_API_KEY` — `pipeline/llm_router.py`. Note: `.env.example` also lists `BIFROST_GATEWAY_URL`, `BIFROST_BACKBONE_TOKEN`, `BIFROST_LOKI_URL`, `BIFROST_TELEMETRY_URL`, `BIFROST_TELEMETRY_TOKEN`, but only `BIFROST_BASE_URL`/`BIFROST_API_KEY` are read by current code.
- Argus / OTel: `ARGUS_SYNC_ENABLED` (default `false`), `ARGUS_COLLECTOR_URL` (default `http://localhost:4317`), `SOW_INSTANCE_ID`, `ARGUS_HQ_URL`, `ARGUS_BACKBONE_TOKEN` (`pipeline/observability.py`, `config/user/argus-collector-edge.yaml`).
- Langfuse (evals only): `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` (`scripts/seed_langfuse_dataset.py`, `infra/admin/evaluator/main.py`, `pipeline/evals/judges.py` indirectly via Bifrost).
- LLM retry tuning: `LLM_REMOTE_MAX_ATTEMPTS` (default 8), `LLM_MAX_ATTEMPTS`, `LLM_REMOTE_MAX_ELAPSED_S` (default 300), `LLM_REMOTE_MAX_WAIT_S` (default 300) — `pipeline/llm_client.py`.
- Paths/runtime: `SOW_DATA_DIR` (default `data`), `SOW_FERNET_KEY`, `DOCKER_HOST_INTERNAL`, `MAX_NODES`, `BETTER_AUTH_TRUSTED_ORIGINS`.

**Container/Compose Config:**
- `Dockerfile` — Multi-stage build (`builder` installs deps to `/install`, final stage copies + runs as user `sow:1000`); healthcheck hits `/api/status`.
- `infra/user/docker-compose.user.yml` — End-user stack on `127.0.0.1:8000`. Services: `app`, `argus-collector` (`otel/opentelemetry-collector-contrib:0.96.0`), `bifrost` (`maximhq/bifrost:latest`), `loki` (`grafana/loki:2.9.4`, port 3100), `tempo` (`grafana/tempo:2.3.1`), `grafana` (`grafana/grafana:10.2.3`, port 3000).
- `infra/admin/docker-compose.admin.yml` — Admin/HQ stack. Services: `bifrost` (port 8081), `argus-collector` (ports 4317/4318), `langfuse` (port 3002) + Postgres 16, `loki` (port 3101), `tempo`, `prometheus` (port 9090), `grafana` (port 3001), `evaluator` (built from `infra/admin/evaluator/`).
- `config/admin/*.yaml` and `config/user/*.yaml` — Collector, Bifrost, Tempo, Prometheus configurations consumed by the compose files.

## Platform Requirements

**Development:**
- Python 3.11 with `pip` and `make` (Makefile uses `python3 -m venv venv`).
- Docker + Docker Compose (for telemetry stack, Bifrost, optional Langfuse).
- Optional: Ollama installed locally for LLM `LOCAL` mode; installer at `scripts/install/install.sh` will offer to install via Homebrew (macOS) or `ollama.com/install.sh` (Linux). The install script also sets `OLLAMA_HOST=0.0.0.0` so containers can reach the host Ollama at `http://host.docker.internal:11434`.
- Native libs for PDF parsing: `libmupdf-dev`, `libfreetype6-dev` (auto-installed in container; required on host for `pip install pymupdf` if not using Docker).

**Production / Deployment Target:**
- Published image: `ghcr.io/magggiiii/sow_2_jira:${S2J_VERSION}` (referenced from `infra/user/docker-compose.user.yml` and `scripts/install/install.sh`).
- Default HTTP service exposed on `8000` with `/api/status` healthcheck (`Dockerfile`, both compose files).
- User stack expects to mount `./data` into `/app/data` for persistence.
- Installer target: `~/.sow_to_jira/` user-home install layout with `s2j` and optional `s2j-admin` shell aliases (`scripts/install/install.sh`).

---

*Stack analysis: 2026-05-11*
