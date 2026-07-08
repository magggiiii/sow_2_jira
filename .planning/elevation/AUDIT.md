# SOW-to-Jira — Elevation Audit

**Project:** SOW-to-Jira Portable Extraction Engine
**Stack:** Python 3.11 + FastAPI + LiteLLM (~5,900 LOC)
**Audit scope:** 10 subsystems, ahead of the locked elevation to a hosted multi-user SaaS on Render (single org `calibraint`, many users, row-level isolation by `user_id`).
**Date:** 2026-06-11

---

## 1. Executive Summary

SOW-to-Jira works end-to-end for a single user on a single machine, but it is a **local-first single-process prototype** wearing the architecture of a SaaS it was never built to be. Across all 10 audited subsystems the verdict is consistent: 8 of 10 areas are **POC**, 2 are **partial**, and none are production-ready for the hosted multi-user target. The good news is that the bones are typed and legible (Pydantic v2 contracts, decent retry/backoff in the LLM client, real agent unit tests), so the migration path is clear — but the gap between "runs on my laptop" and "runs for many users on Render" is structural, not cosmetic.

Four themes dominate the findings:

1. **Everything is process-global or local-disk, with no tenant.** Run state lives in in-process dicts (`active_runs`/`active_orchestrators`) and on the ephemeral local filesystem (`data/sessions/`, `data/audit.db` SQLite, `data/settings.json`, `data/.keyfile`, `data/project_indices/`). There is **no `user_id` anywhere** in the codebase — no User/Org/Credential/Run entity, no row-level isolation seam. On Render this means total data loss on every deploy, broken status/cancel across instances, and credentials shared process-wide so concurrent users overwrite each other's LLM/Jira keys. This single theme produces a critical finding in every persistence-touching subsystem.

2. **The pipeline runs in-process and is not durable.** `PipelineOrchestrator.run()` executes synchronously inside a FastAPI `BackgroundTask` thread, holding the full run in memory with no checkpointing or resumability. A multi-minute 103-node run dies on any Render deploy/SIGTERM/OOM with no way to resume — re-billing all LLM calls. This must move to a queue + Render Background Worker with state in Postgres/Redis. It is named as the single biggest structural change by three separate auditors.

3. **The intelligence layer — the product's core value — is broken in three reinforcing ways, all live in `main`.** The first real run (103 nodes → 509 tasks) produced **100% of tickets flagged INCOMPLETE**, **zero dedup merges**, and **critic conf=0.00 on 100% of flagged tasks**. The root cause of two of these is a single hard-coded `max_tokens=4096` cap in `complete_json` plus regex-scraped (not provider-enforced) JSON, which truncates long outputs mid-array; the broad `except` in every agent then swallows the failure as a silent no-op presented as success. Phase 12 documents credible fixes, but **none are implemented in code yet**.

4. **Security and secrets are trust-everything.** Zero auth on any endpoint, path traversal in the upload handler, no `.dockerignore` (so on-disk `.env` and a `ghp_` GitHub PAT bake into the image via `COPY . .`), fully unpinned dependencies including deprecated PyPDF2 parsing untrusted uploads, and a broken Jira push handler (`JiraClient` used but never imported — a live `NameError`). *Note: auditors disagree on whether `.github_token` is committed — the security auditor verified it is gitignored but present on disk; either way the image-bake path is the real risk.*

**Bottom line:** The platform move and the intelligence-layer quality fix are both in scope and both mandatory. The recommended sequence is to **fix the intelligence layer first** (so the migration carries a working engine, not a broken one), then re-architect persistence/execution/auth around Postgres + a background worker + per-user credentials.

---

## 2. Maturity Scorecard

| Area | Maturity | One-line |
|------|----------|----------|
| Pipeline core & orchestration | `poc` | Single linear in-process script; no resumability, no user scoping, hard-coded local paths, and two of the three quality bugs live in its wiring. |
| Intelligence agents (extraction/dedup/critic/coverage/…) | `poc` | Well-architected on paper but the first real run shipped garbage (100% INCOMPLETE, 0 merges, conf=0.00); Phase 12 fixes are docs only. |
| LLM routing & provider layer | `partial` | Strong retry/backoff engine, but process-global credentials, no provider JSON-mode, and dead Ollama/Bifrost/ZAI branches. |
| Web/API surface & UI | `poc` | Works for one local user; no auth, process-global state dicts, in-process background tasks, and a broken Jira push handler. |
| Jira integration | `poc` | Thoughtful partial-failure handling, but global-env credentials, zero idempotency (retries duplicate issues), no 429 handling, unwired MCP dead code. |
| Persistence & state model | `poc` | All durable state on ephemeral local disk; no DB, no tenant column, SQLite audit shared cross-thread, no migrations. |
| Observability & telemetry | `poc` | Local logging is solid; the entire Argus fleet/OTel-export half is overbuilt, partly non-functional, and wrong for a single hosted instance. |
| Testing & quality gates | `partial` | Good agent unit tests (100 pass), but the default command is red on checkout, no CI/coverage/lint, and zero tests above the agent layer. |
| Security & secrets | `poc` | Zero auth, shared-env credentials, upload path traversal, no `.dockerignore`, unpinned deps parsing untrusted PDFs. |
| Domain model & schemas | `partial` | Competent single-tenant Pydantic contracts, but no multi-tenant entities, free-text-where-enum-belongs, and plaintext creds dumped into run artifacts. |

---

## 3. CRITICAL & HIGH Findings (ordered by severity)

### CRITICAL

#### C-1 — Pipeline runs in-process as a FastAPI BackgroundTask; no isolation, durability, or cross-instance status
- **Area:** Orchestration / Web-API / Persistence / LLM-routing (cross-cutting)
- **Evidence:** `ui/server.py:241-302` (`run_pipeline_task` + in-memory `active_runs`/`active_orchestrators`), orchestrator instantiated and `.run()` called inline at `ui/server.py:288-290`; daemon-thread push at `ui/server.py:713`; PageIndex calls `asyncio.run()` inside the worker thread at `pageindex/page_index.py:1089`; Dockerfile runs Gunicorn `-w 2`.
- **Problem:** Each run executes synchronously in a background thread holding the full task list and orchestrator in memory. `active_runs`/`active_orchestrators` are plain process dicts, so status/cancel only work if the polling request hits the same worker — which it won't under `-w 2` or Render autoscaling. A multi-minute run pins a worker thread and starves HTTP serving; any deploy/SIGTERM/OOM kills the run mid-flight with no resume, leaving sessions stuck `is_running=True`.
- **Elevation:** Move pipeline + push execution to a dedicated Render Background Worker consuming a queue (Redis/Render Key Value). The web process enqueues `(user_id, run_id)` and returns immediately; run status/cancellation live in Postgres/Redis so any instance can serve them. This is the single biggest structural change for the SaaS move.

#### C-2 — All run state is on the ephemeral local filesystem with no user scoping
- **Area:** Orchestration / Persistence / Web-API
- **Evidence:** `pipeline/orchestrator.py:111,183,362,380` hard-code `data/sessions/{run_id}/...`; `ui/server.py:248-256` writes `metadata.json` to the same path; `audit/logger.py:12` `DB_PATH=data/audit.db`; `config/settings.py:93` `data/settings.json`; `pipeline/agents/cross_run_index.py:62` writes `data/project_indices/<key>/index.npz`.
- **Problem:** Every artifact (document_tree, node_index, pipeline_output, coverage_reports, embeddings, audit DB, settings) is written to ephemeral per-instance disk under a `run_id` with no `user_id` namespace. On Render the filesystem is wiped on every deploy, cross-instance reads 404, and there is no row-level isolation. `/api/sessions` (`ui/server.py:199`) silently empties after any redeploy.
- **Elevation:** Move run artifacts and uploaded PDFs to object storage (S3/R2) keyed by `(user_id, run_id)`; move runs/sessions/tasks/audit to Render Postgres; move embeddings + cross-run index to Postgres+pgvector. Nothing durable stays on local disk.

#### C-3 — No resumability or stage-level checkpointing; any crash loses the entire run
- **Area:** Orchestration / Persistence
- **Evidence:** `pipeline/orchestrator.py:131-401`; the only persistence is `node_index.json` (line 197), `document_tree.json` (line 125), and the final `pipeline_output.json` written once at the end (line 365).
- **Problem:** `run()` holds `all_closed_tasks`, `open_tasks`, and `section_coverage_reports` in process memory across the whole node loop (1-4 LLM calls × 103 nodes). A mid-run failure or Render recycle throws everything away with no resume; a 509-task run that dies at node 90 restarts from node 0, re-billing all LLM calls.
- **Elevation:** Persist incremental per-node results (tasks, coverage report, covered node_ids) to durable storage after each node, keyed by `run_id+node_id`; make the loop resume by skipping processed node_ids. In Postgres this becomes a `tasks` table + `run_progress` table a worker can pick up.

#### C-4 — Coverage checker output applied with zero confidence gating → 100% of tasks flagged INCOMPLETE
- **Area:** Orchestration / Intelligence
- **Evidence:** `pipeline/orchestrator.py:284-288` flags every task in a section the moment `report.missed_items` is non-empty; `coverage_check.py:127` defines `min_confidence=0.6` and `coverage_check.py:203` computes `checker_confidence`, but the orchestrator reads neither. The checker only ever sees one section's `extracted_tasks` (`coverage_check.py:133-224`), never the final deduped corpus.
- **Problem:** A single `missed_item` flags all of a section's tasks INCOMPLETE regardless of the checker's own confidence or the count/severity of misses. Cross-section duplicates (covered by a task extracted elsewhere) are reported as missed. The real run produced 618 missed_items and 100% INCOMPLETE — the signal is destroyed.
- **Elevation:** Defer coverage flagging to **after** dedup + gap recovery, then filter every reported missed_item run-wide against the final corpus by embedding similarity and tier it (drop ≥0.85 already-covered, likely_overlap 0.70-0.85, uncovered <0.70). Gate on `checker_confidence >= min_confidence`, flag at the section/report level (not per-ticket), and require the checker to cite which task it believes is missing so false positives are auditable.

#### C-5 — `complete_json` hard-codes `max_tokens=4096` with regex JSON scraping → dedup zero-merges and ~29/30 extraction failures
- **Area:** Intelligence / LLM-routing
- **Evidence:** `pipeline/llm_client.py:501` (`max_tokens=4096` inside `complete_json`, no caller override at `:486-543`); cleanup at `:523-526` only trims to the last `]`/`}` and cannot fix a mid-array truncation; dedup calls it at `deduplication.py:331` with potentially hundreds of pairs; all six agents consume the same path.
- **Problem:** Every agent's JSON call is capped at 4096 completion tokens. A 509-task run's decision array exceeds that and truncates mid-object, producing exactly the "Expecting , delimiter" malformation; `json.loads` raises and the agent's broad `except` (`deduplication.py:336`) returns input unchanged — a silent no-op presented as success.
- **Elevation:** Switch the layer to provider structured-output / JSON-mode (`response_format={"type":"json_object"}` or `json_schema`) so the contract is enforced server-side. Make `max_tokens` per-call sized to the payload; batch dedup candidate pairs (20-30/call) so no response can truncate. Add `json_repair` as a fallback and a partial-recovery detector that routes short/dropped nodes to gap recovery instead of silently losing them. **Highest-leverage fix in the codebase.**

#### C-6 — Provider/credentials resolved from a single process-global settings file; no per-user routing
- **Area:** LLM-routing / Security / Persistence
- **Evidence:** `pipeline/llm_router.py:19-44` (`SettingsManager().load()` then `settings.get("provider")`); `config/settings.py:90-145` (no user dimension); `pipeline/orchestrator.py:45-50` builds `LLMClient` with only `mode`; `ui/server.py:95,113` push decrypted keys into `os.environ`.
- **Problem:** Every concurrent run reads the same `data/settings.json` and the same process `os.environ`. Two users running concurrently get whichever provider/API key was last persisted — User A's run silently bills User B's key. This is both a correctness blocker and a cross-tenant credential breach.
- **Elevation:** Make `ProviderConfig` fully caller-supplied: pass a resolved `(provider, model, decrypted api_key, api_base)` into `LLMClient`/`PipelineOrchestrator` from the request layer, loaded from the per-user Postgres row and decrypted with the app key from a Render secret env-var. Delete `configure_litellm_for_mode()`'s dependency on the global `SettingsManager`; never touch `os.environ` for creds.

#### C-7 — Jira push handler references `JiraClient` with no import → runtime `NameError` on every push
- **Area:** Web-API / Jira
- **Evidence:** `ui/server.py:664` `jira = JiraClient(hierarchy, audit, ...)`; no `from integrations... import JiraClient` anywhere in `ui/server.py`. The exception is swallowed by the broad `except Exception` at `ui/server.py:694`.
- **Problem:** The entire `/api/push` flow raises `NameError: name 'JiraClient' is not defined` the instant it runs, surfaced only as a generic status error. The product's headline feature is dead code — clear evidence the push path hasn't been exercised since a refactor.
- **Elevation:** Import the client (`from integrations.jira_client import JiraClient`), add a smoke test that constructs and invokes `run_push_task` against a mock, and add CI import-lint (pyflakes/ruff `F821`) so undefined names fail the build. In the rewrite, resolve per-user Jira creds from Postgres, not `os.environ`.

#### C-8 — No authentication or authorization on any endpoint
- **Area:** Web-API / Security
- **Evidence:** `ui/server.py` — every route (`/api/upload`, `/api/process`, `/api/settings`, `/api/push`, `/api/sessions`, `/api/tasks`) has no `Depends`/auth guard; grep for `jwt|oauth|login|get_current_user` returns nothing.
- **Problem:** On a hosted deployment anyone with the URL can upload PDFs, trigger LLM spend, read/delete every session, and read/overwrite encrypted settings (provider + Jira creds). With no user model, the locked row-level-isolation-by-`user_id` requirement is impossible — all sessions are one global pool keyed by a timestamp.
- **Elevation:** Add real auth (session cookie or JWT) with a User table in Postgres, scope every query/route by `user_id`, gate routes with a FastAPI dependency. The env already hints at Better Auth (`BETTER_AUTH_TRUSTED_ORIGINS`); consider those patterns.

#### C-9 — Single shared secrets blob; secrets decrypted into global process env → cross-tenant credential leak
- **Area:** Security / Persistence
- **Evidence:** `config/settings.py:90-150` (one `data/settings.json` keyed by provider, not user); `ui/server.py:95` `os.environ['LITELLM_API_KEY']=decrypt(...)`, `:113` `os.environ['JIRA_API_TOKEN']=decrypt(...)`; `pipeline/llm_router.py:32` reads the same shared key.
- **Problem:** Credentials are global to the process. Setting them via `os.environ` means every concurrent BackgroundTask uses whichever user wrote last — one user's OpenRouter/Jira token runs and pushes another user's SOW. A direct cross-tenant data-exfiltration vector.
- **Elevation:** Store per-user credentials encrypted (Fernet/KMS, app key from a Render secret env-var) in Postgres keyed by `user_id`. Decrypt per-request into a request-scoped config object passed explicitly through `RunConfig`/Jira clients — never `os.environ`. Add `WHERE user_id = :current_user` to all reads.

#### C-10 — Jira credentials are process-global `os.environ`, not per-user
- **Area:** Jira
- **Evidence:** `jira_client.py:33,42`; `ui/server.py:497-503` sets `JIRA_SERVER`/`JIRA_API_TOKEN` from the request; `ui/server.py:649` sets `JIRA_PROJECT_KEY` inside `run_push_task`; `jira_mcp_client.py:28-29,43-45` read the same globals.
- **Problem:** `JIRA_*` env vars are shared mutable global state. Two concurrent save_settings/push requests race: User B's save overwrites `JIRA_API_TOKEN` while User A is mid-push, so A's issues are created under B's account.
- **Elevation:** Stop using `os.environ`. Pass an explicit per-call credentials object (`server_url`, `email`, `api_token`, `project_key`) into `JiraClient.__init__`, resolved from the authenticated user's encrypted Postgres row at push time. Make the client stateless w.r.t. global env.

#### C-11 — No Jira idempotency → retries create duplicate issues
- **Area:** Jira
- **Evidence:** `jira_client.py:404` and fallback `:436` call `create_issue` with no pre-check; `jira_mcp_client.py:142`; PUSHED flag persisted only to `data/sessions` on local disk (`ui/server.py:675`); no idempotency/external_id markers in `integrations/`.
- **Problem:** If a 509-task push fails at task 300 (rate limit, timeout, Render SIGTERM), re-pushing re-creates the first 300 as duplicates, since the PUSHED flag may not have persisted and only guards approved-status, not has-a-key. Background pushes on Render are killable mid-run, making this near-certain.
- **Elevation:** Persist `jira_issue_key` per task in Postgres immediately on success; skip any task that already has a key on re-push. Add a deterministic idempotency token (`run_id+task_id` → issue_key map, or a unique label queried before create). Make push resumable.

#### C-12 — No multi-tenant entities exist anywhere (User, Org, Credential, Run, AuditEntry)
- **Area:** Domain model / Persistence
- **Evidence:** grep for `user_id|org_id|tenant|owner|class User|class Org|class Credential` across `pipeline/`, `ui/server.py`, `integrations/`, `audit/`, `models/` returns **zero hits**. `RunConfig` (`schemas.py:199-207`), `AuditEntry` (`schemas.py:212-220`), `ManagedTask` (`schemas.py:160-180`) carry no ownership field.
- **Problem:** The locked target is row-level isolation by `user_id`, but nothing in the domain model can be attributed to a user or org. A query like "runs for this user" is unrepresentable — there is no seam to hang isolation on.
- **Elevation:** Introduce a persisted entity layer: `Org`, `User(org_id, email, role)`, `Credential(user_id, kind, provider, encrypted_blob)`, `Run(user_id, org_id, status, config_snapshot, …)`, and persist `AuditEntry` with `user_id`+`org_id` FKs. Add `user_id`/`org_id` to `ManagedTask`. Back with SQLModel/SQLAlchemy over Postgres; keep LLM-facing `RawTask`/`AcceptanceCriterion` as transport-only models.

#### C-13 — Committed `.github_token` / on-disk secrets bake into the production image
- **Area:** Security / Observability
- **Evidence:** No `.dockerignore` (not found); `Dockerfile:28` `COPY . .`; `.github_token` on disk (41 bytes, starts `ghp_`) and `.env` on disk with real BIFROST/ZAI/JIRA/LANGFUSE secrets. *The security auditor verified these are gitignored (not committed); the observability auditor flagged `.github_token` as tracked via `git ls-files`. Either way the image-bake path is real.*
- **Problem:** The build context includes `.env` and `.github_token`, so any image built from this tree ships real credentials in a layer recoverable by anyone who can pull/inspect the image (Render registry, CI cache).
- **Elevation:** Add `.dockerignore` excluding `.env`, `.env.*`, `.github_token`, `data/`, `.git/`, `venv/`, `*.log`, `telemetry_queue.jsonl`. **Rotate the `ghp_` token and all `.env` secrets immediately.** Move every secret (GitHub token, Langfuse keys, app Fernet/KMS key, per-user encryption key) to Render secret env-vars. Verify whether the file is in git history and scrub with `git filter-repo`/BFG if so.

---

### HIGH

#### H-1 — `complete_json` never requests provider JSON mode (relies on prompt + regex repair)
- **Area:** LLM-routing
- **Evidence:** `pipeline/llm_client.py:486-543`; kwargs built at `:331-340` has no `response_format`; all six agents consume it.
- **Problem:** The client asks for JSON only in the prompt and salvages with regex (last-bracket truncation at `:524-526`); long outputs drift and the regex silently corrupts/rejects valid-but-large arrays. No schema enforcement. This is the routing-layer face of C-5.
- **Elevation:** Add `response_format={"type":"json_object"}` (or `json_schema` with the Pydantic target) gated by provider capability; use native structured outputs/tool-calling for OpenAI/OpenRouter/Anthropic. Keep regex only as last resort; on `JSONDecodeError` do one bounded re-ask before raising.

#### H-2 — Universal silent-swallow error handling turns every LLM/JSON failure into an invisible no-op
- **Area:** Intelligence
- **Evidence:** `deduplication.py:336-347` (→ tasks unmodified), `extraction.py:266-274` (→ `[]`), `coverage_check.py:180-188` (→ empty report), `critic.py:200-208`, `classifier.py:130-138`, `gap_recovery.py:117-124`. Every failure path only writes an audit row.
- **Problem:** A node that lost all tasks, a dedup that did nothing, and a coverage check that errored all look identical to success at the run level. There is no run-level health metric, so the first sign of trouble was a manual 509-task inspection. This is why three critical bugs shipped undetected.
- **Elevation:** Keep graceful-degrade but add a per-run quality report: count of EXTRACTION_ERROR/PARTIAL nodes, dedup merges, coverage tiers, critic flag rate. Mark a run DEGRADED past thresholds (e.g. >5% node parse failures, or 0 merges on >100 tasks). Add an eval harness (golden SOW → expected count/merge bands) in CI.

#### H-3 — Critic forced into per-section duplicate detection, returns conf=0.00, yet still mutates flags
- **Area:** Intelligence
- **Evidence:** `critic.py:50` (LIKELY_DUPLICATE issue), `:89` prompt asks for overlap "in the same list", `:389-390` → LOW_CONFIDENCE, `:385-386` → AMBIGUOUS_SCOPE; orchestrator calls critic per-section (`orchestrator.py:276`). Phase-12: 175/227 CRITIQUE_FLAGGED were likely_duplicate at conf=0.00.
- **Problem:** Only dedup has cross-section + embedding visibility, yet the critic (one section) is asked to find duplicates, so it guesses (conf=0.00). The `_apply` logic flags regardless of confidence — there is no confidence gate on the FLAG path, only on the auto-fix path.
- **Elevation:** Remove LIKELY_DUPLICATE from the critic (dedup owns it) and gate ALL flagging on a minimum confidence floor (e.g. ≥0.5). Add a few-shot good/needs-work pair and require a per-issue justification. Treat conf=0.00 as "no opinion" = no flag.

#### H-4 — Local sentence-transformers embedding + on-disk indices break the multi-user model
- **Area:** Intelligence / Persistence
- **Evidence:** `deduplication.py:8,47,85-88` (MiniLM downloaded/loaded in-process), `:114-122` writes `embeddings.npz`, `:202-205` `ProjectEmbeddingIndex` under `data/project_indices` keyed by `project_key` with **no user scope**; cross-run search excludes only by `run_id`.
- **Problem:** The ~80MB model downloads into ephemeral storage on cold start (lost on redeploy); `embeddings.npz`/`project_indices` are ephemeral and shared; the cross-run index is keyed by `jira_project_key` with no `user_id` — so User A's prior tasks surface as POTENTIAL_DUPLICATE for User B on a shared project. A data-isolation leak.
- **Elevation:** Move embeddings to a hosted embedding API (or pin the model into the image) and persist embeddings + cross-run index in Postgres (pgvector) keyed by `(user_id, project_key)`, with row-level isolation in the search query. Drop the on-disk `.npz`/`project_indices` paths.

#### H-5 — Phase 12 fixes are documented but NOT implemented; orchestrator already mis-calls `extract()`
- **Area:** Intelligence
- **Evidence:** No `json_repair`/`mark_partial`/`EXTRACTION_PARTIAL`/`filter_against_corpus`/`_run_batches` symbols exist in `pipeline/` (grep empty); `requirements.txt` has no `json-repair`; `orchestrator.py:261` calls `raw_tasks = self.extraction_agent.extract(...)` as a plain list while 12-01-PLAN specifies a `(list, partial)` tuple.
- **Problem:** All three known failures are live in `main`. Anyone assuming the layer is "fixed" ships the flag-bomb/zero-merge/conf=0.00 behavior. The current `extract()` shape also means the planned tuple change must be wired end-to-end or extraction breaks.
- **Elevation:** Execute Phase 12 (json-mode + per-call max_tokens + dedup batching + json_repair fallback, post-dedup run-wide coverage filter, remove critic dup role + confidence-gate flags) **before** the Render migration, with the per-run quality report as the acceptance gate (merges>0, sane INCOMPLETE rate on the same 103-node SOW).

#### H-6 — Full dedup re-run after gap recovery compounds the dedup JSON-failure bug
- **Area:** Orchestration
- **Evidence:** `pipeline/orchestrator.py:314` (first dedup) and `:348` (re-dedup of deduplicated + recovered).
- **Problem:** The orchestrator runs dedup twice; given the JSON truncation (C-5/H-1) produces zero merges, doubling invocations doubles failure surface and token spend for no benefit, and it silently accepts no-op dedup as success.
- **Elevation:** Only dedup the newly recovered tasks against the existing deduped set; detect/alert when a dedup pass produces zero merges on a large input (a malformed-response signal). Stop amplifying the underlying bug.

#### H-7 — `CoverageTracker.get_gaps` ignores its `min_text_length` contract, firing gap recovery on empty nodes
- **Area:** Orchestration
- **Evidence:** `pipeline/coverage.py:29-42` — signature `get_gaps(min_text_length=100)` and docstring claim length filtering, but the body returns every uncovered node; orchestrator calls it with `min_text_length=100` (`orchestrator.py:332`) expecting filtering.
- **Problem:** Returns all uncovered nodes including structural/empty ones (headings, TOC entries), so gap recovery burns LLM calls on nodes with no tasks and likely manufactures spurious tasks, inflating the noisy quality signal.
- **Elevation:** Actually filter on `len(node.get('text') or node.get('summary') or '') >= min_text_length`; distinguish "no actionable content" from "missed content" so empty nodes never enter gap recovery.

#### H-8 — PageIndex uses CWD-relative, non-run-scoped log/text paths and in-process `last_tree`
- **Area:** Orchestration
- **Evidence:** `pageindex/utils.py:687,720` (JsonLogger writes `logs/<pdfname>_<timestamp>.json` via relative path); orchestrator caches to `data/sessions/<run_id>/document_tree.json` but legacy `data/document_tree.json` persists; fallback re-walks in-process `last_tree` (`indexer.py:147-153`).
- **Problem:** PageIndex logs to `./logs/` keyed only by PDF name + timestamp — two users with same-named SOWs collide, and the path depends on CWD. On ephemeral Render disk these vanish. Relying on in-process `last_tree` for node text breaks once runs are queued/resumable.
- **Elevation:** Route PageIndex logging through the app observability layer (or scope by `(user_id, run_id)` under an absolute base dir). Persist node text in storage; don't depend on in-process `last_tree` across stages.

#### H-9 — `litellm` global success/failure callbacks mutated per-client instance
- **Area:** LLM-routing
- **Evidence:** `pipeline/llm_client.py:264-266` sets `litellm.success_callback=["opentelemetry"]` in `__init__`; `_configure_litellm_logging()` (`:206-236`) mutates module-level `litellm.set_verbose` on every construction.
- **Problem:** `litellm` is a process-global module. Every `LLMClient` (one per run, many concurrent) rewrites shared callback/verbosity state — a race across BackgroundTasks — and hard-wires the OTel callback to the dropped Argus path.
- **Elevation:** Configure `litellm` global state exactly once at app startup (FastAPI lifespan). For Langfuse Cloud use litellm's langfuse callback configured once from env. Remove per-instance mutation.

#### H-10 — No cost tracking; unreliable token accounting
- **Area:** LLM-routing
- **Evidence:** `llm_client.py:357-360` reads `response.usage` with `getattr` fallbacks to 0; audit logs only total tokens (`:384-393`); no `litellm.completion_cost`, no per-user aggregation.
- **Problem:** A multi-user SaaS needs per-user, per-model cost to bill/limit. Today no dollar cost is computed, token counts silently become 0 when usage is missing, and nothing is attributed to `user_id`. Quotas cannot be enforced.
- **Elevation:** Compute cost via `litellm.completion_cost(response)` and persist `{user_id, run_id, model, prompt_tokens, completion_tokens, cost_usd}` per call. Add per-user budget checks before the call; tag Langfuse traces with `user_id`. Treat missing usage as warn, not silent 0.

#### H-11 — Container (Epic/Story) creation failure silently degrades hierarchy to flat
- **Area:** Jira
- **Evidence:** `jira_client.py:148-155` (epic) and `:167-174` (story): if `_create_container` returns None (`:474-482`), `epic_cache` is never set, `parent_key` becomes None, tasks are created flat, and child `JiraPushResult` is `success=True` with no warning.
- **Problem:** A failed Epic/Story create scatters all children as parentless top-level issues while the user is told the push succeeded — hundreds of orphaned issues for a 103-node SOW with no signal.
- **Elevation:** On container-create failure, either abort that group with explicit per-task warnings or surface a `hierarchy_degraded` flag on each affected result. Retry container creation once on transient 429/5xx.

#### H-12 — No rate-limit / 429 / backoff handling for large Jira pushes
- **Area:** Jira
- **Evidence:** `jira_client.py:131-175` loops `create_issue` serially with no throttling; only retry is the parent-400 fallback at `:424-449`; `jira_mcp_client.py:80-100` same.
- **Problem:** Atlassian Cloud rate-limits per account (429 + Retry-After). Pushing 509 tasks + containers + links in a tight loop will hit limits; a 429 becomes a permanent PUSH_FAILED with no retry, leaving a half-pushed project. Shared Render egress IPs worsen this.
- **Elevation:** Wrap all `create_issue`/`create_issue_link` calls in a retry honoring Retry-After on 429 and backing off on 5xx with a max-attempts cap. Use bounded concurrency (3-5) with a token-bucket limiter instead of a serial loop.

#### H-13 — Run/status state in process-global dicts; not multi-worker or multi-instance safe
- **Area:** Web-API / Persistence
- **Evidence:** `ui/server.py:166-167` `active_runs`/`active_orchestrators` dicts; read in `get_status` (231), `cancel_run` (306), `delete_session` (318), push guard (702); `MODEL_CACHE` (342) same.
- **Problem:** With Gunicorn `-w 2`, a status poll can hit a worker that never saw the run and returns default Idle; cancellation and the "already running" guard only work on the originating worker. Horizontal scaling multiplies the inconsistency.
- **Elevation:** Persist run status and cancel flags in shared storage (Postgres row or Redis key) keyed by `run_id+user_id`; all workers read/write there. Replace the in-memory orchestrator handle with a DB-backed cancel flag the worker polls.

#### H-14 — File upload: no size limit, user-controlled filename, no content validation (path traversal)
- **Area:** Web-API / Security
- **Evidence:** `ui/server.py:327-336` — only checks `.pdf` suffix, then `shutil.copyfileobj` to `UPLOAD_DIR / file.filename`. Contrast `get_session_path` (`:169-172`) which *does* reject `..` — protection is inconsistent.
- **Problem:** No max-size means a large upload exhausts disk/memory; the suffix check is trivially bypassed; the raw client filename is used as the path, so two users collide/overwrite and `../.keyfile`-style names escape `UPLOAD_DIR` to overwrite arbitrary writable files. Nothing verifies the bytes are a PDF.
- **Elevation:** Generate a server-side `uuid4` filename namespaced by `user_id`, validate magic bytes (`%PDF`) not just suffix, enforce a max content-length (413 on oversize), stream to object storage, and `Path(...).resolve().is_relative_to(UPLOAD_DIR.resolve())`.

#### H-15 — CORS allows credentials with localhost-default origins but no auth exists
- **Area:** Web-API / Security
- **Evidence:** `ui/server.py:119-128` `CORSMiddleware allow_credentials=True`, origins from `BETTER_AUTH_TRUSTED_ORIGINS` defaulting to localhost; `allow_headers=['*']`.
- **Problem:** `allow_credentials=True` anticipates cookie auth that doesn't exist; the localhost default silently breaks the real frontend on Render, and `allow_headers='*'` with credentials is a loose CSRF/cross-origin policy once auth lands.
- **Elevation:** Once real auth exists, lock `allow_origins` to the deployed frontend domain(s) via env (fail startup if unset in prod), enumerate exact allowed headers (`Authorization`, `Content-Type`), keep credentials on, and add CSRF protection for cookie sessions.

#### H-16 — `run_id` is a non-unique timestamp+filename slug; collision and isolation risk
- **Area:** Persistence / Domain model
- **Evidence:** `ui/server.py:532-534` `run_id = f"{YYYYMMDD-HHMMSS}-{slug(filename)}"`; session paths `data/sessions/<run_id>/`. Separately `RunConfig.run_id = str(uuid4())[:8]` (`schemas.py:206`) — the two surfaces generate run_id differently.
- **Problem:** Two users uploading the same filename within the same second collide and overwrite each other's session dir; there is no user namespace. The 8-char uuid default also risks birthday collisions across many users and disagrees with the server-built id.
- **Elevation:** Use a full UUID4/ULID as the primary run identifier in exactly one place (a Run factory), namespace all storage by `user_id/run_id`, and keep the readable slug as a display-only label column.

#### H-17 — SQLite audit DB: shared cross-thread connection, no indexes, no concurrency story
- **Area:** Persistence
- **Evidence:** `audit/logger.py:16` `sqlite3.connect(check_same_thread=False)` with one shared `self._conn`; commit per `log()` (`:64`); no index on `run_id` yet `get_run_logs` filters `WHERE run_id=?` (`:67`); `AuditLogger()` instantiated per run/push (`ui/server.py:278,663`, `main.py:117`).
- **Problem:** SQLite is single-writer; concurrent runs hit "database is locked", and `check_same_thread=False` with a shared connection risks corruption. Per-run queries full-scan (already 5757 rows). Multiple Render processes writing one SQLite file on non-shared FS is simply broken.
- **Elevation:** Replace with a Postgres `audit_log` table (`user_id`, `run_id` indexed), a connection pool, batched inserts, and pooled transactions instead of per-call commit.

#### H-18 — In-process BackgroundTask state lost across instances and restarts
- **Area:** Persistence
- **Evidence:** `ui/server.py:166-167` dicts; `run_pipeline_task` as a BackgroundTask; `/api/status` (`:227`) and `/api/cancel` (`:304`) read those dicts; results land only after `run()` completes.
- **Problem:** A run started on instance A is invisible to status/cancel on instance B, and a restart mid-run loses it with no checkpoint. (Same root as C-1/H-13, called out from the persistence lens.)
- **Elevation:** Persist run lifecycle as a Postgres row updated by the worker; move execution to a Render Background Worker consuming a queue so status/cancel read from the DB regardless of serving instance.

#### H-19 — Cross-run dedup index keyed by Jira project key → cross-tenant pollution
- **Area:** Persistence / Intelligence
- **Evidence:** `pipeline/agents/cross_run_index.py:56-62` `ProjectEmbeddingIndex(project_key)` → `data/project_indices/<project_key>/index.npz`; on-disk dirs are `PROJ/` and `RWE/` (project keys, not users); `add_run`/`search` operate on the whole project corpus with no user filter.
- **Problem:** In a single-org multi-user deployment, two users pushing to the same project key share one embedding index, so User A's task titles and run history appear in User B's cross-run matches. Also can't live on ephemeral disk.
- **Elevation:** Move into Postgres+pgvector with a `(user_id, project_key)` composite scope; filter `search()` by the requesting user.

#### H-20 — No `.dockerignore`: on-disk `.env` and PAT bake into the image
- **Area:** Security
- *(Detailed under C-13; listed here as the security subsystem's HIGH framing — `Dockerfile:28 COPY . .` plus on-disk `.env`/`.github_token`.)*

#### H-21 — Fully unpinned dependencies + deprecated PyPDF2 parsing untrusted PDFs
- **Area:** Security
- **Evidence:** `requirements.txt` has 0 `==` pins; PDF stack is `PyPDF2>=3.0.1`, `pymupdf>=1.26.0`, `opendataloader-pdf`; no lockfile.
- **Problem:** Floating versions make builds non-reproducible and silently pull vulnerable releases — critical for a public SaaS parsing untrusted uploads. PyPDF2 is deprecated (CVE history); PyMuPDF native parsing of malicious PDFs is a memory-safety surface reachable from the anonymous upload endpoint.
- **Elevation:** Pin exact versions + commit a lockfile (pip-tools/uv). Replace PyPDF2 with maintained `pypdf`. Run uploaded PDFs through the parser in a resource-limited sandboxed worker (separate Render worker with memory/time caps). Add Dependabot/`pip-audit` to CI.

#### H-22 — No ownership checks on `session_id`/`run_id` parameters (IDOR)
- **Area:** Security
- **Evidence:** `ui/server.py:550-566` `update_task(session_id)`, `:572` `add_task`, `:304` `cancel/{run_id}`, `:217` `get_tasks`, `:313`/`:321` delete with `shutil.rmtree` — all take the id from the client with no user binding.
- **Problem:** Even after adding auth, these stay IDOR-vulnerable: any authenticated user could read, mutate, or delete another user's run by guessing the highly-guessable timestamp-filename `run_id`.
- **Elevation:** Store run/session ownership in Postgres and enforce `WHERE user_id = current_user` on every lookup; use opaque UUID run_ids; authorize before any rmtree/mutation.

#### H-23 — Run identity is a truncated 8-char UUID; free-text fields where enums belong
- **Area:** Domain model
- **Evidence:** `RunConfig.run_id = str(uuid4())[:8]` (`schemas.py:206`); `DedupDecision.decision: str` (`:229`), `TaskDependency.kind: str` (`:85`), `AcceptanceCriterion.verified_by: str` (`:79`), `ProcessingStatus.kind: str` (`ui/server.py:147`); consumers compare raw strings (`jira_client.py:355`).
- **Problem:** 8 hex chars risks collisions as a path component and DB key. Closed-set fields typed as `str` let the LLM emit `keepboth`/`KEEP_BOTH`, and downstream string comparisons silently mis-route — exactly the class of bug behind the dedup zero-merges symptom.
- **Elevation:** Full UUID4/ULID for run identity. Promote closed sets to str Enums (`DedupDecisionType`, `DependencyKind`, `VerificationMethod`, `RunKind`) with a `mode='before'` validator that normalizes case/whitespace and fails loudly on unknown values.

#### H-24 — API layer bypasses typed models and mutates raw dicts from checkpoint JSON
- **Area:** Domain model / Web-API
- **Evidence:** `ui/server.py:178-189` `load_data` returns a raw dict; `update_task` does `tasks[i].update(...)` (`:558`); approve sets `tasks[i]['status']='APPROVED'` (`:604`); `add_task` hand-builds a dict (`:581-589`); `ManagedTask` reconstructed only at push (`:653,707`).
- **Problem:** `ManagedTask` is the stated contract but the review/edit/approve path never validates against it — `TaskUpdate.status: str` accepts any string, ACs can revert to the legacy `list[str]` shape, and errors only surface much later at `ManagedTask(**t)` on push. In a DB world this is how unvalidated/cross-tenant data slips into rows.
- **Elevation:** Make every mutation round-trip through `ManagedTask` (load → `model_validate` → typed patch → `model_dump`); type `TaskUpdate` with the real types. The future repository layer should accept/return `ManagedTask` only, never bare dicts.

#### H-25 — Default test command is red on a clean checkout; no CI, coverage, or lint
- **Area:** Testing
- **Evidence:** `tests/test_hierarchical_judge.py:3` imports `pipeline/evals/judges.py` which does `from langchain_openai import ChatOpenAI` (`:7`) — `langchain` is not in `requirements.txt`, so `venv/bin/pytest tests/` → "Interrupted: 1 error during collection", 0 tests run (excluding that file: 100 pass). No `.github/`, no `pyproject.toml`/`pytest.ini`/`ruff` config, no `make test`/`lint`/`coverage`.
- **Problem:** The documented command fails immediately, masking 100 passing tests behind a `ModuleNotFoundError`; any CI wired to it is red on first run. Nothing prevents a regression — including to the auth/encryption code the SaaS will depend on — from merging.
- **Elevation:** Port `HierarchicalJudge` onto the existing `llm_client.py` path (drop langchain) or pin it, and guard optional-dep tests with `pytest.importorskip`. Add `.github/workflows/ci.yml` running `pytest --cov --cov-fail-under=<floor>` + ruff on push/PR; add `pyproject.toml` (ruff + pytest config, `testpaths = tests`) and `make test/lint/coverage`; ratchet the coverage floor up.

#### H-26 — Phase-12 production bugs have no regression tests; truncation path untested
- **Area:** Testing
- **Evidence:** `complete_json` hardcodes `max_tokens=4096` then regex-strips + `json.loads` with no `finish_reason`/length check (`llm_client.py:486-540`); `tests/test_thinking_model_json.py` covers only `<think>` stripping and trailing garbage, not a mid-array truncation; coverage over-report and critic conf=0.00 have no bounded-false-positive test.
- **Problem:** The three bugs that broke the first real run can recur with zero test signal; existing tests assert plumbing, not quality bounds.
- **Elevation:** Add regression tests: (a) `complete_json` fed a `finish_reason=length` response must repair-or-retry, not raise; (b) a well-covered coverage_check fixture yields zero `missed_items`; (c) a critic fixture asserts no LOW_CONFIDENCE/AMBIGUOUS_SCOPE on genuinely high confidence and never blanket conf=0.00. Make the eval harness runnable in CI against a recorded fixture.

#### H-27 — Entire web/orchestration/integration surface has zero tests
- **Area:** Testing
- **Evidence:** Grep across `tests/`: 0 references to `ui/server.py`, `pipeline/orchestrator.py`, `pipeline/llm_router.py`, `integrations/jira_mcp_client.py`; no `TestClient`/`httpx`; no e2e exercising `PipelineOrchestrator.run()`.
- **Problem:** Every cross-layer SaaS-critical behavior is unverified — request validation, status/session endpoints, background-task lifecycle, settings encryption round-trip, orchestrator sequencing/checkpointing. The new multi-user pieces (auth, row isolation, per-user creds) will sit on top of untested surface.
- **Elevation:** Add FastAPI `TestClient` tests for every route (401 when unauthenticated; User A cannot read User B's run). Add one orchestrator integration test against a small fixture PDF with a stubbed LLM asserting checkpoint shape. Add Jira client tests against a mocked transport (`responses`/`respx`) covering parent-link fallback and error mapping.

#### H-28 — Per-run file logging / JSONL audit on local disk; no `user_id` in log context
- **Area:** Observability
- **Evidence:** `observability.py:179` `system.log`, `:189` `audit.jsonl`, `:198-219` `data/logs/run_<run_id>.log`; `logger.configure(extra={agent, run_id})` (`:175`) has no `user_id`.
- **Problem:** Render web FS is ephemeral and not shared across replicas, so these logs vanish on deploy; with no `user_id`/`org_id` in context, per-tenant log/trace attribution and isolation are impossible.
- **Elevation:** Emit JSON logs to stdout (Render captures them) with `user_id+org_id+run_id` on every line; move the audit trail into the Postgres audit table; stream run logs for the UI by querying the log backend, not local files.

#### H-29 — OTel metrics emitted but never exported; Argus fleet model wrong for one hosted instance; langfuse SDK missing
- **Area:** Observability
- **Evidence:** Counters/histograms created via `metrics.get_meter()` (`observability.py:140-150`) and recorded in the LLM hot path (`llm_client.py:367-371`) but no `MeterProvider`/`PeriodicExportingMetricReader` anywhere (grep empty); `infra/user` + `infra/admin` ship per-instance edge/HQ collectors, Bifrost, Loki, Tempo, Grafana for an N-instance forwarding fleet; `requirements.txt` has no `langfuse` (only `infra/admin/evaluator/requirements.txt` does); `verify-telemetry.py:8` imports `resolve_observability_endpoint` which no longer exists.
- **Problem:** Token/latency/cost metrics silently go to the no-op meter — dead instrumentation that looks like working observability. The whole edge→HQ topology assumes many self-hosted user installs, the opposite of one Render service. The decided Langfuse "keep" path has no installed SDK, and the shipped integrity check ImportErrors.
- **Elevation:** Delete `infra/user`/`infra/admin` from the deploy path and the `SOW_INSTANCE_ID`/`argus.instance_id` fleet identity. Emit JSON logs to stdout + add `langfuse` to `requirements.txt` and push LLM generations (with cost/tokens) via the Langfuse SDK, keyed by `user_id`/`org_id`. Rewrite `verify-telemetry.py` as a Render healthcheck or delete it.

---

## 4. MEDIUM / LOW Findings Appendix (by area)

### Orchestration
- **[MEDIUM] Stage failures inside `run()` are unstructured and non-recoverable; partial failures masked.** Only `node_index` persistence is wrapped in try/except (`orchestrator.py:182-201`); agent exceptions are swallowed (return empty), so a run can "succeed" with silently dropped nodes; final status is binary (Complete / Error:<str>). *Elevation:* per-node/per-stage result records (succeeded/failed/skipped + error class) persisted with the run, a structured failure summary in run status, and metrics so failure rates are observable.
- **[MEDIUM] `__init__` does heavy work and reads config from env + app_config inconsistently.** Thresholds from `os.getenv` while other limits from `app_config['pipeline']` (`orchestrator.py:53-93`); `configure_litellm_for_mode` + `DocumentIndexer` built in `__init__` then rebuilt in `run()` (`:91-92`, `:139-146`); `provider_config` mutated on shared `self.llm`. *Elevation:* move all tuning into `RunConfig` (per-run, per-user), resolve provider/model once, construct fresh agents per `run()`.

### Intelligence
- **[MEDIUM] Dedup merge semantics are buggy and lossy independent of the JSON failure.** `deduplication.py:362-373` confused `merged_from` bookkeeping; `:368-370` dead-code block; `:428-435` `list(set(...))` dedups by string identity and loses ordering; `keep_first`/`keep_second` (`:375-383`) set MERGED but never call `_merge_tasks`, so the dropped task's ACs/deliverables/source_refs are lost. *Elevation:* absorb content via `_merge_tasks` before dropping, fix provenance to the survivor only, order-preserving case-normalized de-dup, and a unit test asserting no AC/source_ref is lost across any branch.

### LLM-routing
- **[MEDIUM] Ollama/LOCAL, Bifrost, ZAI routing and Docker-host rewriting are dead weight.** `llm_router.py:46-69`, `llm_client.py:270-273,316-319,397-419`, `config/settings.py:27-49`; a 3600s local timeout + unlimited-retry loop could hang a Render worker. *Elevation:* delete `LLMMode.LOCAL`, the Ollama wait loop, ZAI/Bifrost header injection, and `_ensure_docker_host`; collapse to one hosted-API path with a bounded timeout.
- **[MEDIUM] Fixed 60s timeout + 300s/8-attempt retry budget can pin Render worker capacity.** `llm_client.py:313-319`; blocking `time.sleep` (`:56-61`); honored Retry-After up to 300s with no SLA ceiling. *Elevation:* move execution to a queue/worker, make timeout/attempts env-tunable, cap honored Retry-After to a product SLA, add a per-process concurrency semaphore.
- **[LOW] Error classification leans on brittle string matching.** `_is_non_retryable_llm_error`/`is_retryable_remote_error` (`llm_client.py:34-49,177-204`) and `_extract_status_code` regex (`:91-92`) match substrings, misclassifying errors whose bodies echo "403"/"try again". *Elevation:* prefer typed litellm exceptions and `status_code`, string heuristics last.

### Web-API
- **[MEDIUM] `get_session_path` path-traversal guard is a silent fallback, not a rejection.** Returns `Path('data/pipeline_output.json')` on bad input instead of 404 (`ui/server.py:169-172`); delete/metadata writes build paths from `run_id` with no validation. *Elevation:* validate against a strict pattern, 404 on mismatch, never fall back to a shared path.
- **[MEDIUM] Synchronous file I/O + full-file JSON rewrite in request handlers.** `load_data`/`save_data` (`:185-195`); `get_sessions` scans every `metadata.json`; a single-task edit rewrites all 509 tasks with a last-writer-wins race. *Elevation:* back tasks/sessions with Postgres (one UPDATE per edit, indexed list query, transactional concurrent edits).
- **[LOW] Hard-coded 6-step progress contract and brittle polling.** `ui/app.js:569` `Step ${status.current_step}/6` while the pipeline has more stages; logs capped to 50 in-memory entries; 1s polling assumes same worker. *Elevation:* derive total steps from the orchestrator, persist logs/status to the DB, data-drive labels, consider SSE/websocket.

### Jira
- **[MEDIUM] MCP client is unwired dead code and less robust than REST.** Referenced only by `test_jira_mcp.py`; shells out via `sh -c` to `npx @atlassian/mcp-remote` with the token in the command string (`jira_mcp_client.py:50-71`), regex-parses keys, no parent fallback/issue-type resolution/links. *Elevation:* drop the MCP client for the Render move and standardize on REST; if wanted later, reintroduce via hosted MCP transport without spawning npx or token-in-shell.
- **[MEDIUM] Issue-link direction semantics inverted vs documented intent.** `jira_client.py:251-257` passes `inwardIssue=source_key, outwardIssue=target_key` with type Blocks, likely reading backwards; silent (link created). *Elevation:* pin the intended semantic from the dependency model and set inward/outward accordingly, with a per-kind unit test.
- **[LOW] Issue-type fallback can silently mis-type issues and flatten sub-task hierarchy.** `_resolve_issue_type` last-resort picks an arbitrary type (`:96-109`); a sub-task falling back to Task means no parent is possible (`:158-160`). *Elevation:* surface resolution failures as per-task warnings; refuse to silently substitute a sub-task with a standalone Task.

### Persistence
- **[MEDIUM] No migrations framework; ad-hoc startup migration mutates disk.** `CREATE TABLE IF NOT EXISTS` is the only schema mechanism (`audit/logger.py:19`); `config/settings.py:132` reshapes legacy settings on load; `ui/server.py:46-66` moves `pipeline_output.json` into a session dir on boot. *Elevation:* introduce Alembic + SQLAlchemy models; encode the local→Postgres backfill as a one-time idempotent migration, not startup side effects.
- **[MEDIUM] `telemetry_queue.jsonl` / `audit.jsonl` are unbounded local append files.** `data/telemetry_queue.jsonl` (383 KB), `data/audit.jsonl` (6.2 MB), `data/system.log` (1.6 MB); `sync_telemetry()` drains the local queue on startup (`ui/server.py:68`). *Elevation:* drop the Argus store-and-forward buffers; emit to stdout + Langfuse Cloud directly.

### Observability
- **[MEDIUM] `verify-telemetry.py` is broken against the current module.** Imports `resolve_observability_endpoint` (module exports `resolve_collector_endpoint`, `:37`) and references removed `BIFROST_*` env vars. *Elevation:* rewrite as a Render healthcheck (assert `LANGFUSE_*`, send one test generation, confirm stdout JSON logging) or delete.
- **[MEDIUM] `SYNC_ENABLED` default-off silently disables all tracing and ties trace-ID injection to it.** `observability.py:32-35,80-82`; Loguru hard-sets `otelTraceID='disabled'` when off (`:170,211`). *Elevation:* make Langfuse the single explicit toggle, fail/warn loud if traces expected but unconfigured, always inject a real trace/span ID when a span is active.
- **[MEDIUM] The offline evaluator is a non-functional stub with hardcoded fake keys.** `infra/admin/evaluator/main.py:23-33` trace loop commented out; `score_trace()` all placeholders; fake `pk-lf-1234567890` keys (`:10-11`). *Elevation:* rebuild as a Render cron/worker pulling Langfuse traces/datasets and scoring with `HierarchicalJudge`, reading keys from Render secrets.
- **[MEDIUM] Telemetry scrubbing is a single hardcoded field; SOW content can leak into traces/logs.** `telemetry.py:8-13` only drops `section_title`; `llm_client.py:364` sets `response_preview=content[:1000]`; Langfuse direct mode sends all spans (`observability.py:104-107`). *Elevation:* allowlist safe fields (ids/counts/durations/model), gate content capture behind an env flag + per-org consent, use trace-level `user_id`/`org_id` tags.

### Testing
- **[MEDIUM] Eval/judge test suite is stubbed and dependency-broken.** `tests/test_phase11_evals.py:39-52` three tests are bare `pass`; `test_run_eval_dataset_script` only asserts `hasattr`; `judges.py` uses langchain while the codebase uses litellm. *Elevation:* implement against a recorded fixture or delete the asserting-nothing stubs; unify the judge onto `llm_client.py`; mark live-dep tests `@pytest.mark.integration`.
- **[MEDIUM] Tests for dropped/local-first features still in the gate; global env mutation pollutes state.** `test_routing.py:7-37` tests `_ensure_docker_host`; `test_phase2_runtime_reliability.py:116-138` asserts LOCAL/Ollama isolation; `test_routing.py` sets `os.environ` with no teardown. *Elevation:* delete dropped-feature tests when Ollama/local-Docker goes; replace raw `os.environ[...] =` with `monkeypatch.setenv`.
- **[LOW] Root-level `test_*.py` are live-credential smoke scripts masquerading as tests.** `test_jira_api.py:6-25` hits a real Jira server, only `print()`s, never asserts; pytest auto-discovers the `test_` prefix. *Elevation:* rename to `scripts/check_*.py` (drop the prefix) or move under `scripts/`; set `testpaths = tests`.

### Security
- **[MEDIUM] Weak Fernet key handling: env key used raw, swallowed decrypt errors.** `config/settings.py:102-105` returns `env_key.encode()` with no validation; `ui/server.py:96-97,114-115` `except Exception: pass` silently swallow decrypt failures. *Elevation:* validate the env key shape at startup and fail fast; log surfaced errors (without secret values); adopt a managed key with a versioned rotation path.
- **[MEDIUM] Overly permissive CORS headers/methods with credentials allowed.** `ui/server.py:122-128` `allow_credentials=True` with `allow_headers=['*']`. *Elevation:* enumerate allowed headers, keep origins explicit and required (fail startup if unset in prod), add CSRF for cookie sessions. *(See H-15.)*

### Domain model
- **[MEDIUM] Domain schemas scattered across agent modules instead of centralized.** `MissedItem`/`SectionCoverageReport` in `coverage_check.py:41,48`; `TaskCritique`/`CritiqueReport` in `critic.py:55,64`; `ClassificationResult` in `classifier.py:40`; `EvaluationScores` in `judges.py:13` — yet they're persisted into `pipeline_output.json` (`orchestrator.py:285`). *Elevation:* move all persisted/cross-layer schemas into `models/` (e.g. `models/intelligence_schemas.py`) so the durable contract is versioned in one place.
- **[MEDIUM] `eval_schemas.py` has drifted from the production schema.** `GoldenTicket.acceptance_criteria: Optional[List[str]]` (`eval_schemas.py:13`) while `RawTask` uses structured `AcceptanceCriterion`; legacy `typing` imports; `HierarchicalDatasetItem` a thin stub. *Elevation:* regenerate `GoldenTicket` from the canonical model (or subset it) and add a contract test asserting its fields are a subset of `ManagedTask`.
- **[MEDIUM] Missing field/object invariants — confidence is the only constraint.** `RawTask.confidence` has `ge=0/le=1` but `ManagedTask.confidence` (`schemas.py:172`) has none; `SourceRef` has no `page_end>=page_start`; no `extra='forbid'`. *Elevation:* add bounds to `ManagedTask.confidence`, a `SourceRef` validator, `ConfigDict(extra='forbid')` on LLM-facing models, and a validator that MERGED tasks have non-empty `merged_from`.
- **[LOW] `datetime.utcnow()` (deprecated, naive) used for all timestamps.** `schemas.py:179-180,214`; `deduplication.py:447`, `state.py:241`, `coverage_check.py:54`, `ui/server.py:64,255`, `cross_run_index.py:115`. *Elevation:* switch to `datetime.now(timezone.utc)` (tz-aware) and store as `timestamptz`; centralize a `utcnow()` helper.
- **[LOW] `LLMMode` enum and `ProviderConfig` encode dropped/local-first assumptions.** `LLMMode.LOCAL` + Bifrost comments (`schemas.py:47-50`); `ProviderConfig` holds plaintext `api_key`/`api_base` and is `model_dump`'d into `pipeline_output.json` (`orchestrator.py:369`). *Elevation:* remove `LLMMode.LOCAL`; split credential material into the new `Credential` entity referenced by id so persisted run configs snapshot a `credential_id`, never the key.

---

## 5. Consolidated Render Hosted-SaaS Blockers

These must be resolved before the hosted multi-user deploy. Grouped by theme; each maps to findings above.

### A. Execution model — move off in-process BackgroundTasks
1. Pipeline + push run as in-process FastAPI BackgroundTasks/daemon threads (`ui/server.py:536,713`), killed by every Render deploy/SIGTERM/OOM with no resume, leaving runs stuck `is_running=True`. **Move to a Render Background Worker + job queue (Redis/Key Value) with SIGTERM-safe shutdown.** *(C-1, C-3, H-13, H-18)*
2. A single rate-limited LLM call can block a worker thread up to ~300s (`llm_client.py:313-319`); the Ollama path allows 3600s + unlimited retries. **Bounded timeouts, env-tunable retry budget, per-process concurrency cap — and delete the LOCAL path.**

### B. State & isolation — Postgres + object storage, everything keyed by `user_id`
3. All durable state (`data/sessions/*`, `data/audit.db`, `data/settings.json`, `data/.keyfile`, `data/project_indices/*`, `data/uploads/*`, per-run logs) is on ephemeral per-instance disk → 100% data loss on deploy and cross-instance 404s. **Postgres + object storage.** *(C-2, H-17, H-28)*
4. Process-global state dicts (`active_runs`/`active_orchestrators`/`MODEL_CACHE`) break status/cancel/concurrency-guard under `-w 2` and scaling. **Shared status in Postgres/Redis.** *(C-1, H-13)*
5. No `user_id`/`org_id` anywhere — row-level isolation is impossible without a ground-up entity model (User/Org/Credential/Run/AuditEntry) and query scoping; `/api/sessions` and DELETE expose/delete all users' data. *(C-12, H-22)*
6. `run_id` is a timestamp+filename slug (and a divergent 8-char uuid in the schema) — collides under concurrency as a storage key. **Full UUID/ULID, namespaced by `user_id`.** *(H-16, H-23)*
7. SQLite single-writer audit DB + on-disk embeddings/cross-run index cannot be shared across instances and leak across tenants (cross-run index keyed by project_key only). **Postgres + pgvector keyed by `(user_id, project_key)`.** *(H-17, H-4, H-19)*

### C. Credentials & secrets — per-user, encrypted in Postgres, app key in a Render secret
8. LLM + Jira credentials are resolved from one global `data/settings.json` and pushed into shared `os.environ`, so concurrent users overwrite each other's keys — a cross-tenant credential breach. **Per-user encrypted creds in Postgres, decrypted into a request-scoped config object, never `os.environ`.** *(C-6, C-9, C-10)*
9. The Fernet app key falls back to a local `data/.keyfile` (ephemeral, per-instance) → decryption fails after redeploy. **App key must come only from a Render secret env-var; remove the keyfile fallback.**
10. No `.dockerignore` + `COPY . .` bakes on-disk `.env` and the `ghp_` PAT into the image. **Add `.dockerignore`, rotate all secrets, inject via Render secrets.** *(C-13, H-20)*
11. Zero auth means a public `onrender.com` URL exposes Jira push and credential-overwrite to the internet. **Auth + user model is a hard prerequisite.** *(C-8)*
12. `ProviderConfig` holds plaintext `api_key` and is `model_dump`'d into `pipeline_output.json` — persisting runs per-user writes credentials at rest. **Persist a `credential_id`, not the key.** *(LOW domain finding)*

### D. Intelligence quality — fix before/with the migration
13. `complete_json max_tokens=4096` + regex JSON scraping keeps failing on large hosted-provider outputs (dedup zero-merges, ~29/30 extraction failures) regardless of platform. **Provider JSON-mode + per-call max_tokens + dedup batching + json_repair fallback.** *(C-5, H-1)*
14. Coverage flag-bomb (100% INCOMPLETE) and critic conf=0.00 mass-flagging are live in `main`; Phase 12 fixes are docs only. **Execute Phase 12 with a per-run quality report as the acceptance gate.** *(C-4, H-3, H-5, H-6, H-7)*
15. No per-user credential scoping in the agent layer; `LLMClient` is built from global env/settings. **Inject the requesting user's provider config per run.** *(C-6)*
16. Local sentence-transformers MiniLM downloaded in-process per worker → cold-start latency + redownload on every deploy. **Pin into the image or use a hosted embedding API.** *(H-4)*

### E. Untrusted input & dependencies
17. Anonymous PDF upload with path traversal (`UPLOAD_DIR / file.filename`) and in-process untrusted-PDF parsing (PyPDF2/PyMuPDF) is a public file-overwrite/RCE surface. **Sandbox parsing in a resource-limited worker; uuid filenames; magic-byte + size validation.** *(H-14, H-21)*
18. Fully unpinned dependencies (no lockfile) make each build non-reproducible and can pull vulnerable releases parsing untrusted input. **Pin + lockfile + `pip-audit`/Dependabot in CI.** *(H-21)*

### F. Observability & CI
19. Argus edge/HQ fleet (Bifrost, Tempo, Loki, Prometheus, Grafana, OTel collectors) assumes many forwarding instances — wrong for one Render service; compose files reference config that isn't in the repo. **Delete from the deploy path; use Render logs/metrics + Langfuse Cloud.** *(H-29)*
20. OTel metrics never export (no MeterProvider); `langfuse` SDK is missing from `requirements.txt`; `SYNC_ENABLED` default-off silently drops all traces; `verify-telemetry.py` ImportErrors. **Add `langfuse`, emit cost/token metrics via Langfuse generations, fail-loud on missing keys, rewrite the verifier.** *(H-29)*
21. No CI, coverage gate, or lint; the default `pytest tests/` is red on a clean checkout (missing langchain). **CI with a green suite + coverage floor must exist before hosting; the first tests written should cover auth, `user_id` isolation, and per-user credential encryption.** *(H-25, H-26, H-27)*

---

*Faithful to the 10 subsystem auditor findings; no findings invented. Where auditors disagreed (the `.github_token` commit status), both positions are noted rather than resolved.*
