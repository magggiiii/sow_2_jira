# Phase 01: provider-consistency - Research

**Researched:** 2026-03-30
**Domain:** Provider configuration, credentials management, immutable run state, async API requests
**Confidence:** HIGH

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** Stop modifying global environment variables (`os.environ`) for LLM credentials during runs. Instead, use an immutable per-run config object passed down through the pipeline to ensure concurrent extractions don't interfere.
- **D-02:** Extract the currently duplicated Fernet encryption and settings logic from `ui/server.py` and `pipeline/llm_router.py` into a single, dedicated module (e.g., `config/settings.py`).
- **D-03:** "Fail fast and loud." If the `settings.json` file is corrupted or unreadable, the system should throw an explicit error (e.g., 500 status code) and clearly alert the user, rather than silently catching the exception and proceeding with empty defaults.
- **D-04:** Use `httpx` (or a similar async HTTP client) to fetch provider model lists asynchronously, preventing the FastAPI main thread from blocking. Implement a short-lived cache to avoid redundant network calls.

### the agent's Discretion
- Specific cache duration for model discovery (e.g., 5-10 minutes).
- Exact structure of the immutable run config object.

### Deferred Ideas (OUT OF SCOPE)
None — discussion stayed within phase scope
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| PROV-01 | Switch LLM provider dynamically without server restart | Moving state from `os.environ` to a per-run `ProviderConfig` passed into `LLMClient` will allow each run to use dynamic config without polluting the global environment. |
| PROV-02 | System uses provider-correct model identifiers | Centralizing settings logic in `config/settings.py` will guarantee the `_build_litellm_model` logic applies consistently and predictably across all pipeline components. |
| PROV-03 | Stored credentials load consistently after restart | Centralized Fernet encryption/decryption ensures key reading/writing operates exactly the same way during FastAPI startup and subsequent reads. |
| PROV-04 | Model list refresh clears stale options | Transitioning to async model discovery with `httpx` and a short-lived cache (5-10 min) will provide fast, up-to-date model lists. |
</phase_requirements>

## Summary

The current architecture mutates global `os.environ` variables (e.g., `LITELLM_API_KEY`, `LITELLM_PROVIDER`) to pass credentials down to `litellm`. This creates severe race conditions when multiple runs or API requests occur, leading to stale model/key carryover. Furthermore, the Fernet encryption logic and settings JSON loading are fully duplicated between `ui/server.py` and `pipeline/llm_router.py`.

**Primary recommendation:** Introduce a `config/settings.py` module to encapsulate settings persistence and encryption, add `httpx` to `requirements.txt` for async model discovery, and extend `models/schemas.py`'s `RunConfig` with a nested `ProviderConfig` to safely inject parameters into `LLMClient` initialization without touching `os.environ`.

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `pydantic` | >=2.0.0 | Typed settings schemas | Already heavily utilized in `models/schemas.py`. |
| `cryptography` | >=42.0.0 | Fernet symmetric encryption | Already in use, just needs deduplication. |
| `fastapi` | (current) | Web API / background tasks | Required framework for the system. |
| `httpx` | ^0.27.0 | Async HTTP client | Prevents blocking the main FastAPI thread during model discovery (D-04). |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `cachetools` | ^5.3.3 | TTL Cache | (Optional) Easy decorator for the 5-10 min model discovery cache. A simple dictionary with a timestamp also suffices. |

**Installation:**
```bash
# httpx needs to be explicitly added
echo "httpx>=0.27.0" >> requirements.txt
pip install httpx
```

## Architecture Patterns

### Recommended Project Structure
```text
config/
├── sow_config.json      # Existing logic rules
└── settings.py          # NEW: Centralized settings manager and Fernet logic
```

### Pattern 1: Centralized Settings Management
**What:** Move all Fernet initialization, encryption, decryption, and file I/O to a dedicated class or module.
**When to use:** Replacing the duplicated `_load_settings()` and `FERNET` initialization.
**Example:**
```python
# config/settings.py
import json
import os
from pathlib import Path
from cryptography.fernet import Fernet
from pydantic import BaseModel

class SettingsManager:
    def __init__(self, data_dir: str = "data"):
        self.settings_path = Path(data_dir) / "settings.json"
        self.keyfile_path = Path(data_dir) / ".keyfile"
        self._fernet = self._init_fernet()

    def load(self) -> dict:
        if not self.settings_path.exists():
            return {}
        try:
            with open(self.settings_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            # D-03: Fail fast and loud
            raise RuntimeError(f"Corrupted settings.json: {e}")
```

### Pattern 2: Immutable Per-Run Context
**What:** Instead of `os.environ["LITELLM_API_KEY"] = key`, add a `ProviderConfig` struct to `RunConfig` and pass it directly to `litellm.completion(api_key=...)`.
**Example:**
```python
# models/schemas.py
class ProviderConfig(BaseModel):
    provider: str
    model: str
    api_key: str = ""
    api_base: str = ""
    azure_api_version: str = ""

class RunConfig(BaseModel):
    # existing fields...
    provider_config: ProviderConfig
```

### Anti-Patterns to Avoid
- **Mutating `os.environ` at runtime:** `litellm` automatically reads from the environment, but it can also take arguments directly. Setting `os.environ` dynamically in a web server leads to thread cross-contamination.
- **Silent Exception Catching for Configs:** `ui/server.py` currently has `except Exception: return {}` for settings. This violates D-03 and hides corruption.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Async API requests | `requests` with threading/asyncio | `httpx.AsyncClient` | `httpx` is native async, integrating perfectly with FastAPI. |
| In-memory caching | Complex thread-safe dicts | Simple timestamp tuples or `cachetools` | Model discovery cache is simple; a global dict with expiration timestamps `(data, expiry_time)` is sufficient. |

## Runtime State Inventory

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data | `data/settings.json`, `data/.keyfile` | Refactor code to load/save from one unified module (`config/settings.py`). Existing data formats remain unchanged. |
| Live service config | None | None |
| OS-registered state | None | None |
| Secrets/env vars | `os.environ` usage in `ui/server.py`, `pipeline/llm_router.py`, `pipeline/llm_client.py` | Complete removal of runtime `os.environ` mutations for LLM configuration. Ensure `litellm.completion()` uses explicit kwarg injection. |
| Build artifacts | `__pycache__` | Standard cleanup. |

## Common Pitfalls

### Pitfall 1: Litellm's Implicit Env Fallbacks
**What goes wrong:** `litellm` automatically looks at `os.environ` if explicitly passed `api_key=None` or empty string.
**Why it happens:** Litellm's default behavior is to cascade: explicit kwargs -> os.environ -> error.
**How to avoid:** If the `ProviderConfig` dictates an empty key (e.g. local Ollama), ensure you explicitly manage litellm's expectations or ensure `os.environ` is scrubbed of old credentials on app startup if they exist.

### Pitfall 2: Blocking Main FastAPI Thread
**What goes wrong:** Calls to `/api/providers/{provider_id}/models` freeze the UI.
**Why it happens:** The endpoint currently uses the synchronous `requests.get()` without `await`, halting Uvicorn's async event loop.
**How to avoid:** Implement `async def get_provider_models()` and use `async with httpx.AsyncClient() as client: await client.get(...)`.

## Code Examples

### Explicit LiteLLM Injection
```python
# pipeline/llm_client.py
import litellm

def complete(self, prompt: str, ...):
    kwargs = {
        "model": self.provider_config.model,
        "messages": [{"role": "user", "content": prompt}],
    }
    
    # Inject directly to litellm instead of relying on env
    if self.provider_config.api_key:
        kwargs["api_key"] = self.provider_config.api_key
    if self.provider_config.api_base:
        kwargs["api_base"] = self.provider_config.api_base
        
    response = litellm.completion(**kwargs)
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `os.environ` mutation per request | Dependency Injection via Pydantic (`RunConfig`) | Phase 1 | Thread safety and predictable run behavior across providers. |
| Synchronous `requests` in FastAPI | `httpx.AsyncClient` | Phase 1 | Non-blocking model discovery endpoint, enabling responsive UI. |

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python | Runtime | ✓ | 3.11 (Docker) / 3.9 (Host) | — |
| `httpx` | Async Model Discovery | ✗ | — | Add `httpx>=0.27.0` to `requirements.txt`. |

**Missing dependencies with fallback:**
- `httpx`: Must be installed via `pip install httpx` and added to `requirements.txt` to safely support D-04.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | `pytest` (Not officially installed yet, ad-hoc python scripts exist) |
| Config file | none — see Wave 0 |
| Quick run command | `pytest tests/` |
| Full suite command | `pytest tests/` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| PROV-01 | Config resolves correctly without `os.environ` mutation | unit | `pytest tests/test_settings.py` | ❌ Wave 0 |
| PROV-03 | Corrupt `settings.json` raises explicit Exception | unit | `pytest tests/test_settings.py` | ❌ Wave 0 |
| PROV-04 | Async model discovery caches and returns results | unit | `pytest tests/test_discovery.py` | ❌ Wave 0 |

### Wave 0 Gaps
- [ ] Framework install: `pip install pytest pytest-asyncio` — Need to introduce `pytest` as there are only ad-hoc test scripts currently (`test_jira_api.py`, `test_jira_mcp.py`).
- [ ] `tests/test_settings.py` — covers PROV-01, PROV-03
- [ ] `tests/test_discovery.py` — covers PROV-04

## Sources

### Primary (HIGH confidence)
- `.planning/phases/01-provider-consistency/01-CONTEXT.md` - Core requirements and architectural directives (D-01 to D-04).
- Existing Source Code (`pipeline/llm_router.py`, `ui/server.py`, `pipeline/llm_client.py`) - Verified the duplication of Fernet and `os.environ` usage.

### Secondary (MEDIUM confidence)
- Official `httpx` documentation (general knowledge) regarding async client usage in FastAPI routes.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - `httpx` is the standard solution for FastAPI async outbound calls.
- Architecture: HIGH - Moving from global `os.environ` to explicitly passed Pydantic schemas is standard python engineering practice to prevent thread-safety issues.
- Pitfalls: HIGH - LiteLLM env fallback is a known behavior.

**Research date:** 2026-03-30
**Valid until:** 30 days
