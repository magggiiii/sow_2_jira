# Codebase Concerns

**Analysis Date:** 2026-05-11

## Tech Debt

**No dependency lockfile:**
- Issue: Only `requirements.txt` exists with loose lower-bound pins (`>=`) for most packages; no `poetry.lock`, `Pipfile.lock`, `pip-tools` output, or `requirements.lock`.
- Files: `requirements.txt`, `Dockerfile` (`pip install --prefix=/install --no-cache-dir -r requirements.txt`)
- Impact: Non-reproducible builds across environments. A future LiteLLM/Pydantic/Traceloop release can silently change behaviour of provider routing, schema validation, or telemetry between builds of the same Docker image.
- Fix approach: Pin via `pip-compile` (pip-tools) or migrate to `uv`/`poetry`. Pin minor versions for `litellm`, `pydantic`, `traceloop-sdk`, `opentelemetry-*`, `jira`, `sentence-transformers`. Commit the lock alongside `requirements.txt`.

**No formatter or linter configured:**
- Issue: No `pyproject.toml`, `setup.cfg`, `.flake8`, `ruff.toml`, `.black`, `pre-commit`, or `.pre-commit-config.yaml` at repo root. Style is enforced by convention only.
- Files: repo root (absence)
- Impact: Drift across modules (e.g. `pipeline/observability.py` uses comment box-drawing characters and emoji checkmarks; `audit/logger.py` is minimal); harder code review; PRs may regress on whitespace and import ordering.
- Fix approach: Add `ruff` (lint + format) configured in `pyproject.toml` with sensible defaults (line length 120). Add `pre-commit` hook. Run a single mass-format commit then enforce in CI.

**Vendored PageIndex fork:**
- Issue: VectifyAI PageIndex is checked into the repo at `pageindex/` instead of being depended on as a Python package, and `pipeline/indexer.py` injects `sys.path` to import it (`sys.path.insert(0, str(Path(__file__).parent.parent))`).
- Files: `pageindex/page_index.py`, `pageindex/page_index_md.py`, `pageindex/utils.py`, `pageindex/config.yaml`, `pipeline/indexer.py:15`
- Impact: No automatic security or bug-fix updates from upstream; vendored copy drifts silently; no version pin recorded. Any LiteLLM contract change inside PageIndex must be patched manually.
- Fix approach: Either (1) fork formally on GitHub and depend via pinned commit in `requirements.txt`, or (2) document the upstream commit + rationale for vendoring in a top-level `pageindex/UPSTREAM.md`. Add a quarterly review checklist.

**Two parallel Jira clients (REST + MCP):**
- Issue: `integrations/jira_client.py` (291 lines, direct REST via `jira` SDK) and `integrations/jira_mcp_client.py` (154 lines, Atlassian Rovo MCP via `npx @atlassian/mcp-remote`) implement the same `push_tasks` contract along diverging code paths.
- Files: `integrations/jira_client.py`, `integrations/jira_mcp_client.py`, `test_jira_api.py`, `test_jira_mcp.py`
- Impact: Bug fixes (e.g. parent-link fallback at `integrations/jira_client.py:233`) must be ported manually; the MCP path spawns `node -e '...'` shell wrapper, increasing surface area and making errors hard to debug. Hierarchy mappings (`Epic`/`Story`/`Sub-task`) are duplicated.
- Fix approach: Introduce a `JiraPushBackend` Protocol in `integrations/`; share `_build_description`, `_build_labels`, `_resolve_issue_type`, and hierarchy logic. Keep transport-specific code thin.

**Root-level standalone test scripts vs. real pytest suite:**
- Issue: `test_jira_api.py`, `test_jira_mcp.py`, `test_discovery.py`, `test_settings.py` live at repo root and mix two styles (some have `if __name__ == "__main__":` script entry points, some use `pytest.mark.asyncio`). The real suite lives under `tests/`.
- Files: `test_jira_api.py`, `test_jira_mcp.py`, `test_discovery.py`, `test_settings.py`, `tests/test_hierarchical_judge.py`, `tests/test_phase11_evals.py`, `tests/test_phase2_runtime_reliability.py`, `tests/test_routing.py`
- Impact: pytest auto-discovers everything matching `test_*.py` from the root and may run the script-style tests in environments that lack `.env`, causing flaky failures. New contributors are unclear where to put new tests.
- Fix approach: Move the root scripts into `scripts/manual_checks/` and rename without the `test_` prefix, or convert them to proper pytest tests under `tests/` with proper fixtures and mocks. Add `pytest` config `testpaths = ["tests"]` to pyproject.

**`PipelineOrchestrator.run()` is a 200+ line procedural method:**
- Issue: The single `run()` method in `pipeline/orchestrator.py` (lines 102-314) interleaves provider resolution, tree caching, 5 pipeline steps, telemetry emission, tracing spans, cancellation checks, and checkpoint writes.
- Files: `pipeline/orchestrator.py:102-314`
- Impact: High refactor risk; step boundaries are not testable in isolation; new steps (e.g. validation, hierarchy reshaping) require editing the monolith.
- Fix approach: Extract each `STEP_X_` block into a private method returning typed step results. Move per-step telemetry into a small `_emit_step_done(step, start_ts, **fields)` helper. Keep `run()` as a thin sequencer.

**Legacy migration code path lives in startup:**
- Issue: `ui/server.py:42-62` (`startup_event`) contains a one-shot migration from `data/pipeline_output.json` to `data/sessions/legacy-migration-YYYYMMDD/` that runs every process start.
- Files: `ui/server.py:42-62`
- Impact: Code that will never trigger on fresh installs still gets exercised on every boot. Confusing for new readers.
- Fix approach: Move to a dedicated `scripts/migrate_legacy_sessions.py` and remove the startup hook after a deprecation window.

**Loguru noise filter inside startup:**
- Issue: `PollingFilter` class is defined inline inside `startup_event` (`ui/server.py:73-77`) to suppress `/api/status` polling logs. Re-applied per worker boot; not configurable.
- Files: `ui/server.py:73-77`
- Impact: Hides legitimate failures of the status route in logs; harder to operate.
- Fix approach: Replace with a structured log level or a route-level decorator that emits status logs at DEBUG.

## Known Bugs

**`get_session_path` fallback returns a non-session path silently:**
- Symptoms: When `session_id` contains `..`, `/`, or `\`, the function returns `Path("data/pipeline_output.json")` and downstream `load_data` reads that file instead of returning an error.
- Files: `ui/server.py:165-168`
- Trigger: Crafted query string `/api/tasks?session_id=../etc` or missing session id.
- Workaround: Frontend always supplies a valid UUID, but the API does not enforce this.
- Fix: Raise `HTTPException(400)` for invalid session ids; the legacy fallback should be removed now that the migration ran.

**`config.api_base.rstrip("/v1").rstrip("/")` is per-character not per-suffix:**
- Symptoms: For an Ollama base URL like `http://host.docker.internal:11434/v1`, `.rstrip("/v1")` will strip any combination of `/`, `v`, `1` characters from the end — not the literal substring `/v1`. URLs containing trailing `1`s or `v`s on the host segment can be silently mangled.
- Files: `pipeline/llm_router.py:69`
- Trigger: Custom Ollama port or path ending in those characters.
- Workaround: Avoid trailing `/v1` in `OLLAMA_BASE_URL`.
- Fix: Replace with `if api_base.endswith("/v1"): api_base = api_base[:-3]` then `api_base = api_base.rstrip("/")`.

**`os.environ["JIRA_*"]` direct access raises `KeyError` on missing config:**
- Symptoms: `JiraClient.__init__` and `JiraMCPClient.__init__` crash with raw `KeyError` if env not set.
- Files: `integrations/jira_client.py:17, 25`, `integrations/jira_mcp_client.py:28-29, 43`
- Trigger: Push flow invoked before `_apply_settings_to_env_legacy` populates env (e.g. cold start without saved settings).
- Workaround: Configure Jira via UI before pushing.
- Fix: Use `os.environ.get(...)` and raise `ValueError("JIRA_SERVER not configured")` with a user-actionable message; surface as HTTP 400 in the route.

**`os.environ` mutation in route handlers persists across runs:**
- Symptoms: `save_settings` writes `JIRA_SERVER` and `JIRA_API_TOKEN` directly into `os.environ` (`ui/server.py:495, 499`). With multiple Gunicorn workers, only the worker that handled the save call sees the new value until next process restart.
- Files: `ui/server.py:495, 499`, `ui/server.py:89-111`
- Trigger: User updates Jira credentials in UI; subsequent push handled by a different worker still uses stale env.
- Fix: Read fresh from `SettingsManager` in `JiraClient`/`JiraMCPClient` constructors instead of relying on env globals.

## Security Considerations

**`.github_token` plaintext file at repo root:**
- Risk: A GitHub Personal Access Token (`ghp_...`) is checked out at `.github_token` in the working tree (visible via `git status` as untracked).
- Files: `.github_token` (gitignored at `.gitignore:7`, not in `git log --all -- .github_token`), `.gitignore.bak` lacks the entry
- Current mitigation: Listed in `.gitignore` (line 7). Git history shows no commit ever added it.
- Recommendations: (1) Confirm via `git log --all --source -- .github_token` that no prior branch leaked it; (2) **revoke and rotate the token immediately** since it sat untracked on disk with broad permissions; (3) move to a token loaded from `.env` or an OS keychain; (4) add a `pre-commit` secrets scanner (e.g. `gitleaks`, `detect-secrets`).

**Fernet key file beside the encrypted settings:**
- Risk: `data/.keyfile` (Fernet key) sits in the same directory as `data/settings.json` (encrypted secrets). Anyone with read access to `data/` can decrypt all stored API keys and Jira tokens.
- Files: `config/settings.py:85, 93-109`
- Current mitigation: `os.chmod(self.keyfile_path, 0o600)` is attempted (`config/settings.py:104`) but wrapped in `try/except OSError` so failures are silent (e.g. on Windows volumes).
- Recommendations: (1) Document that `data/` must not be world-readable in deployment guides; (2) prefer `SOW_FERNET_KEY` env-injected key for production so the key never lands on disk next to ciphertext; (3) log a warning when the `chmod` fails instead of silently swallowing.

**Encrypted secrets only — no plaintext fallback (good):**
- Risk: N/A — `SettingsManager.load` returns `{}` on missing file and raises `RuntimeError("Corrupted settings.json")` on parse failure (`config/settings.py:117-136`). Decryption failures bubble as exceptions through `_apply_settings_to_env_legacy` and are caught silently (`ui/server.py:91-93`) — secrets simply don't load.
- Files: `config/settings.py`, `ui/server.py:79-111`
- Current mitigation: Silent-skip on decrypt failure means a corrupted `data/.keyfile` does not leak plaintext.
- Recommendations: Surface decrypt failures to the UI ("Stored secrets unreadable — re-enter credentials") rather than silently dropping them; users currently see "no provider configured" with no actionable hint.

**Shell-wrapped MCP command builds via string interpolation:**
- Risk: `JiraMCPClient._async_push_tasks` builds a `node -e '...spawn("{base_cmd}", ...)...'` command containing `email` and `token` substituted via Python f-string (`integrations/jira_mcp_client.py:50-66`).
- Files: `integrations/jira_mcp_client.py:43-71`
- Current mitigation: Inputs come from env vars set by trusted code paths.
- Recommendations: If `JIRA_EMAIL` or `JIRA_MCP_API` ever contain shell metacharacters (quotes, `$`, backticks), the wrapper would inject. Pass credentials via `env=...` on the spawned process rather than interpolating into the shell command. Drop the `node -e` wrapper in favour of stdio filtering inside Python.

**PII scrubbing is a single-key denylist:**
- Risk: `pipeline/telemetry.py:8-13` scrubs only the `section_title` key from telemetry payloads. Other potentially sensitive fields (`title`, `short_description`, `use_case`, `deliverables`, `prompt_preview`) pass through.
- Files: `pipeline/telemetry.py:8-13`, `pipeline/llm_client.py:298` (span sets `prompt_preview` to first 1000 chars)
- Current mitigation: Remote sync defaults OFF (`ARGUS_SYNC_ENABLED=false`, `pipeline/observability.py:22`).
- Recommendations: Move to an allowlist for telemetry payloads, or run all string fields through a regex-based PII redactor (emails, AWS keys, JWTs) before emission. Document the scrubbing contract in `pipeline/telemetry.py`.

**Container runs non-root (good):**
- Risk: N/A — `Dockerfile:30-34` creates user `sow` (UID 1000), chowns `/app`, and runs the Gunicorn process as that user.
- Files: `Dockerfile:30-34`
- Current mitigation: Already in place.
- Recommendations: Pin UID via build arg so host volume mounts (for `data/`, `logs/`) can be chowned correctly by operators.

**CORS origin list comes from a `BETTER_AUTH_*` env var:**
- Risk: `ui/server.py:115-124` reads `BETTER_AUTH_TRUSTED_ORIGINS` (an unrelated naming carryover) defaulting to `http://localhost:8000,http://127.0.0.1:8000` and applies `allow_credentials=True` plus `allow_headers=["*"]`.
- Files: `ui/server.py:115-124`
- Current mitigation: Defaults are tight (localhost only).
- Recommendations: Rename the env variable to `SOW_TRUSTED_ORIGINS`; document it in `.env.example`. Restrict `allow_headers` to a known list.

## Performance Bottlenecks

**Pairwise O(n²) embedding similarity in deduplication:**
- Problem: `DeduplicationAgent.deduplicate` loops `for i in range(n): for j in range(i+1, n)` computing cosine similarity, then sends every above-threshold pair to the LLM.
- Files: `pipeline/agents/deduplication.py:89-95, 106-125`
- Cause: Simple Python loop and one LLM batch over all candidate pairs. With `n=300` tasks, that's 44,850 dot products in Python and a single very large prompt with all pairs serialised into JSON.
- Improvement path: Replace double loop with `embeddings @ embeddings.T` and `numpy.triu_indices` to vectorise (already use NumPy). Cap pairs sent to the LLM (top-k by similarity) and batch into multiple calls when count exceeds a threshold to avoid token blow-up.

**Lazy `SentenceTransformer` model load every run:**
- Problem: `DeduplicationAgent._get_embedder` lazy-loads `all-MiniLM-L6-v2` on first dedup call (`pipeline/agents/deduplication.py:55-58`); each new `PipelineOrchestrator` instance gets its own agent and re-downloads/reloads the model.
- Files: `pipeline/agents/deduplication.py:55-58`, `pipeline/orchestrator.py:59`
- Cause: No module-level cache; agents are constructed per run.
- Improvement path: Make `_embedder` a module-level singleton or class attribute initialised once per process. Add a startup warmup in `ui/server.py:startup_event` to amortise cost.

**LLM extraction is strictly sequential per node:**
- Problem: `PipelineOrchestrator.run` iterates nodes serially (`pipeline/orchestrator.py:180-214`) with a synchronous `litellm.completion` call per node.
- Files: `pipeline/orchestrator.py:180-214`, `pipeline/llm_client.py:343-346`
- Cause: Single-threaded design; rate-limited providers historically broke parallelism.
- Improvement path: Add bounded concurrency (semaphore = 4-8) using `concurrent.futures.ThreadPoolExecutor`; the existing retry logic already handles per-call rate limiting and `Retry-After` headers. Keep deduplication and gap recovery serial.

**Embedding text concat truncates at 200 chars only by snippet:**
- Problem: Dedup embedding text concatenates `title` + first 200 chars of `short_description` (`pipeline/agents/deduplication.py:67-71`); tasks that differ only in `acceptance_criteria` or `deliverables` collide.
- Files: `pipeline/agents/deduplication.py:60-71`
- Improvement path: Include a hash of structured fields or weight title 3x in the embedding input; raise threshold slightly to compensate.

## Fragile Areas

**LLM JSON parsing depends on regex post-processing:**
- Files: `pipeline/llm_client.py:499-512`
- Why fragile: `complete_json` strips Markdown fences, conversational preamble, and trailing content via three sequential regexes. Any LLM output with embedded `[` or `]` inside a quoted string (e.g. an `acceptance_criteria` item like `"[ ] foo"`) is preserved correctly only because the outer JSON wraps it, but a model that emits `Here is JSON: [...] Hope this helps.` after a structured block can still trip `last_bracket` heuristics.
- Safe modification: Add fixture tests in `tests/` capturing real-world LLM responses. Consider switching to LiteLLM's structured-output / JSON-mode for providers that support it.
- Test coverage: No dedicated tests for `complete_json` parsing edge cases.

**Cancellation via `stop_event` is cooperative and unevenly checked:**
- Files: `pipeline/orchestrator.py:184-187`, `pipeline/llm_client.py:56-61, 395-397, 418-420`, `pipeline/agents/extraction.py` (no cancellation check)
- Why fragile: Extraction, dedup, and gap-recovery loops don't check `stop_event` between LLM calls — cancellation only fires at the orchestrator's node-loop boundary and inside `_sleep_with_cancel`. An in-flight LLM call cannot be aborted mid-flight.
- Safe modification: Add `stop_event` parameter to `DeduplicationAgent.deduplicate` and `GapRecoveryAgent.recover` and check between iterations.
- Test coverage: No tests assert that cancellation halts dedup or gap recovery.

**ContextVar-based per-run provider config:**
- Files: `pipeline/orchestrator.py:113`, `models/schemas.py` (`current_provider_config`)
- Why fragile: Provider configuration is propagated via `current_provider_config.set(...)` per run. Background tasks spawned via FastAPI `BackgroundTasks` run in the worker's thread, and the ContextVar is set inside `run()` but never reset (`token` is captured but not used in a `try/finally` to reset). Concurrent runs in the same worker can race.
- Safe modification: Wrap `current_provider_config.set` in a `try/finally` that calls `current_provider_config.reset(token)` after `run()` returns or raises.
- Test coverage: `tests/test_routing.py` exists but does not assert isolation between concurrent runs.

**`_apply_settings_to_env_legacy` silently swallows decryption errors:**
- Files: `ui/server.py:89-111`
- Why fragile: `try/except: pass` blocks at `:91-93` and `:108-111` mean a corrupted Fernet key or rotated settings file results in "Jira/LLM unconfigured" with no log line.
- Safe modification: Replace `except: pass` with `except Exception as e: logger.warning(f"Could not load encrypted secret: {e}")`.

**Tree cache key collisions on re-run with `--skip-indexing`:**
- Files: `pipeline/orchestrator.py:82-100`
- Why fragile: Cache path is keyed only on `run_id`; if a user reuses a run_id across different PDFs (e.g. test_sow.pdf changes), the stale tree is reused without invalidation.
- Safe modification: Hash `pdf_path` content (or use `Path(pdf_path).stat().st_mtime`) in the cache filename. Invalidate when the source mtime differs.

## Scaling Limits

**Single-process in-memory run state:**
- Current capacity: `active_runs` and `active_orchestrators` dicts in `ui/server.py:162-163` hold all pipeline state in a single worker.
- Limit: Gunicorn workers each have their own copy. Polling `/api/status?session_id=X` from a load-balanced fronting may hit a worker that did not start that run and returns the empty default `ProcessingStatus()` (`ui/server.py:223-227`).
- Scaling path: Switch to a shared store (Redis or SQLite WAL) for status; or pin worker count to 1 in the Dockerfile CMD until this is fixed. The current CMD uses `-w 2` (`Dockerfile:41`).

**Audit DB connection is process-shared but not thread-safe across writers:**
- Current capacity: `AuditLogger._conn = sqlite3.connect(..., check_same_thread=False)` (`audit/logger.py:16`) allows multi-thread reuse; commits per `log()` call.
- Limit: SQLite serialises writes; concurrent runs across threads will serialise on a single connection's mutex. No connection pooling.
- Scaling path: One AuditLogger instance per run; or migrate to per-thread connections with `sqlite3.connect(..., isolation_level=None)` and explicit transactions.

**`max_nodes` denial-of-wallet guard fixed at 200:**
- Current capacity: `RunConfig.max_nodes` default 200 (`ui/server.py:236`).
- Limit: Larger SOWs raise `RuntimeError("Denial of Wallet Protection: PDF generated N sections, max allowed is 200")` (`pipeline/orchestrator.py:145-149`).
- Scaling path: Make the cap operator-configurable via env var and expose in settings UI; document the tradeoff (more nodes = more LLM cost).

## Dependencies at Risk

**`opendataloader-pdf` is imported but no longer central:**
- Risk: `requirements.txt:1` lists it as the first dependency, but the active document indexer goes through `pageindex/` which uses PyMuPDF/PyPDF2 directly.
- Impact: Dead-weight dependency with a large native footprint; longer builds and larger images.
- Migration plan: Confirm via `grep -r "opendataloader" pipeline/ pageindex/ ui/ integrations/`; if no runtime import remains, remove from `requirements.txt` and `Dockerfile` build deps.

**`traceloop-sdk` controls OTLP wiring globally:**
- Risk: `Traceloop.init` (`pipeline/observability.py:56-66`) is the only place the OTLP exporter is configured. Breakage in a new release of `traceloop-sdk` (loose `>=0.33.0` pin) silently drops all spans.
- Impact: Loss of Bifrost/Tempo/Loki observability without an alarm.
- Migration plan: Add a startup self-check that emits one span and verifies a 2xx OTLP response, raising a clear log warning when sync is enabled but no exporter is reachable. Pin to a known-good minor version.

**`mcp` SDK + `npx @atlassian/mcp-remote` runtime dependency:**
- Risk: MCP path requires Node.js + npm at runtime to spawn `npx -y @atlassian/mcp-remote`. The Dockerfile does **not** install Node.
- Impact: MCP Jira push silently fails inside the container (`npx: command not found`); only the REST client works in the published image.
- Migration plan: Either remove the MCP client entirely (REST path covers all cases), or add `nodejs npm` to the Dockerfile apt-install layer and pin the `@atlassian/mcp-remote` version.

**`sentence-transformers >= 2.7.0` pulls torch:**
- Risk: Indirect dependency on `torch` (multi-GB wheel) inflates image size for one usage (dedup embeddings).
- Impact: Dockerfile pip install layer is slow; cold-start downloads `all-MiniLM-L6-v2` weights.
- Migration plan: Either pre-bake the model into the Docker image (`RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"`) or move to a smaller embedder (`fastembed`, ONNX MiniLM) to drop torch entirely.

## Missing Critical Features

**No CI pipeline / no GitHub Actions workflow:**
- Problem: No `.github/workflows/` directory; tests are not enforced on PR.
- Blocks: Confidence in refactors; lockfile + lint enforcement; release-tag automation.

**No automated linting or formatting in pre-commit:**
- Problem: No `.pre-commit-config.yaml`.
- Blocks: Enforcing style without manual review.

**No request authentication on FastAPI routes:**
- Problem: All `/api/*` routes are public (CORS-restricted but unauthenticated). Anyone reaching `:8000` can read sessions, push to Jira, or rewrite settings.
- Blocks: Multi-user deployment, internet-exposed deployment. The product is local-first by design, but operators may not realise the implication.
- Workaround: Run behind an authenticating reverse proxy; document this requirement prominently in deployment docs.

**No rate limiting on `/api/upload` or `/api/providers/{provider_id}/models`:**
- Problem: PDF upload accepts arbitrary file sizes (`ui/server.py:323-332`); model discovery hits external providers and is cached but unbounded in concurrency.
- Blocks: Resilience against accidental flooding from the UI.

## Test Coverage Gaps

**LLM retry / backoff logic:**
- What's not tested: `extract_retry_hint`, `compute_wait_seconds`, `is_retryable_remote_error`, the `_execute_call` retry loop with `_sleep_with_cancel`.
- Files: `pipeline/llm_client.py:115-204, 389-477`
- Risk: A regression in retry-after parsing (e.g. mishandling an ISO date in the `Retry-After` header) would silently bypass provider rate limits and trigger bans.
- Priority: **High** — this is the most impactful runtime logic for the product.

**`PipelineOrchestrator.run` end-to-end:**
- What's not tested: The full step sequence with mocked indexer, LLM, and Jira client.
- Files: `pipeline/orchestrator.py`
- Risk: Step reordering or telemetry-emission changes can silently break downstream consumers (`active_orchestrators`, checkpoint format).
- Priority: **High**.

**Jira push parent-link fallback:**
- What's not tested: The retry-without-parent branch (`integrations/jira_client.py:233-258`).
- Risk: Next-Gen vs Classic project differences regress without notice.
- Priority: **Medium** — covered by `test_jira_api.py` script only (live, requires real Jira).

**MCP push path:**
- What's not tested: `integrations/jira_mcp_client.py` has no unit tests; `test_jira_mcp.py` requires npm + real Atlassian credentials.
- Risk: Stale MCP wrapper code in production image.
- Priority: **Low** if MCP is being deprecated; **High** otherwise.

**Settings encryption roundtrip on key rotation:**
- What's not tested: Behaviour when `data/.keyfile` changes but `data/settings.json` is from a previous key.
- Files: `config/settings.py:111-115`
- Risk: Users see "settings empty" without log explanation after a key rotation; they re-enter and may not realise old `settings.json` is now garbage.
- Priority: **Medium**.

**Observability initialisation when `ARGUS_SYNC_ENABLED=true` but collector unreachable:**
- What's not tested: `Traceloop.init` failure modes; OTLP exporter timeouts.
- Files: `pipeline/observability.py:50-82`
- Risk: Initialisation hang or thrown exception on startup if the collector sidecar isn't up yet.
- Priority: **Medium**.

## Operational

**Ollama-in-Docker requires explicit host binding:**
- Files: `pipeline/llm_router.py:55-69`, `config/settings.py:27-49` (`_ensure_docker_host`), `docker-compose.ollama.yml`
- Issue: When running Ollama on the host and the app in Docker, `OLLAMA_BASE_URL=http://localhost:11434` is rewritten to `http://host.docker.internal:11434` by `_ensure_docker_host`. This requires Ollama to be bound to `0.0.0.0` (not the default `127.0.0.1`) so the container can reach it. Users with default Ollama install hit silent connection failures.
- Mitigation: Document `OLLAMA_HOST=0.0.0.0:11434` requirement in install guides. Add a startup probe that pings `OLLAMA_BASE_URL` and logs a clear remediation line when unreachable.

**Compose files moved under `infra/` and installer paths must stay in sync:**
- Files: `infra/admin/docker-compose.admin.yml`, `infra/user/docker-compose.user.yml`, `scripts/install/install.sh`
- Issue: Top-level `docker-compose.yml`, `docker-compose.ollama.yml`, and `docker-compose.bifrost.yml` are referenced in `CLAUDE.md` but the actual layout is `infra/{admin,user}/`. The installer at `scripts/install/install.sh` is the single source of truth for compose paths.
- Mitigation: Add a CI check that greps compose-file references in docs against the actual `infra/` layout. Or move to a `compose.yaml` profile-based setup.

**`data/` and `logs/` are gitignored — fresh clones have no persistent state:**
- Files: `.gitignore:12-13`
- Issue: A fresh clone has no `data/audit.db`, no `data/settings.json`, no `data/.keyfile`, no uploads. First run must complete the settings flow before pushing to Jira. The CLI wizard at `main.py:49-98` does **not** prompt for LLM provider credentials — it assumes they exist in env or `data/settings.json`.
- Mitigation: Make `main.py` detect missing provider config and either prompt or fail with `"Run the UI to configure providers, then re-run main.py"`. Document the fresh-install flow in README.

**`Makefile clean` is destructive without confirmation:**
- Files: `Makefile:46-52`
- Issue: `make clean` deletes `venv/`, `data/*.db`, `data/*.json`, and `data/parser_output` — including `data/settings.json` (encrypted user credentials) and `data/audit.db` (run history).
- Mitigation: Split into `make clean` (just venv + caches) and `make purge` (full reset, with `@read -p "Type YES to delete all run data: "` confirmation).

**Healthcheck depends on `/api/status` returning 200 even on errors:**
- Files: `Dockerfile:38-39`, `ui/server.py:223-227`
- Issue: `/api/status` returns a default `ProcessingStatus()` for unknown `session_id`, so Docker always sees it healthy even if the pipeline is broken.
- Mitigation: Add a `/api/health` endpoint that checks settings load + Fernet key availability + sessions dir writability and use that in the healthcheck.

---

*Concerns audit: 2026-05-11*
