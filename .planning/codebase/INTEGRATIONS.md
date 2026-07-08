# External Integrations

**Analysis Date:** 2026-05-11

## APIs & External Services

### Jira (Atlassian)

Two parallel push paths are implemented; the UI/pipeline chooses one per run.

**Path A — Direct REST via `jira` SDK (default, production-tested):**
- Location: `integrations/jira_client.py` — class `JiraClient`.
- SDK: `jira>=3.8.0` (`from jira import JIRA`, basic auth with email + API token).
- Hierarchies supported: `JiraHierarchy.FLAT`, `JiraHierarchy.EPIC_TASK`, `JiraHierarchy.STORY_SUBTASK` (defined in `models/schemas.py`).
- Pre-flight validation calls `self.jira.project(self.project_key)` and resolves available issue types via `project.issueTypes`; falls back if the desired type (`Task`/`Story`/`Epic`/`Sub-task`) is unavailable (`_resolve_issue_type`).
- Container linking uses unified `parent` field (`fields["parent"] = {"key": parent_key}`); a fallback retry strips `parent` and re-creates flat if a 400/parent error is returned (`_create_task`).
- Default issue-type names from `config/sow_config.json::jira` block: `task_issue_type=Task`, `story_issue_type=Story`, `epic_issue_type=Epic`, `subtask_issue_type=Sub-task`.

**Path B — Official Atlassian Rovo MCP Remote:**
- Location: `integrations/jira_mcp_client.py` — class `JiraMCPClient`.
- SDK: `mcp` (`from mcp import ClientSession, StdioServerParameters`, `from mcp.client.stdio import stdio_client`).
- Transport: spawns `npx -y @atlassian/mcp-remote https://mcp.atlassian.com/v1/mcp --email <email> --token <token>` wrapped in a Node `child_process` to filter stdout JSON-RPC frames from log noise.
- Tools called: `create-issue` with `projectKey`, `summary`, `description`, `issueType`, optional `parentKey`.
- Token preference: `JIRA_MCP_API` env var, falling back to `JIRA_API_TOKEN`.

**Auth (both paths):**
- `JIRA_SERVER` — e.g. `https://your-domain.atlassian.net`.
- `JIRA_EMAIL` — Atlassian account email.
- `JIRA_API_TOKEN` — Atlassian API token (REST path basic auth and MCP fallback).
- `JIRA_MCP_API` — Optional separate token for the Rovo MCP proxy.
- `JIRA_PROJECT_KEY` — Target project, set at run start in `main.py` and overridable per request in `ui/server.py`.

**Standalone harnesses:** `test_jira_api.py`, `test_jira_mcp.py` at repo root exercise each path independently.

### LLM Providers via LiteLLM

All LLM completions flow through a single client. Provider routing is resolved per run.

**Routing & client:**
- `pipeline/llm_router.py::configure_litellm_for_mode(mode: LLMMode)` returns a `ProviderConfig` (no global env mutation) by:
  1. Loading `data/settings.json` via `config/settings.py::SettingsManager` (Fernet-encrypted API key decrypted at use).
  2. Building a litellm-compatible model string via `build_litellm_model(provider, model, azure_deployment)` — handles `openai/`, `anthropic/`, `gemini/`, `ollama/`, `azure/<deployment>`, `openrouter/`, `groq/`, `mistral/`, `together/`, `cohere/`, `zai/` prefixes.
  3. `_ensure_docker_host` rewrites `localhost`/`127.0.0.1` to `${DOCKER_HOST_INTERNAL:-host.docker.internal}` so containers reach host services (e.g. Ollama).
- `pipeline/llm_client.py::LLMClient` wraps `litellm.completion(...)` with retry hint parsing (`Retry-After`, `x-ratelimit-reset`, provider-message regex), exponential+jitter backoff, distinct local-Ollama loop with unlimited retries, and OpenTelemetry success/failure callbacks (`litellm.success_callback = ["opentelemetry"]`) when `ARGUS_SYNC_ENABLED=true`.

**Modes (`models/schemas.py::LLMMode`):**
- `LLMMode.API` — Default routes through Bifrost (`BIFROST_BASE_URL` + `BIFROST_API_KEY`), with z.ai GLM (`ZAI_MODEL`, default `glm-4`) as the default model and the `x-zai-api-key` header set from `ZAI_API_KEY` when present.
- `LLMMode.LOCAL` — Defaults to Ollama; model `${OLLAMA_MODEL:-qwen2.5:7b}` and base `${OLLAMA_BASE_URL:-http://localhost:11434}` (after Docker-host rewrite). Trailing `/v1` and `/` are stripped before LiteLLM. Header `x-ollama-base-url` is forwarded when set.
- `LLMMode.CUSTOM` — Falls back to `gpt-4o` if no model in settings.

**Providers registered in `config/settings.py::PROVIDER_REGISTRY`:**
- OpenAI (`https://api.openai.com/v1`).
- Anthropic (`https://api.anthropic.com`).
- Google / Gemini (`https://generativelanguage.googleapis.com/v1`; LiteLLM prefix `gemini/`).
- Ollama (`http://localhost:11434`; LiteLLM prefix `ollama/`).
- OpenRouter (`https://openrouter.ai/api/v1`).
- Groq (`https://api.groq.com/openai/v1`).
- Mistral (`https://api.mistral.ai/v1`).
- Together (`https://api.together.xyz/v1`).
- Cohere (`https://api.cohere.ai/v1`).
- Azure OpenAI (deployment-driven: `azure/<deployment_name>`).
- z.ai (`https://api.z.ai/v1`).

**Eval-time judge (separate path):**
- `pipeline/evals/judges.py::HierarchicalJudge` uses `langchain_openai.ChatOpenAI` against Bifrost (`BIFROST_URL` default `http://s2j-admin-bifrost:8081/v1`, key `BIFROST_API_KEY`). Default judge model `ollama/llama3`. This LangChain dependency is only installed in the admin evaluator image.

### Atlassian Rovo MCP Remote Server

- Endpoint: `https://mcp.atlassian.com/v1/mcp`.
- Spawned via `npx -y @atlassian/mcp-remote` (requires Node available on the host or container).
- Auth: `--email ${JIRA_EMAIL} --token ${JIRA_MCP_API or JIRA_API_TOKEN}`.
- See `integrations/jira_mcp_client.py::_async_push_tasks`.

### PageIndex (Vendored)

- Vendored module at `pageindex/` (VectifyAI PageIndex), wrapped by `pipeline/indexer.py::DocumentIndexer`.
- Builds a hierarchical TOC tree from a PDF using LiteLLM-routed model calls; flattens to nodes consumed downstream by extraction.
- Config: `pageindex/config.yaml` defaults (`max_page_num_each_node: 10`, `max_token_num_each_node: 20000`, `toc_check_page_num: 20`), with `model` injected at runtime.
- Reentry/caching: `pipeline/orchestrator.py::TREE_CACHE_PATH` persists the tree between runs for the same PDF.

### PDF Parser (OpenDataLoader)

- `pipeline/parser.py::PDFParser` calls `opendataloader_pdf.convert(input_path, output_dir, format="json")`.
- Output written to `data/parser_output/<stem>.json`; elements flattened recursively from the `kids` hierarchy (headings, paragraphs, tables, lists, captions).

## Data Storage

**Databases:**
- SQLite — Local audit log at `data/audit.db` (`audit/logger.py::AuditLogger`); table `audit_log` with `run_id`, `agent`, `node_id`, `action`, `task_id`, `detail`, `llm_tokens_used`, `llm_model`.
- PostgreSQL 16 — Admin-only, backs Langfuse (`infra/admin/docker-compose.admin.yml` service `db`, DSN `postgresql://langfuse:langfuse@db:5432/langfuse`). Not used by the application path.

**File Storage:**
- Local filesystem only. Layout under `${SOW_DATA_DIR:-data}/`:
  - `data/uploads/` — Uploaded SOW PDFs.
  - `data/parser_output/` — OpenDataLoader JSON output (`pipeline/parser.py`).
  - `data/sessions/<run_id>/metadata.json` + `pipeline_output.json` — Per-run state (`ui/server.py`, `pipeline/orchestrator.py`).
  - `data/logs/run_<run_id>.log` — Per-run loguru file sinks (`pipeline/observability.py::add_run_file_logger`).
  - `data/system.log` (rotation 10MB, 10 days, zip) — System loguru sink.
  - `data/audit.jsonl` (rotation 50MB, 30 days, serialized) — JSON audit sink.
  - `data/audit.db` — SQLite audit DB.
  - `data/settings.json` — Encrypted runtime settings.
  - `data/.keyfile` — 32-byte Fernet key (0600).
  - `data/argus_storage/` — OTel file-storage queue for store-and-forward (`config/user/argus-collector-edge.yaml`).
- User compose mounts `./data` into the app container (`infra/user/docker-compose.user.yml`).

**Caching:**
- In-memory only: `active_runs`, `active_orchestrators` dicts in `ui/server.py`; per-run epic/story caches in `integrations/jira_client.py` and `integrations/jira_mcp_client.py`. No external cache (Redis/etc).

## Authentication & Identity

**Application UI:**
- No multi-user auth; FastAPI app is intended to bind to `127.0.0.1:8000` for local use (see `infra/user/docker-compose.user.yml` and `Dockerfile` healthcheck).
- CORS / trusted origins controlled via `BETTER_AUTH_TRUSTED_ORIGINS` env var (`ui/server.py`), default `http://localhost:8000,http://127.0.0.1:8000`.

**External services:**
- Jira REST — HTTP basic with email + API token (`integrations/jira_client.py`).
- Jira MCP — Same token passed via CLI flag to `@atlassian/mcp-remote`.
- LLM providers — Per-provider API keys (Fernet-encrypted in `data/settings.json`; decrypted at LLM call time).
- Bifrost — `BIFROST_API_KEY` (`pipeline/llm_router.py`); evaluator uses `evaluator-key-local-ollama` virtual key in `config/admin/bifrost.admin.yaml`.
- Argus HQ collector — `ARGUS_BACKBONE_TOKEN` sent as `Authorization: Bearer ...` to the configured `ARGUS_HQ_URL` (`config/user/argus-collector-edge.yaml`).
- Langfuse — `LANGFUSE_PUBLIC_KEY` + `LANGFUSE_SECRET_KEY` (`scripts/seed_langfuse_dataset.py`, `infra/admin/evaluator/main.py`); OTLP HTTP exporter uses `Basic ${LANGFUSE_AUTH_BASE64}` (`config/admin/argus-collector-admin.yaml`).

## Monitoring & Observability

The observability backbone is branded **Argus**. There are two stacks: an end-user edge stack (forwards to a remote HQ) and a self-hosted admin/HQ stack.

**Tracing:**
- OpenTelemetry SDK (`opentelemetry-api`, `opentelemetry-sdk`) configured in `pipeline/observability.py`. Tracer is `trace.get_tracer("sow-to-jira")`.
- Traceloop/OpenLLMetry (`traceloop-sdk`) auto-instruments LLM calls: `Traceloop.init(app_name=..., disable_reports=True, exporter_args={"endpoint": resolve_collector_endpoint()})` (`pipeline/observability.py::init_argus`). Only initializes when `ARGUS_SYNC_ENABLED=true`.
- LiteLLM OTel callback: `litellm.success_callback = ["opentelemetry"]`, `litellm.failure_callback = ["opentelemetry"]` (`pipeline/llm_client.py::LLMClient.__init__`).
- FastAPI auto-instrumentation: `FastAPIInstrumentor` imported in `ui/server.py`.
- Span helper decorator: `pipeline/observability.py::trace_span(name, agent, run_id)` used in `pipeline/parser.py`, `integrations/jira_client.py`, etc.

**Metrics:**
- `gen_ai.client.token.usage` (counter) and `gen_ai.client.operation.duration` (histogram) — defined in `pipeline/observability.py`, recorded in `pipeline/llm_client.py::_perform_one_call` with attributes `gen_ai.token.type`, `argus.instance_id`, `model`.
- Bifrost exports its own Prometheus metrics (governance/financials) — scraped by `config/admin/prometheus.admin.yml` job `bifrost` at `bifrost:8080`.
- Argus collector exposes a Prometheus scrape endpoint at `argus-collector:8889` (`config/admin/argus-collector-admin.yaml`).

**Logging:**
- Loguru, configured in `pipeline/observability.py`:
  - stdout colored sink with OTel trace ID in extras.
  - `data/system.log` rotating sink (10 MB / 10 days / zip).
  - `data/audit.jsonl` JSON serialized sink (50 MB / 30 days, excludes DEBUG).
  - Per-run sink added/removed by `add_run_file_logger(run_id)` / `run_logger(run_id)` context manager.
- OTel logging instrumentation (`LoggingInstrumentor`) injects `trace_id`/`span_id` when sync is enabled.
- Legacy `TelemetryEmitter` (`pipeline/telemetry.py`) now just routes events through loguru with structured payloads (e.g. `llm.call`, `llm.retry`).

**Backbone components (user stack, `infra/user/docker-compose.user.yml`):**
- `argus-collector` — `otel/opentelemetry-collector-contrib:0.96.0`. Receives OTLP gRPC (4317) and HTTP (4318) from the app at `ARGUS_COLLECTOR_URL`. Store-and-forwards to `${ARGUS_HQ_URL}` with `Authorization: Bearer ${ARGUS_BACKBONE_TOKEN}` and a persistent file-storage queue at `/app/data/argus_storage` (`config/user/argus-collector-edge.yaml`).
- `bifrost` — `maximhq/bifrost:latest`. LLM gateway with Loki/Tempo wiring (`LOKI_URL=http://loki:3100`, `TEMPO_URL=http://tempo:4317`).
- `loki` — `grafana/loki:2.9.4`, exposed on `127.0.0.1:3100`.
- `tempo` — `grafana/tempo:2.3.1`, config at `config/user/tempo.yaml` (OTLP grpc+http receivers, local block storage, span-metrics + service-graphs metrics-generator → Prometheus remote-write).
- `grafana` — `grafana/grafana:10.2.3`, exposed on `127.0.0.1:3000`, anonymous Admin role.

**HQ components (admin stack, `infra/admin/docker-compose.admin.yml`):**
- `bifrost` — Port 8081 (avoid collision with user stack); virtual keys + budgets defined in `config/admin/bifrost.admin.yaml` (providers `openai-hq`, `anthropic-hq`, `ollama-local`); OTel plugin pointed at `argus-collector:4317`.
- `argus-collector` — OTel Collector Contrib; receives 4317/4318 and fans out to Tempo, Langfuse (OTLP HTTP at `http://langfuse:3000/api/public/otel` with `Basic ${LANGFUSE_AUTH_BASE64}`), Loki, and Prometheus (`config/admin/argus-collector-admin.yaml`).
- `langfuse` — `langfuse/langfuse:latest` on port 3002, backed by `postgres:16` (service `db`). Used for AI tracing/analytics and dataset/evaluation runs.
- `loki` — Port 3101.
- `tempo` — Reuses `config/user/tempo.yaml` (copied during install).
- `prometheus` — Port 9090, scrapes `bifrost:8080` and `argus-collector:8889` (`config/admin/prometheus.admin.yml`).
- `grafana` — Port 3001, anonymous Admin role.
- `evaluator` — Built from `infra/admin/evaluator/Dockerfile`; runs `infra/admin/evaluator/main.py` which polls Langfuse and scores traces using `pipeline/evals/judges.py::HierarchicalJudge` (LangChain + Bifrost-routed LLM judge).

**Error Tracking:**
- None dedicated (no Sentry). Errors surface through loguru `error`/`exception`, OTel span `record_exception`, and per-run audit DB rows.

**Telemetry Verification:**
- `scripts/verify-telemetry.py` — Pings logger and emits a test `verification.ping` telemetry event for end-to-end smoke check.

## CI/CD & Deployment

**Hosting:**
- Container image distributed via GitHub Container Registry: `ghcr.io/magggiiii/sow_2_jira:${S2J_VERSION}` (referenced from `infra/user/docker-compose.user.yml` and pulled by `scripts/install/install.sh`).
- Local installer creates `~/.sow_to_jira/` with the user compose file and provisions an `s2j` shell alias (and optionally `s2j-admin`) into `~/.zshrc` or `~/.bashrc`.

**CI Pipeline:**
- Not detected in the repository (no `.github/workflows/`, `.gitlab-ci.yml`, or similar configs at the explored paths). Release tagging follows `v1.1.x` style — see `CHANGELOG.md` and `VERSION`.

**Production check:**
- `scripts/prod-check.sh` — Operational sanity script (shell helper).

## Environment Configuration

**Required env vars (Jira push):**
- `JIRA_SERVER`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, `JIRA_PROJECT_KEY`.
- Optional: `JIRA_MCP_API` (preferred for the MCP path).

**Required env vars (LLM, mode-dependent):**
- API mode via Bifrost: `BIFROST_BASE_URL`, `BIFROST_API_KEY` (and `ZAI_API_KEY`/`ZAI_MODEL` to route to z.ai GLM).
- Local mode: `OLLAMA_BASE_URL`, `OLLAMA_MODEL` (Ollama service must expose `OLLAMA_HOST=0.0.0.0` so containers can reach it via `host.docker.internal`).
- UI-driven providers: persisted in `data/settings.json` (Fernet-encrypted), so env vars become optional once configured through the UI.

**Optional env vars (observability):**
- `ARGUS_SYNC_ENABLED` (default `false` — OTel init is skipped when off).
- `ARGUS_COLLECTOR_URL` (default `http://localhost:4317`).
- `SOW_INSTANCE_ID`, `ARGUS_HQ_URL`, `ARGUS_BACKBONE_TOKEN` (used by `config/user/argus-collector-edge.yaml`).
- `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` (eval seeders + admin evaluator only).

**Optional env vars (runtime tuning):**
- `LLM_REMOTE_MAX_ATTEMPTS` (default 8), `LLM_REMOTE_MAX_ELAPSED_S` (default 300), `LLM_REMOTE_MAX_WAIT_S` (default 300) — `pipeline/llm_client.py`.
- `MAX_NODES`, `DOCKER_HOST_INTERNAL`, `SOW_DATA_DIR`, `SOW_FERNET_KEY`, `BETTER_AUTH_TRUSTED_ORIGINS`.

**Secrets location:**
- Repo: `.env` (gitignored) and `.env.example` (template) at root.
- User install: `~/.sow_to_jira/.env`, seeded with non-secret defaults by `scripts/install/install.sh`; secrets entered via the UI and stored Fernet-encrypted in `~/.sow_to_jira/data/settings.json`.
- Fernet key: `data/.keyfile` (0600) or overridden by `SOW_FERNET_KEY`.

## Webhooks & Callbacks

**Incoming:**
- None. Jira and Atlassian Rovo are called outbound only; no webhook receivers are exposed.

**Outgoing:**
- Jira REST `POST /rest/api/2/issue` (issued by `jira.JIRA.create_issue` in `integrations/jira_client.py`).
- Atlassian MCP `create-issue` tool calls over stdio to `@atlassian/mcp-remote` → `https://mcp.atlassian.com/v1/mcp` (`integrations/jira_mcp_client.py`).
- LLM provider HTTPS calls via `litellm.completion` (`pipeline/llm_client.py`) — endpoints determined per-provider by `PROVIDER_REGISTRY` and user settings.
- OTLP exports to local collector at `ARGUS_COLLECTOR_URL` (gRPC 4317), forwarded by the edge collector to `${ARGUS_HQ_URL}` with bearer auth (`config/user/argus-collector-edge.yaml`).
- Langfuse OTLP HTTP ingestion at `http://langfuse:3000/api/public/otel` from the admin collector (`config/admin/argus-collector-admin.yaml`).

---

*Integration audit: 2026-05-11*
