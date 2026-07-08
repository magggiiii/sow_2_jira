# SOW-to-Jira — Master Elevation Roadmap

**From:** local-first single-user prototype (~5,900 LOC, Python 3.11 / FastAPI / LiteLLM, all state on local disk, no auth, in-process pipeline)
**To:** hosted multi-user SaaS on Render for one org (calibraint), row-level isolation by `user_id`, per-user encrypted credentials, hosted-API LLM providers only, Postgres + object storage, durable background worker, Langfuse Cloud observability.

This document sequences the whole effort into phased **waves**. Each wave lists its goal, work items (traceable to the audit findings and Render designs), dependencies, rough effort, and a concrete *done-when* gate. Render-deployment blockers are tagged **[RENDER-BLOCKER]**; quality/polish items are tagged **[QUALITY]**.

---

## ⚡ Top quick win (do this first, today)

**Add a `.dockerignore` and stop secrets baking into the image.** There is no `.dockerignore`, and the `Dockerfile` does `COPY . .`, so the on-disk `.env` and a real `ghp_…` GitHub token in `.github_token` (41 bytes) get copied into every image layer — even though `.github_token` is correctly gitignored and *not* in git history. This is a ~10-minute change that closes a live credential-leak path before any Render build pulls the repo.

```
# .dockerignore
.env
.env.*
.github_token
data/
.git/
venv/
*.log
telemetry_queue.jsonl
__pycache__/
```

Then: **rotate the GitHub PAT and every token currently in the on-disk `.env`** (Bifrost/ZAI/Jira/Langfuse), since they have been sitting in a shared build context. This single action is the highest leverage-to-effort item in the entire plan. Effort: **S**.

> Note on the brief: the brief said `.github_token` is "committed in repo root." Verified — it is gitignored and *not* tracked by git, but it does exist on disk and would leak via `COPY . .`. The fix (`.dockerignore` + rotation) is the same either way; a history scrub is only needed if it was ever committed (it wasn't here).

---

## Wave 0 — Hardening & safety net (no behavior change)

**Goal:** make the repo safe to deploy-from and give every later wave a green CI gate to land against. Nothing here changes product behavior; it makes everything after it provable.

| # | Work item | Source | Tag |
|---|-----------|--------|-----|
| 0.1 | Add `.dockerignore`; rotate leaked `.env`/`.github_token` secrets; bind to `$PORT` not hardcoded `8000` (server.py:719) | security findings; topology design | **[RENDER-BLOCKER]** |
| 0.2 | Fix the dead `/api/push` path: `JiraClient` is used but never imported (server.py:664) → guaranteed `NameError` on every push | web_api: "Jira push handler references JiraClient with no import" | **[RENDER-BLOCKER]** |
| 0.3 | Pin all dependencies + commit a lockfile (uv/pip-tools); swap deprecated `PyPDF2` → maintained `pypdf`; add `pip-audit` | security: "Fully unpinned dependencies + deprecated PyPDF2" | **[RENDER-BLOCKER]** |
| 0.4 | Make the default test command green: gate the langchain-only judge import behind `pytest.importorskip` (or port judge to `llm_client`), add `pytest.ini`/`pyproject.toml` with `testpaths = tests`, `--strict-markers` | testing: "Default test command is red on a clean checkout" | **[QUALITY]** |
| 0.5 | Add GitHub Actions CI: `pytest --cov --cov-fail-under=<current>` + `ruff` lint/format + `pip-audit` on push/PR; add `make test`/`make lint` | testing: "No CI, no coverage gate, no lint" | **[RENDER-BLOCKER]** |
| 0.6 | Add a CI import-lint (ruff F821 / pyflakes) so undefined-name bugs like 0.2 fail the build | web_api finding 0.2 elevation | **[QUALITY]** |
| 0.7 | Rename root `test_*.py` smoke scripts to `scripts/check_*.py` so pytest stops auto-collecting live-credential scripts | testing: "Root-level test_*.py scripts" | **[QUALITY]** |

**Dependencies:** none — this is the foundation.
**Effort:** **M**
**Done = TRUE when:** `.dockerignore` exists and a built image contains no `.env`/`.github_token`; all prior leaked secrets are rotated; `pytest tests/` is green on a clean checkout; CI runs on every PR with a coverage floor and lint gate; `/api/push` no longer raises `NameError` (covered by a smoke test).

---

## Wave 1 — Data + secrets platform (Postgres, object storage, per-user crypto)

**Goal:** stand up the relational backbone and encrypted credential store that *every* multi-user feature depends on. The pipeline still runs locally/in-process at the end of this wave — we are only moving where state and secrets live, not how jobs execute.

| # | Work item | Source | Tag |
|---|-----------|--------|-----|
| 1.1 | Add `sqlalchemy[asyncio]`, `asyncpg`, `alembic`, `boto3`; provision Render Postgres + Cloudflare R2 bucket | data_layer design | **[RENDER-BLOCKER]** |
| 1.2 | Create `pipeline/db.py` (async engine, small pool: `pool_size≈3-5`, `pool_pre_ping`, `pool_recycle=300`) and ORM models: `users`, `user_credentials`, `runs`, `tasks`, `coverage_reports`, `audit_log` — every tenant table carries `user_id` FK + index | data_layer design; persistence: "No user/tenant model" | **[RENDER-BLOCKER]** |
| 1.3 | Alembic baseline migration; wire `preDeployCommand: alembic upgrade head` | data_layer migration steps | **[RENDER-BLOCKER]** |
| 1.4 | `config/crypto.py`: `MultiFernet`-based `encrypt_secret`/`decrypt_secret` reading `APP_ENC_KEY` (+ optional `APP_ENC_KEY_OLD` for rotation); drop the `data/.keyfile` fallback entirely | secrets_obs design; security: "Weak Fernet key handling" | **[RENDER-BLOCKER]** |
| 1.5 | Rewrite `audit/logger.py` to insert rows into Postgres `audit_log` (add `user_id`, index `(run_id)`), replacing the shared cross-thread SQLite connection | persistence: "SQLite audit DB: shared cross-thread connection, no indexes" | **[RENDER-BLOCKER]** |
| 1.6 | Add `integrations/object_store.py` (boto3 → R2, key convention `users/{user_id}/runs/{run_id}/…`); route every `data/sessions/<run_id>/…` and `data/uploads/…` read/write through it | topology + data_layer; persistence: "All durable state on ephemeral local FS" | **[RENDER-BLOCKER]** |
| 1.7 | Orchestrator Step-5 persistence: replace the three `json.dump`-to-disk blocks (pipeline_output / coverage_reports / document_tree / node_index) with DB upserts (`run`, `tasks`, `coverage_reports`) + R2 uploads storing object keys | data_layer migration; orchestration: "All run state on local FS" | **[RENDER-BLOCKER]** |
| 1.8 | Make `run_id` a full UUID/ULID (drop `uuid4()[:8]`); generate in one Run factory, keep the readable `YYYYMMDD-filename` slug as a display column only | data_model: "Run identity is a truncated 8-char UUID"; persistence: "run_id collision" | **[RENDER-BLOCKER]** |
| 1.9 | One-shot `scripts/migrate_fs_to_pg.py`: import the existing `data/sessions/*` + `data/audit.db` + `data/settings.json` under a seed `calibraint-migration` user, routing legacy ACs through `normalize_acceptance_criteria` | data_layer migration steps | **[QUALITY]** |
| 1.10 | Switch all timestamps to tz-aware `datetime.now(timezone.utc)` via one helper (Postgres `timestamptz`) | data_model: "datetime.utcnow() deprecated/naive" | **[QUALITY]** |
| 1.11 | Centralize scattered persisted schemas (`SectionCoverageReport`, `MissedItem`, `CritiqueReport`, `ClassificationResult`) into `models/`; bound `ManagedTask.confidence` to `[0,1]`; add `extra='forbid'` on LLM-facing models | data_model: scattered schemas + missing invariants | **[QUALITY]** |

**Dependencies:** Wave 0 (CI to land against, secrets rotated).
**Effort:** **L**
**Done = TRUE when:** a pipeline run persists its run/tasks/coverage/audit rows to Postgres and its PDF/tree blobs to R2 (nothing durable on local disk); `audit_log` queries are `user_id`-scoped; credentials encrypt/decrypt through `APP_ENC_KEY` with no keyfile; the FS→PG migration script reproduces `task_count`/`coverage_pct` for the existing sessions.

---

## Wave 2 — Auth + multi-user isolation + durable worker

**Goal:** add identity and row-level isolation, and move the long-running pipeline off the web process onto a durable queue/worker. After this wave the app is genuinely multi-user and survives deploys/scaling. This is the structurally largest change and the core of the SaaS move.

### 2A — Auth & isolation

| # | Work item | Source | Tag |
|---|-----------|--------|-----|
| 2.1 | Google OAuth/OIDC via `authlib`, gated to `@calibraint.com` (`email_verified` + domain check server-side; `hd` is a UI hint only) | auth design | **[RENDER-BLOCKER]** |
| 2.2 | Server-side opaque sessions in Postgres (`sessions` table, store `sha256(token)`, HttpOnly/Secure/SameSite=Lax cookie); `current_user` FastAPI dependency | auth design | **[RENDER-BLOCKER]** |
| 2.3 | Apply `Depends(current_user)` to every data route; add `WHERE user_id = :current_user` to all run/task/credential queries; ownership check + 404 on `session_id`/`run_id` (kills the IDOR + path-traversal fallback) | security: "No auth", "No ownership checks", "Path traversal"; web_api findings | **[RENDER-BLOCKER]** |
| 2.4 | Per-user credentials: rewrite GET/POST `/api/settings` to read/write `user_credentials` rows; **delete all `os.environ['LITELLM_*'/'JIRA_*']` writes** (server.py, jira_client.py:33-42, jira_mcp_client.py); thread a per-run `RunCredentials`/`ProviderConfig` object into orchestrator/`LLMClient`/`JiraClient` | secrets_obs, auth, llm_routing, jira: "process-global credentials" | **[RENDER-BLOCKER]** |
| 2.5 | Harden uploads: server-side UUID filename under `users/{user_id}/`, validate `%PDF` magic bytes, enforce max content-length (413), stream to R2 | security + web_api: "File upload no size limit / path traversal" | **[RENDER-BLOCKER]** |
| 2.6 | Lock CORS to the single Render origin (fail startup if unset in prod); enumerate allowed headers; double-submit CSRF on `POST /api/settings` and `/api/push` | security + web_api: CORS findings | **[QUALITY]** |

### 2B — Durable execution

| # | Work item | Source | Tag |
|---|-----------|--------|-----|
| 2.7 | Add `arq` + `redis`; provision Render Key Value (`maxmemoryPolicy=noeviction`); web becomes enqueue-only | async_exec design; topology | **[RENDER-BLOCKER]** |
| 2.8 | `pipeline/jobs.py` + `pipeline/worker.py`: run `await asyncio.to_thread(orchestrator.run)`; rebuild `RunConfig`/creds from the DB row (not the request body); `_job_id=run_id` for idempotency; `max_tries=1` for runs, 3 for push | async_exec design | **[RENDER-BLOCKER]** |
| 2.9 | **Delete** `active_runs` / `active_orchestrators` / `MODEL_CACHE` dicts and the `BackgroundTasks`/`threading.Thread` push; `/api/status` reads Redis progress hash with Postgres fallback; `/api/cancel` sets a Redis `cancel:{run_id}` flag the worker polls (replacing the in-process `threading.Event`) | async_exec; orchestration/web_api: "in-process BackgroundTask", "global dicts" | **[RENDER-BLOCKER]** |
| 2.10 | SIGTERM-graceful worker + heartbeat: on startup, requeue/fail any `RUNNING` row with a stale heartbeat; accept that a mid-run deploy interrupts a run (no mid-pipeline resume yet) | async_exec risks; orchestration: "no resumability" | **[RENDER-BLOCKER]** |
| 2.11 | Per-user concurrency cap: refuse enqueue if the user already has a `QUEUED`/`RUNNING` run (protects LLM provider rate limits) | async_exec risks | **[QUALITY]** |
| 2.12 | Configure litellm global callbacks **once** at app/worker startup (FastAPI lifespan), not per-`LLMClient` (kills the concurrent-mutation race) | llm_routing: "Global litellm callbacks mutated per-client" | **[RENDER-BLOCKER]** |

**Dependencies:** Wave 1 (Postgres `users`/`runs`/`user_credentials`, R2, crypto).
**Effort:** **XL**
**Done = TRUE when:** unauthenticated requests get 401; user A cannot read/cancel/delete user B's run (integration test with two user_ids); two concurrent users use isolated LLM/Jira credentials with zero `os.environ` credential writes anywhere; a run survives a web-service restart (progress keeps advancing from the worker); cancel is honored at the next LLM checkpoint; a worker redeploy drains gracefully and re-picks or fails the in-flight run.

---

## Wave 3 — Intelligence-layer quality fixes (the Phase-12 bugs)

**Goal:** make the *output trustworthy*. The first real run (103-node SOW → 509 tasks) flagged 100% INCOMPLETE, did 0 dedup merges, and the critic guessed `conf=0.00` on 100% of flagged tasks. Fix the three reinforcing root causes plus the data-loss and isolation bugs so the platform carries a *working* intelligence layer. Per the audit, ideally land the core of this *before* cutover so we don't ship a broken engine.

| # | Work item | Source | Tag |
|---|-----------|--------|-----|
| 3.1 | **Root cause:** `complete_json` hard-codes `max_tokens=4096` and regex-scrapes JSON. Switch to provider JSON-mode (`response_format={'type':'json_object'}`/json_schema), make `max_tokens` per-call payload-sized, add `json_repair` fallback + one bounded re-ask on `JSONDecodeError` | intelligence + llm_routing: "complete_json max_tokens / no JSON mode" — the #1 highest-leverage quality fix | **[QUALITY]** |
| 3.2 | Dedup: batch candidate pairs (20-30/call) so no response truncates; fix lossy merge semantics (`keep_first/keep_second` must absorb content via `_merge_tasks`; remove dead `:368-370`; fix `merged_from`; order-preserving content dedup); detect "0 merges on >100 tasks" as a degraded signal | intelligence: "dedup zero-merges", "merge semantics buggy and lossy" | **[QUALITY]** |
| 3.3 | Orchestrator: **gate** coverage flagging on `checker_confidence >= min_confidence` + severity; flag at section/report level, not blanket per-task INCOMPLETE; defer coverage to *after* dedup+gap-recovery and filter `missed_items` run-wide against the final corpus by embedding tier (≥0.85 drop / 0.70-0.85 overlap / <0.70 uncovered); record coverage as advisory metadata | orchestration: "100% INCOMPLETE wiring bug"; intelligence: "coverage flag-bombs" | **[QUALITY]** |
| 3.4 | Critic: remove `LIKELY_DUPLICATE` (dedup owns it); gate **all** flagging (not just auto-fix) on `conf >= 0.5`; treat `conf=0.00` as "no opinion" = no flag; add few-shot calibration + required per-issue justification | intelligence + orchestration: "critic conf=0.00 mass-flags" | **[QUALITY]** |
| 3.5 | Stop the dedup re-run amplification: only dedup newly recovered tasks against the existing deduped set, not the whole set again | orchestration: "Full dedup re-run after gap recovery" | **[QUALITY]** |
| 3.6 | `CoverageTracker.get_gaps`: actually apply the `min_text_length` filter (currently ignored) so empty structural nodes never enter gap recovery | orchestration: "get_gaps ignores text length" | **[QUALITY]** |
| 3.7 | Embeddings/cross-run isolation: pin all-MiniLM into the Docker image (no runtime download) **or** move to a hosted embedding API; move embeddings + cross-run index to Postgres+`pgvector` keyed by `(user_id, project_key)` and filter search by `user_id` (fixes cross-user duplicate leakage) | intelligence: "local sentence-transformers + cross-run index break multi-user" | **[RENDER-BLOCKER]** |
| 3.8 | Per-run quality report surfaced in the UI: counts of EXTRACTION_ERROR/PARTIAL nodes, dedup merges, coverage tiers, critic flag rate; mark a run **DEGRADED** on thresholds (e.g. >5% node parse failures, or 0 merges on >100 tasks); convert silent agent no-ops into recorded degraded-stage events | intelligence + orchestration: "universal silent-swallow", "unstructured stage failures" | **[QUALITY]** |
| 3.9 | Eval harness in CI: golden 103-node SOW → expected task-count/merge-count bands + "well-covered section yields zero missed_items"; unify the judge onto `llm_client` (drop langchain); implement or delete the stubbed eval tests | testing: "Phase-12 bugs have no regression tests", "eval suite stubbed" | **[QUALITY]** |

**Dependencies:** can begin in parallel with Wave 1/2 at the agent level; 3.7 needs Wave 1 (Postgres/pgvector) and 3.8 needs Wave 1 (DB-backed run record). The audit recommends landing 3.1–3.6 before the Wave 4 cutover.
**Effort:** **L**
**Done = TRUE when:** on the same 103-node SOW, dedup performs >0 merges, INCOMPLETE rate is sane (not ~100%), the critic emits zero `conf=0.00` blanket flags, no acceptance criterion/source_ref is lost across any dedup branch (unit-tested), the eval harness passes in CI within its bands, and the run summary shows a DEGRADED flag when thresholds trip.

---

## Wave 4 — Render deploy + hosted observability cutover

**Goal:** ship the four-service Blueprint and replace the Argus/Bifrost fleet with Render-native logs + Langfuse Cloud. After this wave the app is live on Render.

| # | Work item | Source | Tag |
|---|-----------|--------|-----|
| 4.1 | `render.yaml` Blueprint: Web (enqueue-only) + Background Worker (Arq, plan `standard`/≥2GB for MiniLM+pymupdf) + Managed Postgres + Key Value + nightly GC Cron + `sow-shared` env group; single image role-switched by `SOW_ROLE` | topology design | **[RENDER-BLOCKER]** |
| 4.2 | Dockerfile entrypoint branch (web/worker/cron); bind `0.0.0.0:$PORT`; bake all-MiniLM into the image; `--graceful-timeout 30` | topology design | **[RENDER-BLOCKER]** |
| 4.3 | Normalize `DATABASE_URL` (`postgresql://` → `postgresql+psycopg://`/asyncpg) once at startup; use the **internal** connection string | topology risks | **[RENDER-BLOCKER]** |
| 4.4 | Rewrite `pipeline/observability.py`: single loguru → stdout JSON sink (`serialize=True`), bind `user_id`+`run_id`; **remove** traceloop-sdk, all `opentelemetry-*`, OTLP exporters, file sinks, `ARGUS_*`/`BIFROST_*`/`INSTANCE_ID`, `init_argus`, `SYNC_ENABLED` gate | observability: Argus-fleet-wrong, OTel-never-exports, SYNC_ENABLED findings | **[RENDER-BLOCKER]** |
| 4.5 | Add `langfuse` to requirements; instrument LLM calls in `llm_client.py` with `start_as_current_generation` tagged by `user_id`+`run_id` (gives per-user cost/token aggregation); fail-open on tracing outage | observability: "langfuse not in requirements", "OTel metrics never export" | **[RENDER-BLOCKER]** |
| 4.6 | Cost tracking: compute `litellm.completion_cost`, persist `{user_id, run_id, model, prompt/completion tokens, cost_usd}`; treat missing usage as warn not silent 0; add per-user budget check before the call | llm_routing: "No cost tracking" | **[QUALITY]** |
| 4.7 | Delete `infra/user` + `infra/admin` Argus compose stacks from the deploy path; rewrite/delete `verify-telemetry.py` (it ImportErrors against the current module) into a Render healthcheck | observability: "Argus fleet wrong", "verify-telemetry.py broken" | **[QUALITY]** |
| 4.8 | Telemetry scrubbing: replace the single-field denylist with an allowlist of safe fields; stop sending raw SOW/LLM content (`response_preview`) into traces/logs by default | observability: "Telemetry scrubbing leaks SOW content" | **[QUALITY]** |
| 4.9 | `scripts/gc_sessions.py` for the nightly Cron (purge expired R2 artifacts + orphan rows) | topology migration steps | **[QUALITY]** |
| 4.10 | `render blueprints validate`; one-off `alembic upgrade head`; smoke-test one end-to-end hosted run on an API provider (no Ollama) | topology migration steps | **[RENDER-BLOCKER]** |

**Dependencies:** Waves 1–2 (DB, R2, worker, auth, secrets) — and Wave 3.1–3.6 strongly recommended first.
**Effort:** **L**
**Done = TRUE when:** the Blueprint validates and deploys all four services; a full SOW→tasks→Jira-push run completes end-to-end on a hosted API provider; logs appear in Render with `user_id`+`run_id`; LLM traces with per-user cost appear in Langfuse Cloud; no Argus/Bifrost/OTel code or compose remains in the deploy path; no raw SOW content leaks into traces.

---

## Wave 5 — Cleanup, Jira robustness & polish

**Goal:** delete dead local-first code, make Jira push production-grade (idempotent, rate-limit-aware), and finish UI/observability polish. Quality, not blockers.

| # | Work item | Source | Tag |
|---|-----------|--------|-----|
| 5.1 | Jira idempotency: persist `jira_issue_key` per task on success; skip already-keyed tasks on re-push; make push resumable (no duplicate issues after SIGTERM mid-push) | jira: "No idempotency"; jira render_blockers | **[QUALITY]** |
| 5.2 | Jira 429/Retry-After + 5xx backoff with bounded concurrency (3-5) instead of a serial loop | jira: "No rate-limit/429 handling" | **[QUALITY]** |
| 5.3 | Surface container (Epic/Story) creation failure as a structured `hierarchy_degraded` flag instead of silently flattening; retry container create once on 429/5xx | jira: "Container failure silently degrades hierarchy" | **[QUALITY]** |
| 5.4 | Fix inverted issue-link direction (`inwardIssue`/`outwardIssue` for Blocks); unit-test link direction per kind | jira: "Issue-link direction inverted" | **[QUALITY]** |
| 5.5 | **Delete dead local-first code:** `LLMMode.LOCAL`/Ollama unlimited-retry loop + 3600s timeout, ZAI/Bifrost header injection, `_ensure_docker_host`, the MCP Jira client (npx-shell-out), Ollama discovery in `server.py`/`main.py`; remove `ollama` from the provider registry; drop the corresponding tests (`test_routing.py`, LOCAL branches) | llm_routing + jira + testing + data_model: dropped-feature findings | **[QUALITY]** |
| 5.6 | Per-node/per-stage result records + structured failure inventory in run status; emit as metrics to Langfuse/Render dashboards | orchestration: "Stage failures unstructured" | **[QUALITY]** |
| 5.7 | UI: data-driven step count (the `/6` is hard-coded though the pipeline has ~9 stages); persist logs/status to DB; SSE or DB-backed polling so status survives multi-instance | web_api: "Hard-coded 6-step progress" | **[QUALITY]** |
| 5.8 | Move all pipeline tuning knobs from process-global env vars into `RunConfig` (per-user/per-run overridable); build fresh agent instances per `run()` so no provider/model state leaks between runs | orchestration: "__init__ heavy work / env+app_config split" | **[QUALITY]** |
| 5.9 | Tighten LLM error classification onto typed litellm exceptions instead of substring matching; cap honored `Retry-After` to a product SLA | llm_routing: "brittle string matching", "fixed timeout / retry budget" | **[QUALITY]** |
| 5.10 | Tests for the new SaaS surface: FastAPI TestClient route tests (401 when unauth, cross-user isolation), one orchestrator integration test against a stubbed LLM, Jira client tests against a mocked transport; document `APP_ENC_KEY` rotation procedure | testing render_blockers; secrets rotation risk | **[QUALITY]** |

**Dependencies:** Waves 1–4.
**Effort:** **M**
**Done = TRUE when:** a re-push creates zero duplicate issues; a 509-task push survives rate limits without permanent half-push; no `LLMMode.LOCAL`/Ollama/Bifrost/MCP-Jira code remains; the UI step count matches the real pipeline; route-level auth/isolation tests pass in CI; a key-rotation runbook exists.

---

## At-a-glance

| Wave | Goal | Effort | Unblocks Render? | Done-when (1-liner) |
|------|------|--------|------------------|---------------------|
| **Quick win** | `.dockerignore` + rotate secrets | S | **Yes (blocker)** | No secret in image; tokens rotated |
| **0 — Hardening** | Safe-to-deploy repo + CI gate | M | **Yes (blocker)** | Green CI, fixed push import, pinned deps, no secret leak |
| **1 — Data + secrets** | Postgres + R2 + per-user crypto | L | **Yes (blocker)** | All state in Postgres/R2, creds encrypted via `APP_ENC_KEY` |
| **2 — Auth + worker** | Identity, isolation, durable jobs | XL | **Yes (blocker)** | 401 unauth, cross-user isolation, runs survive restart/cancel |
| **3 — Intelligence quality** | Fix Phase-12 output bugs | L | Mostly **[QUALITY]** (3.7 blocker) | Merges>0, sane INCOMPLETE rate, no `conf=0.00` flags, evals green |
| **4 — Render deploy + obs** | Ship Blueprint + Langfuse | L | **Yes (blocker)** | 4 services live, hosted run works, traces in Langfuse |
| **5 — Cleanup + Jira polish** | Idempotent push, drop dead code | M | **[QUALITY]** | No dup issues, dead local-first code gone, SaaS tests pass |

**Critical path to a working hosted deploy:** Quick win → Wave 0 → Wave 1 → Wave 2 → Wave 4. **Wave 3** (except 3.7) is quality and can run in parallel with 1/2, but landing 3.1–3.6 before the Wave 4 cutover means the SaaS ships a *trustworthy* engine instead of the current flag-bomb/zero-merge behavior. **Wave 5** is post-launch hardening.
