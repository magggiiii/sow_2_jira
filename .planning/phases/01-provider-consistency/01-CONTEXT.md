# Phase 01: provider-consistency - Context

**Gathered:** 2026-03-30
**Status:** Ready for planning

<domain>
## Phase Boundary

Provider switching, model execution, and persisted credentials behave deterministically across runs. Fix provider/model routing and credential persistence behavior.
</domain>

<decisions>
## Implementation Decisions

### Configuration Scope
- **D-01:** Stop modifying global environment variables (`os.environ`) for LLM credentials during runs. Instead, use an immutable per-run config object passed down through the pipeline to ensure concurrent extractions don't interfere.

### Crypto & Settings Logic
- **D-02:** Extract the currently duplicated Fernet encryption and settings logic from `ui/server.py` and `pipeline/llm_router.py` into a single, dedicated module (e.g., `config/settings.py`).

### Failure Behavior
- **D-03:** "Fail fast and loud." If the `settings.json` file is corrupted or unreadable, the system should throw an explicit error (e.g., 500 status code) and clearly alert the user, rather than silently catching the exception and proceeding with empty defaults.

### Model Discovery
- **D-04:** Use `httpx` (or a similar async HTTP client) to fetch provider model lists asynchronously, preventing the FastAPI main thread from blocking. Implement a short-lived cache to avoid redundant network calls.

### Claude's Discretion
- Specific cache duration for model discovery (e.g., 5-10 minutes).
- Exact structure of the immutable run config object.
</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Architecture & Concerns
- `.planning/codebase/ARCHITECTURE.md` — Identifies current pipeline orchestrator patterns and the need for per-run config.
- `.planning/codebase/CONCERNS.md` — Highlights the specific bugs with global `os.environ` mutation and duplicated crypto logic.
- `.planning/codebase/INTEGRATIONS.md` — Details the external LLM provider routing and authentication points.
</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `ui/server.py` — Current FastAPI endpoints that need updating to use the new settings module and async model discovery.
- `pipeline/llm_router.py` — Core LiteLLM configuration that must stop mutating global state.

### Established Patterns
- Pydantic models for run configuration (`models/schemas.py`), which is the ideal place to introduce the new immutable config object.

### Integration Points
- Any `LLMClient` initialization will need to accept the per-run configuration instead of relying solely on `os.environ`.
</code_context>

<specifics>
## Specific Ideas
No specific requirements — standard best practices for FastAPI, Pydantic, and async I/O apply.
</specifics>

<deferred>
## Deferred Ideas
None — discussion stayed within phase scope
</deferred>

---

*Phase: 01-provider-consistency*
*Context gathered: 2026-03-30*
