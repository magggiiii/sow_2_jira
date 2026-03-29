# External Integrations

**Analysis Date:** 2026-03-30

## APIs & External Services

**Issue Tracking:**
- Jira REST API (Jira Cloud/Server) - Create and organize issues from extracted tasks.
  - SDK/Client: `jira` Python package used in `integrations/jira_client.py`.
  - Auth: `JIRA_SERVER`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, `JIRA_PROJECT_KEY`.
- Atlassian Rovo MCP Remote - Alternative Jira push path via MCP tool calls (`create-issue`).
  - SDK/Client: `mcp` Python SDK in `integrations/jira_mcp_client.py` and external `npx -y @atlassian/mcp-remote https://mcp.atlassian.com/v1/mcp`.
  - Auth: `JIRA_EMAIL` plus `JIRA_MCP_API` (fallback `JIRA_API_TOKEN`).

**LLM Providers (via LiteLLM routing):**
- OpenAI, Anthropic, Google Gemini, Ollama, OpenRouter, Groq, Mistral, Together, Cohere, Azure OpenAI, Z.AI - model inference and provider model discovery.
  - SDK/Client: `litellm` in `pipeline/llm_client.py` and `pipeline/llm_router.py`; provider registry/endpoints in `ui/server.py`.
  - Auth: provider `api_key` encrypted in `data/settings.json`, then mapped to `LITELLM_API_KEY` and provider-specific env vars in `pipeline/llm_router.py`.

**Observability/Telemetry:**
- OTLP endpoint(s) for traces - span export configured in `pipeline/observability.py`.
  - SDK/Client: `opentelemetry-exporter-otlp` (`OTLPSpanExporter`).
  - Auth: `BIFROST_TELEMETRY_TOKEN` bearer token for `BIFROST_TELEMETRY_URL`.
- Loki log ingestion (Bifrost gateway and backbone sink) - async log and telemetry push via `requests`.
  - SDK/Client: custom handlers in `pipeline/observability.py` and queue worker in `pipeline/telemetry.py`.
  - Auth: bearer token from `get_backbone_token()` or environment-backed token.

## Data Storage

**Databases:**
- Not detected (no relational/NoSQL client dependency and no DB connection code).
  - Connection: Not applicable.
  - Client: Not applicable.

**File Storage:**
- Local filesystem storage under `data/` (or `SOW_DATA_DIR`) for sessions, uploads, logs, parser output, settings, keyfile, and telemetry queue (`ui/server.py`, `pipeline/orchestrator.py`, `pipeline/parser.py`, `pipeline/observability.py`).
- Docker named volume `sow_data` mounted to `/app/data` in `docker-compose.yml`.

**Caching:**
- Local file cache for indexed document tree at `data/document_tree.json` in `pipeline/orchestrator.py`.
- In-memory process state cache for active runs/orchestrators in `ui/server.py` (`active_runs`, `active_orchestrators`).

## Authentication & Identity

**Auth Provider:**
- Custom secret management; no external identity provider detected.
  - Implementation: settings are encrypted with Fernet (`cryptography`) using `data/.keyfile` or `SOW_FERNET_KEY` in `ui/server.py` and `pipeline/llm_router.py`.
- API authentication patterns:
  - Jira basic auth with email/token in `integrations/jira_client.py`.
  - Bearer/x-api-key headers for LLM provider model discovery in `ui/server.py`.
  - Bearer token for telemetry/Loki push in `pipeline/observability.py` and `pipeline/telemetry.py`.

## Monitoring & Observability

**Error Tracking:**
- No dedicated SaaS error tracker detected (no Sentry/Bugsnag integration).
- Errors are logged through Loguru and propagated in API status/error payloads (`ui/server.py`, `pipeline/observability.py`).

**Logs:**
- Console + rotating local file logs at `data/system.log` in `pipeline/observability.py`.
- Optional remote Loki push via `BIFROST_LOKI_URL`/`LOKI_URL` in `pipeline/observability.py`.
- Telemetry buffering fallback in `data/telemetry_queue.jsonl` when remote push fails (`pipeline/observability.py`).

## CI/CD & Deployment

**Hosting:**
- Containerized self-hosted deployment (Gunicorn/Uvicorn) via `Dockerfile` and `docker-compose.yml`.
- Optional sidecar-style services: `bifrost` (`docker-compose.bifrost.yml`) and `ollama` (`docker-compose.ollama.yml`).

**CI Pipeline:**
- Not detected (`.github/workflows` or other CI config not found in repository scan).

## Environment Configuration

**Required env vars:**
- Jira push: `JIRA_SERVER`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, `JIRA_PROJECT_KEY` (`integrations/jira_client.py`, `ui/server.py`).
- MCP Jira push: `JIRA_EMAIL`, `JIRA_MCP_API` (or `JIRA_API_TOKEN`) (`integrations/jira_mcp_client.py`).
- LLM routing: `LITELLM_PROVIDER`, `LITELLM_MODEL`, `LITELLM_API_KEY`, `LITELLM_API_BASE` (`pipeline/llm_router.py`, `pipeline/llm_client.py`).
- Bifrost/API mode defaults: `BIFROST_API_KEY`, `BIFROST_BASE_URL` (`pipeline/llm_router.py`).
- Telemetry/observability: `BIFROST_TELEMETRY_URL`, `BIFROST_TELEMETRY_TOKEN`, `BIFROST_LOKI_URL`, `LOKI_URL` (`pipeline/observability.py`).
- Local model mode: `OLLAMA_BASE_URL`, `OLLAMA_MODEL` (`main.py`, `pipeline/llm_router.py`, `docker-compose.ollama.yml`).
- Security/runtime: `SOW_DATA_DIR`, `SOW_FERNET_KEY`, `BETTER_AUTH_TRUSTED_ORIGINS` (`ui/server.py`, `pipeline/observability.py`, `pipeline/llm_router.py`).

**Secrets location:**
- `.env` at repo root is present (contents not read).
- Persisted app secrets are stored encrypted in `data/settings.json`; key material in `data/.keyfile` (`ui/server.py`).
- In container mode, persisted secrets/data live in Docker volume `sow_data` mounted at `/app/data` (`docker-compose.yml`).

## Webhooks & Callbacks

**Incoming:**
- No webhook receiver endpoints detected.
- Internal HTTP API endpoints are exposed via FastAPI (`ui/server.py`), including `/api/process`, `/api/push`, `/api/upload`, `/api/settings`, and `/api/providers/{provider_id}/models`.

**Outgoing:**
- LLM provider model discovery calls via `requests.get` to provider `/models`-style endpoints in `ui/server.py`.
- Telemetry and log shipping via `requests.post` to Loki/OTLP-related backends in `pipeline/observability.py` and `pipeline/telemetry.py`.
- Jira issue creation via Jira SDK HTTP calls in `integrations/jira_client.py`.
- Atlassian MCP remote process invocation to `https://mcp.atlassian.com/v1/mcp` in `integrations/jira_mcp_client.py`.

---

*Integration audit: 2026-03-30*
