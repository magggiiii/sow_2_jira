# Coding Conventions

**Analysis Date:** 2026-03-30

## Naming Patterns

**Files:**
- Use `snake_case.py` module names across core code, including `pipeline/orchestrator.py`, `pipeline/observability.py`, `integrations/jira_client.py`, and `audit/logger.py`.
- Test-like scripts use the `test_*.py` prefix at repo root: `test_jira_api.py`, `test_jira_mcp.py`.

**Functions:**
- Use `snake_case` for functions and methods, including private helpers with leading underscore (for example `_build_or_load_tree` in `pipeline/orchestrator.py`, `_build_description` in `integrations/jira_client.py`, `_load_settings` in `ui/server.py`).
- Route handlers in `ui/server.py` use verb-style names (`get_tasks`, `start_processing`, `push_to_jira`).

**Variables:**
- Local variables use `snake_case` (`run_config`, `dedup_threshold`, `section_text`).
- Constants use `UPPER_SNAKE_CASE` (`TREE_CACHE_PATH` in `pipeline/orchestrator.py`, `EXTRACTION_SYSTEM_PROMPT` in `pipeline/agents/extraction.py`, `SETTINGS_PATH` in `ui/server.py`).

**Types:**
- Enum class names use `PascalCase` (`TaskStatus`, `TaskFlag`, `LLMMode`, `JiraHierarchy` in `models/schemas.py`).
- Pydantic model names use `PascalCase` (`RunConfig`, `ManagedTask`, `JiraPushResult` in `models/schemas.py`; `ProcessingStatus` and request models in `ui/server.py`).

## Code Style

**Formatting:**
- Tool used: Not detected (`pyproject.toml`, `setup.cfg`, `.flake8`, `ruff.toml`, and formatter configs are not present at repository root).
- Key settings: Not applicable; style is enforced by existing code patterns rather than config.
- Follow existing spacing and readability conventions: blank lines between logical blocks and concise inline comments where needed (for example in `pipeline/orchestrator.py`, `integrations/jira_client.py`, `ui/server.py`).

**Linting:**
- Tool used: Not detected (no lint config files in repository root).
- Key rules: Implicitly follow current code patterns:
1. Prefer explicit type hints on public interfaces (`pipeline/orchestrator.py`, `pipeline/llm_client.py`, `audit/logger.py`).
2. Use docstrings on classes/high-value methods (`models/schemas.py`, `pipeline/agents/extraction.py`, `integrations/jira_client.py`).
3. Keep side effects explicit with clear logging around major operations (`pipeline/orchestrator.py`, `ui/server.py`).

## Import Organization

**Order:**
1. Standard library imports (`os`, `json`, `threading`, `Path`) as seen in `main.py`, `ui/server.py`, `pipeline/observability.py`.
2. Third-party imports (`fastapi`, `pydantic`, `loguru`, `litellm`, `jira`) as seen in `ui/server.py`, `pipeline/llm_client.py`, `integrations/jira_client.py`.
3. Local project imports (`models.schemas`, `pipeline.*`, `audit.logger`) as seen in `main.py`, `pipeline/orchestrator.py`, `pipeline/agents/extraction.py`.

**Path Aliases:**
- Not detected. Use direct package-relative imports like `from models.schemas import ...` and `from pipeline.orchestrator import PipelineOrchestrator` (`main.py`, `ui/server.py`).

## Error Handling

**Patterns:**
- Wrap network/API/IO boundaries in `try/except`, log failure, and return structured fallback data where possible.
- Raise domain-specific exceptions for blocking failures (for example `RuntimeError` in `main.py` and `pipeline/llm_client.py`, `ValueError` in `integrations/jira_client.py`).
- Convert backend failures to HTTP errors in API routes (`ui/server.py` uses `HTTPException` with explicit status codes).
- Preserve pipeline continuity by recording recoverable failures and returning empty/default values (`pipeline/agents/extraction.py`, `ui/server.py`).

## Logging

**Framework:** `loguru` (configured in `pipeline/observability.py`), with audit persistence via SQLite in `audit/logger.py`.

**Patterns:**
- Use contextual logging via `logger.contextualize(agent=..., run_id=...)` for correlated tracing (`pipeline/llm_client.py`, `integrations/jira_client.py`).
- Log lifecycle checkpoints and step progress in orchestrated flows (`pipeline/orchestrator.py`, `main.py`).
- Emit warnings for recoverable fallbacks and errors for terminal failures (`integrations/jira_client.py`, `pipeline/observability.py`, `ui/server.py`).

## Comments

**When to Comment:**
- Add short comments for operational intent and non-obvious behavior:
1. Migration/backward-compatibility logic (`ui/server.py` startup migration block).
2. Platform/API compatibility notes (`integrations/jira_client.py` Next-Gen parent linking notes).
3. Prompt and extraction rule intent (`pipeline/agents/extraction.py`).

**JSDoc/TSDoc:**
- Not applicable.
- Python docstrings are used for classes and key methods (`models/schemas.py`, `pipeline/llm_client.py`, `audit/logger.py`).

## Function Design

**Size:** 
- Core orchestration functions can be large and step-oriented (for example `PipelineOrchestrator.run` in `pipeline/orchestrator.py`).
- Helper methods stay focused on one responsibility (`_build_labels`, `_resolve_issue_type` in `integrations/jira_client.py`; `_extract_models_from_response` in `ui/server.py`).

**Parameters:**
- Use typed parameters and explicit defaults for behavior controls (`confidence_threshold` in `pipeline/agents/extraction.py`, `max_nodes` in `ui/server.py`, optional session IDs in API helpers).

**Return Values:**
- Prefer typed structured returns over raw tuples:
1. Pydantic models (`JiraPushResult`, `ManagedTask` in `models/schemas.py`).
2. JSON-like dict/list payloads for API and file persistence (`ui/server.py`, `pipeline/orchestrator.py`).

## Module Design

**Exports:**
- Use direct symbol imports from module files; no central export map.
- Keep domain separation by module path:
1. `models/` for schemas and enums (`models/schemas.py`).
2. `pipeline/` for execution and agent logic (`pipeline/orchestrator.py`, `pipeline/agents/*.py`, `pipeline/llm_client.py`).
3. `integrations/` for Jira push clients (`integrations/jira_client.py`, `integrations/jira_mcp_client.py`).
4. `ui/` for FastAPI server (`ui/server.py`).

**Barrel Files:**
- Limited usage. `pipeline/agents/__init__.py` exists but core imports generally target concrete modules directly.

---

*Convention analysis: 2026-03-30*
