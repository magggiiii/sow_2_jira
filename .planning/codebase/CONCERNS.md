# Codebase Concerns

**Analysis Date:** 2026-03-30

## Tech Debt

**Monolithic PageIndex utility module:**
- Issue: `pageindex/utils.py` combines unrelated responsibilities (LLM calls, PDF parsing, JSON extraction, tree transforms, logging, config loading) in a single 700+ line module, which raises change risk and coupling.
- Files: `pageindex/utils.py`, `pageindex/page_index.py`, `pageindex/page_index_md.py`
- Impact: Small edits can create regressions in unrelated parts of indexing; hard to unit test individual behavior.
- Fix approach: Split `pageindex/utils.py` into focused modules (`llm_io`, `pdf_io`, `structure_ops`, `config`) and add unit tests per module boundary.

**Duplicated settings/crypto logic across layers:**
- Issue: Fernet key and settings handling are duplicated in both UI and router code paths.
- Files: `ui/server.py`, `pipeline/llm_router.py`
- Impact: Behavior drift risk (e.g., one path updates env assignment/validation while the other does not).
- Fix approach: Centralize settings/key loading in a single module used by both `ui/server.py` and `pipeline/llm_router.py`.

**Dead or low-value parser path retained:**
- Issue: `pipeline/parser.py` is implemented but not wired into the main orchestrator flow, while PageIndex is the active path.
- Files: `pipeline/parser.py`, `pipeline/orchestrator.py`
- Impact: Maintenance overhead and stale code risk if parser code diverges silently.
- Fix approach: Either remove unused parser flow or reintroduce it behind an explicit feature flag with tests.

## Known Bugs

**Potential KeyError in MCP story/sub-task flow:**
- Symptoms: Jira push can crash when story creation fails for a section.
- Files: `integrations/jira_mcp_client.py`
- Trigger: In `JiraHierarchy.STORY_SUBTASK`, `story_cache[section]` is indexed even when `_create_issue(...)` returns `None`.
- Workaround: Use non-MCP push path via `integrations/jira_client.py` for unstable projects.

**Cross-run tree cache collision when skipping indexing:**
- Symptoms: Tasks can be extracted from the wrong document when `skip_indexing` is enabled.
- Files: `pipeline/orchestrator.py`
- Trigger: `_build_or_load_tree` reads/writes a shared `data/document_tree.json` (`TREE_CACHE_PATH`) without namespacing by `run_id` or source PDF.
- Workaround: Avoid `skip_indexing` unless processing the same exact document.

**Broad JSON/settings failures silently converted to empty state:**
- Symptoms: Settings/session metadata corruption is masked and app continues with defaults, making root-cause diagnosis difficult.
- Files: `ui/server.py`, `pipeline/llm_router.py`
- Trigger: Bare `except` and `except Exception` blocks return `{}` or `pass` in `_load_settings` and env-application paths.
- Workaround: Manually inspect `data/settings.json` and service logs when settings seem to reset unexpectedly.

## Security Considerations

**Path traversal risk in session deletion endpoint:**
- Risk: Arbitrary directory deletion under process permissions.
- Files: `ui/server.py`
- Current mitigation: None in `/api/sessions/{run_id}`; it directly builds `Path(f"data/sessions/{run_id}")` and calls `shutil.rmtree(...)`.
- Recommendations: Validate `run_id` against strict regex (e.g., `^[a-zA-Z0-9-]+$`), resolve canonical path, and enforce parent directory containment before delete.

**Path traversal/overwrite risk in upload endpoint:**
- Risk: Untrusted filename may escape `data/uploads` via path components.
- Files: `ui/server.py`
- Current mitigation: Extension check (`.pdf`) only.
- Recommendations: Normalize with `Path(file.filename).name`, reject separators/absolute paths, and generate server-side filenames.

**Shell command injection and token exposure surface in MCP wrapper:**
- Risk: Secrets passed in shell-constructed command string; unsafe interpolation can break quoting and potentially permit command injection through malformed values.
- Files: `integrations/jira_mcp_client.py`
- Current mitigation: None beyond environment sourcing.
- Recommendations: Avoid `sh -c`/string interpolation; invoke command with explicit arg list, pass secrets via env/stdin, and avoid embedding tokens in command text.

**No API authentication on operational endpoints:**
- Risk: Any local caller can trigger pipeline execution, settings changes, and Jira push operations.
- Files: `ui/server.py`
- Current mitigation: localhost bind in default deployment (`docker-compose.yml`) and CORS origin list.
- Recommendations: Add authentication/authorization (token or session auth), and gate sensitive endpoints (`/api/settings`, `/api/process`, `/api/push`, `/api/sessions/*`).

## Performance Bottlenecks

**Quadratic pairwise dedup candidate generation:**
- Problem: Dedup computes all pair comparisons (`O(n^2)`) before LLM confirmation.
- Files: `pipeline/agents/deduplication.py`
- Cause: Nested loops over full task list with cosine dot product per pair.
- Improvement path: Use ANN/top-k candidate retrieval or blocking strategy before pairwise scoring.

**Repeated full-file writes in JSON logger:**
- Problem: Every log call rewrites full `log_data` array to disk.
- Files: `pageindex/utils.py`
- Cause: `JsonLogger.log` appends in-memory list then writes complete JSON each call.
- Improvement path: Switch to append-only JSONL logging with periodic compaction.

**Synchronous, per-request external model discovery calls in API thread:**
- Problem: `/api/providers/{provider_id}/models` performs blocking `requests.get(...)` in request path.
- Files: `ui/server.py`
- Cause: Sync network calls in FastAPI app without async/httpx usage.
- Improvement path: Use async HTTP client with timeout/retry policy and short-lived cache for model lists.

## Fragile Areas

**Environment mutation as global state:**
- Files: `ui/server.py`, `pipeline/llm_router.py`, `pipeline/llm_client.py`
- Why fragile: Request-time settings writes mutate `os.environ` globally; concurrent runs can influence each other’s provider/model configuration.
- Safe modification: Replace env mutation with immutable per-run config objects passed into clients.
- Test coverage: No automated tests validate concurrent run isolation.

**Audit logger concurrency model:**
- Files: `audit/logger.py`, `ui/server.py`, `pipeline/orchestrator.py`
- Why fragile: Single SQLite connection with `check_same_thread=False` is shared without explicit locking; mixed thread writes may contend unpredictably.
- Safe modification: Use per-thread connections or serialized write queue with retry/backoff.
- Test coverage: No contention or concurrency tests detected.

**Silent exception swallowing across critical flows:**
- Files: `ui/server.py`, `pageindex/utils.py`, `pipeline/observability.py`, `pipeline/llm_router.py`
- Why fragile: Multiple `except Exception: pass` patterns hide failures in crypto/env/telemetry and parsing logic.
- Safe modification: Narrow exception types and emit structured error logs with context.
- Test coverage: No tests assert failure-path behavior.

## Scaling Limits

**Task volume limit by fixed node cap:**
- Current capacity: Default `max_nodes` is 200 (`RunConfig.max_nodes`), configurable per request.
- Limit: Pipeline aborts with runtime error when node count exceeds cap.
- Scaling path: Use adaptive chunking/hierarchical batching and progressive extraction rather than hard stop.

**In-memory run/session state only:**
- Current capacity: `active_runs` and `active_orchestrators` are process-local dicts.
- Limit: Restart loses active state; horizontal scaling cannot share run control.
- Scaling path: Move run status/control to shared store (Redis/DB) and decouple workers from API process.

## Dependencies at Risk

**Unpinned/latest container image for Ollama:**
- Risk: `ollama/ollama:latest` can introduce unpredictable runtime changes.
- Impact: Reproducibility and stability regressions across deployments.
- Migration plan: Pin to tested image digest/tag in `docker-compose.ollama.yml`.

**Heavy ML dependency loaded at runtime for dedup:**
- Risk: `sentence-transformers` model fetch/load can fail or be slow in constrained/offline environments.
- Impact: Dedup latency spikes or fallback behavior that keeps duplicates.
- Migration plan: Pre-bundle model artifacts or provide deterministic lightweight local embedding fallback.

## Missing Critical Features

**Automated regression test suite for core pipeline:**
- Problem: Only two manual connectivity scripts exist; no automated coverage for extraction/state/dedup/orchestrator/UI endpoints.
- Blocks: Safe refactors, concurrency hardening, and confidence in bug fixes.

**Input validation and auth hardening for mutation endpoints:**
- Problem: Sensitive API operations lack strict input sanitization and authentication.
- Blocks: Safe multi-user/local-network deployment and secure automation.

## Test Coverage Gaps

**Core pipeline orchestration untested:**
- What's not tested: End-to-end flow (`index -> extraction -> state -> dedup -> save`) and stop/cancel behavior.
- Files: `pipeline/orchestrator.py`, `pipeline/indexer.py`, `pipeline/agents/*.py`
- Risk: Behavioral regressions in task lifecycle and coverage accounting go unnoticed.
- Priority: High

**Security-critical API handlers untested:**
- What's not tested: Upload path handling, session deletion safety, settings encryption/decryption flows, and Jira push endpoints.
- Files: `ui/server.py`
- Risk: Path traversal and unsafe state mutation bugs can ship undetected.
- Priority: High

**Integration clients failure-path behavior untested:**
- What's not tested: Jira API/MCP retries, fallback logic, and partial-failure mapping to task statuses.
- Files: `integrations/jira_client.py`, `integrations/jira_mcp_client.py`
- Risk: Production push failures may corrupt status or silently drop tasks.
- Priority: Medium

**Observability and telemetry buffering not validated:**
- What's not tested: Queue backpressure, replay behavior, and exporter failure handling.
- Files: `pipeline/observability.py`, `pipeline/telemetry.py`
- Risk: Lossy telemetry and hidden operational failures.
- Priority: Medium

---

*Concerns audit: 2026-03-30*
