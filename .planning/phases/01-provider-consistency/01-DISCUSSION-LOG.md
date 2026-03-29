# Phase 1: provider-consistency - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-03-30
**Phase:** 01-provider-consistency
**Areas discussed:** Configuration Scope, Crypto/Settings Logic, Failure Behavior, Model Discovery

---

## Configuration Scope

| Option | Description | Selected |
|--------|-------------|----------|
| Immutable per-run config | Pass immutable config objects through the pipeline | ✓ |
| Thread-local context | Use ContextVars to scope configuration | |
| Mutex global env | Add locking to existing global os.environ mutation | |

**User's choice:** Immutable per-run config
**Notes:** User requested explanation of choices; recommended to avoid global state mutation to support concurrent runs securely.

---

## Crypto/Settings Logic

| Option | Description | Selected |
|--------|-------------|----------|
| Dedicated settings module | Extract a dedicated config/settings.py module | ✓ |
| Orchestrator-owned | Move into pipeline orchestrator and expose via API | |
| Keep duplicated | Keep duplicated but write strict tests | |

**User's choice:** Dedicated settings module
**Notes:** Decided to extract shared logic from `ui/server.py` and `pipeline/llm_router.py` into a central module.

---

## Failure Behavior

| Option | Description | Selected |
|--------|-------------|----------|
| Fail fast and loud | Throw explicit error/500 on corruption | ✓ |
| Backup and reset | Rename corrupted file to .bak and reset | |
| Silent defaults | Continue silently with empty defaults | |

**User's choice:** Fail fast and loud
**Notes:** User requested explanation; recommended over silent defaults to avoid hiding root causes when keys disappear.

---

## Model Discovery

| Option | Description | Selected |
|--------|-------------|----------|
| Async calls + cache | Use async httpx and cache results | ✓ |
| Background polling | Poll periodically in background task | |
| ThreadPool execution | Wrap synchronous requests in ThreadPoolExecutor | |

**User's choice:** Async calls + cache
**Notes:** User asked for production recommendation; explained that synchronous calls currently block the entire FastAPI thread.

## Claude's Discretion
- Specific cache duration for model discovery.
- Exact structure of the immutable run config object.

## Deferred Ideas
None
