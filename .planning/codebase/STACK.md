# Technology Stack

**Analysis Date:** 2026-03-30

## Languages

**Primary:**
- Python 3.11 - Application runtime and all backend logic in `main.py`, `ui/server.py`, `pipeline/*.py`, `integrations/*.py`, `models/schemas.py`, and `pageindex/*.py`; container base image in `Dockerfile`.

**Secondary:**
- HTML/CSS/JS (static frontend assets) - Served by FastAPI static mount in `ui/server.py`.
- YAML - Runtime and deployment config in `docker-compose.yml`, `docker-compose.ollama.yml`, `docker-compose.bifrost.yml`, and `pageindex/config.yaml`.
- Shell/PowerShell - Installation/bootstrap scripts in `install_mac.sh`, `install_ubuntu.sh`, `install_windows.ps1`.

## Runtime

**Environment:**
- Python runtime: `python:3.11-slim` in `Dockerfile`.
- ASGI app runtime: FastAPI app object in `ui/server.py` served by Gunicorn+Uvicorn worker in `Dockerfile`.

**Package Manager:**
- `pip` with `requirements.txt` (used in `Dockerfile` and `Makefile`).
- Lockfile: missing (no `poetry.lock`, `Pipfile.lock`, or `requirements.lock` detected).

## Frameworks

**Core:**
- FastAPI - HTTP API/UI server (`ui/server.py`) and endpoints under `/api/*`.
- Pydantic v2 - Schema models and request/response validation in `models/schemas.py` and `ui/server.py`.
- VectifyAI PageIndex (vendored local module) - PDF tree indexing via `pipeline/indexer.py` and `pageindex/page_index.py`.

**Testing:**
- Not detected as a formal test framework in dependencies; ad-hoc test scripts exist in `test_jira_api.py` and `test_jira_mcp.py`.

**Build/Dev:**
- Uvicorn - local dev server command in `Makefile` (`make ui`) and import in `ui/server.py`.
- Gunicorn - production server command in `Dockerfile` (`ui.server:app`).
- Docker Compose - local orchestration in `docker-compose.yml`, `docker-compose.ollama.yml`, `docker-compose.bifrost.yml`.
- Make - developer task runner in `Makefile`.

## Key Dependencies

**Critical:**
- `fastapi` - API server framework (`ui/server.py`, `requirements.txt`).
- `pydantic` - typed models and validation (`models/schemas.py`, `ui/server.py`, `requirements.txt`).
- `litellm` - LLM provider abstraction/routing (`pipeline/llm_client.py`, `pipeline/llm_router.py`, `requirements.txt`).
- `jira` - Jira issue creation/push (`integrations/jira_client.py`, `requirements.txt`).
- `opendataloader-pdf` - PDF parsing backend (`pipeline/parser.py`, `requirements.txt`).
- `mcp` - MCP client support for Atlassian remote server (`integrations/jira_mcp_client.py`, `requirements.txt`).

**Infrastructure:**
- `cryptography` - Fernet encryption for persisted secrets (`ui/server.py`, `pipeline/llm_router.py`, `requirements.txt`).
- `opentelemetry-*` - tracing and OTLP export (`pipeline/observability.py`, `requirements.txt`).
- `loguru` - structured logging (`pipeline/observability.py`, `requirements.txt`).
- `requests` - outbound HTTP for provider discovery and telemetry/log transport (`ui/server.py`, `pipeline/observability.py`, `pipeline/telemetry.py`, `requirements.txt`).
- `python-dotenv` - environment loading in `main.py` and `ui/server.py`.

## Configuration

**Environment:**
- Environment variables are loaded via `load_dotenv()` in `main.py` and `ui/server.py`.
- Runtime settings are persisted in encrypted `data/settings.json` with key material at `data/.keyfile` (`ui/server.py`, `pipeline/llm_router.py`).
- `.env` and `.env.example` files are present at repo root (existence only; contents not read).
- Key configuration env variables consumed in code:
- `JIRA_SERVER`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, `JIRA_PROJECT_KEY` in `integrations/jira_client.py`, `integrations/jira_mcp_client.py`, and `ui/server.py`.
- `LITELLM_PROVIDER`, `LITELLM_MODEL`, `LITELLM_API_KEY`, `LITELLM_API_BASE` in `pipeline/llm_router.py`, `pipeline/llm_client.py`, and `ui/server.py`.
- `BIFROST_BASE_URL`, `BIFROST_API_KEY`, `BIFROST_TELEMETRY_URL`, `BIFROST_TELEMETRY_TOKEN`, `BIFROST_LOKI_URL` in `pipeline/llm_router.py` and `pipeline/observability.py`.
- `OLLAMA_BASE_URL`, `OLLAMA_MODEL` in `main.py`, `pipeline/llm_client.py`, `pipeline/llm_router.py`, and `docker-compose.ollama.yml`.

**Build:**
- Pipeline behavior config in `config/sow_config.json` (Jira issue type defaults, extraction/indexing limits).
- PageIndex defaults in `pageindex/config.yaml` (dynamic model plus token/page caps).
- Container build/runtime config in `Dockerfile` and compose manifests.

## Platform Requirements

**Development:**
- Python 3.11 environment (inferred from `Dockerfile` and dependency compatibility).
- `pip` install via `requirements.txt` (`Makefile` targets `venv` and `install`).
- Optional local services:
- Ollama at `http://localhost:11434` or compose service `ollama` (`docker-compose.ollama.yml`, `pipeline/llm_router.py`).
- Bifrost gateway on `localhost:8080` for routing/observability (`docker-compose.bifrost.yml`, `pipeline/observability.py`).

**Production:**
- Containerized deployment with Gunicorn/Uvicorn in `Dockerfile`.
- Default exposed HTTP service on port `8000` with healthcheck at `/api/status` in `docker-compose.yml`.

---

*Stack analysis: 2026-03-30*
