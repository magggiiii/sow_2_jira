# SOW Scale-Hardening Plan — handling 50-60 page SOWs & long-running runs

**Status:** plan (2026-06-15). Backed by code-level scouting (two dynamic workflows). Branch `elevation/wave-0`.
**Companion docs:** `IMPLEMENTATION-PLAN.md` (169 change-ids), `HARNESS-ARCHITECTURE.md`, `UPGRADES.html`.

## 0. The scenario & today's numbers

A 55-60 page SOW indexes to **~50-150 PageIndex nodes** (`RunConfig.max_nodes` hard-caps at 200,
`models/schemas.py:342`). The orchestrator then processes nodes **strictly sequentially**
(`pipeline/orchestrator.py:241-313`), each node firing up to 4 structured LLM calls (classify →
extract → critic → coverage), plus run-wide dedup + gap-recovery.

- **~300-600 sequential LLM calls** per run.
- At measured live latencies (classifier 2-6s, extraction 11-27s, critic/coverage similar, gap-recovery
  24-111s), wall-clock is **~25 min to ~2 h** for one big SOW.
- The run executes **inside the web process** (FastAPI `BackgroundTasks` → threadpool, `ui/server.py:531-538`),
  with run state in **in-memory dicts** (`active_runs`/`active_orchestrators`, `ui/server.py:167-168`) and a
  **single end-of-run checkpoint** (`orchestrator.py:393`).

**Verdict:** a big SOW will *run* but is slow and fragile. None of the gaps are hard to fix; this plan
sequences them.

> **Myth corrected (verified):** the gunicorn `--timeout` default (30s; `Dockerfile:41` sets no explicit
> timeout) does **not** kill the run. The worker is `uvicorn.workers.UvicornWorker` and `run_pipeline_task` is
> a *sync* function, so FastAPI runs it in a threadpool — the event loop stays alive and the arbiter heartbeat
> keeps firing. The real fragility is architectural (in-memory state + no resume), addressed in Phase C.

---

## 1. The parallelism answer (the headline question)

**Do we do parallel LLM calls? Not in the agent pipeline — it's fully sequential.** (PageIndex *indexing*
already parallelizes via `asyncio.gather`, e.g. `pageindex/page_index.py:99,766` for TOC title verification —
but on a separate `llm_acompletion` path not wired to the agents.)

**Can we? Yes — safely — and it's the single biggest wall-clock win.** Scouting confirmed:

- Per-node `classify/extract/critic/coverage` calls are **independent across nodes** — a node never reads a
  prior node's *results* before running its own LLM calls.
- `StateAgent` (`pipeline/agents/state.py:68-187`) is **rule-based, no LLM**, and `continues_to_next` only
  governs whether a task from node N+1 *merges* with node N — applied **after** extraction. It does **not**
  serialize the calls.
- What **must stay sequential:** (a) the `StateAgent` continuation/merge, applied in node order on gathered
  results; (b) `CoverageTracker.mark_covered` mutation (`coverage.py:24-27`, called in-loop at
  `orchestrator.py:309-310`); (c) run-wide `deduplicate()` (`orchestrator.py:332`), which needs the full task
  list.

**Design (Phase B):** parallelize the I/O-bound per-node LLM work with a `ThreadPoolExecutor` (the
`complete_structured`/litellm seam is sync + thread-safe), collect results **in node order**, then apply
state-merge + coverage + dedup sequentially. ~4-5× wall-clock reduction at `max_workers≈6` (a 150-node run:
~25-40 min → ~6-10 min), bounded by provider rate limits.

---

## 2. Plan overview (priority order)

| # | Item | Delivery | Leverage | Effort | Depends on |
|---|------|----------|----------|--------|------------|
| **A1** | Restore `complete_structured` resilience (timeout + retry/backoff) | offline-now | **high** | small | — |
| **A2** | Per-node error isolation + graceful `max_nodes` (no whole-run crash) | offline-now | high | small | — |
| **A3** | Batch dedup candidate pairs (no single-call truncation) | offline-now | high | small | — |
| **A4** | Surface section truncation (warn+flag); optional adaptive chunking | offline-now / config | medium | small→med | — |
| **B1** | Parallelize the per-node loop (ThreadPoolExecutor) | config-only | **high** | medium | A1, A2 |
| **C1** | Per-node checkpoint + resume | offline-now | high | medium | A2 |
| **C2** | `cancel_check` seam + filesystem status fallback | offline-now | medium | small | — |
| **C3** | Durable worker (Arq + Redis) + Postgres run state | **needs-services** (Wave 2) | high | large | Wave 1, C1, C2 |

**Locked constraints honored:** none of A1-C2 touch `pipeline/llm_router.py` / `BIFROST_*` / `os.environ`
credential resolution (read-only reuse of `llm_client.py` helpers only). C3 is the Wave 2 platform piece and
needs provisioned services.

---

## Phase A — Resilience & integrity (offline-now, no services)

### A1. Restore `complete_structured` resilience — *the C-5 regression* ⭐ start here

**Problem.** `core/agent_runner.py:complete_structured` calls `instructor.from_litellm(litellm.completion)`
directly with **no timeout, no retry, no rate-limit backoff, no truncation detection**. The old `complete_json`
path (`llm_client.py:_execute_call`, lines 333-530) had all four (60s timeout; 8-attempt budget;
`Retry-After`/exponential backoff; `LLMTruncationError`). Over 300-600 calls a single transient 429/timeout
now drops that agent to its safe default (lost tasks), and a hung call can block **indefinitely**.

**Change (`core/agent_runner.py:complete_structured`):**
1. Add `timeout` into `create_kwargs` — default `60`, `3600` for Ollama (mode `LLMMode.LOCAL` or
   `model.startswith("ollama/")`), overridable via `S2J_STRUCTURED_TIMEOUT`. litellm honors `timeout` through instructor.
2. Wrap the `client.chat.completions.create(...)` in a retry loop that **reuses** (import-only, no modification)
   `extract_retry_hint`, `compute_wait_seconds`, `is_retryable_remote_error` from `pipeline/llm_client.py`.
   Budget via env: `LLM_STRUCTURED_MAX_ATTEMPTS` (8), `LLM_STRUCTURED_MAX_ELAPSED_S` (300),
   `LLM_STRUCTURED_MAX_WAIT_S` (300). Retry only on `is_retryable_remote_error`; re-raise `InstructorError`
   only after the budget is exhausted (callers already catch it → behavior transparent).
3. Note instructor's own validation-retry is orthogonal (it retries unsatisfiable *schemas*, not provider
   errors); the two compose. Document in the docstring.

**Risk:** low — `llm_client` imports are one-way (`core→pipeline` safe, no cycle). Agents' degrade-on-final-failure
unchanged. **Tests** (`tests/test_agent_runner_structured.py`, extend the instructor stub to count attempts +
timing): retries on transient error then succeeds; gives up after budget → `InstructorError`; non-retryable
(401) fails immediately (1 call); `timeout` kwarg present (60 default / env override / 3600 Ollama);
`Retry-After` header respected.

### A2. Per-node error isolation + graceful `max_nodes`

**Problem.** A single node's unhandled error aborts the whole run (`orchestrator.py:225-323`); `>200` nodes
raises `RuntimeError` with **no partial output** (`orchestrator.py:184-188`).

**Change:**
- Wrap each node's processing in `try/except`: log `EXTRACTION_FAILED`, increment `error_count`, **continue**.
  Flag the run `DEGRADED_EXTRACTION` if `error_count > 5%` of nodes.
- Replace the `max_nodes` `RuntimeError` with graceful capping: process `nodes[:max_nodes]`, emit
  `DEGRADED_CAPACITY` in the health report (`"Processed N of M nodes"`), keep partial output. Gate old behavior
  behind `RunConfig.node_processing_strategy: Literal["strict","degraded"] = "degraded"`.

**Risk:** low/additive. **Tests:** crash-one-node → others still produce tasks + health flagged; 250-node tree
with cap 200 → 200 processed, `degraded=True`, no raise.

### A3. Batch dedup candidate pairs

**Problem.** `deduplication.py:389-418` sends **all** candidate pairs in one `complete_structured` call
(`max_tokens=8192`); hundreds of pairs truncate. W3-D degraded signal only fires on *zero* merges, so partial
truncation is undetected.

**Change:** chunk `candidate_pairs` into batches (~40), call `complete_structured(response_model=DedupDecisionList)`
per batch, merge the `DedupDecisionList`s (`deduplication.py:342-517`). The schema already supports this. Extend
W3-D: also flag when a >30-pair batch yields zero merges (`DEDUP_DEGRADED_BATCH`).

**Risk:** low; small inputs (<50 pairs) keep the single-call path. **Tests:** 150 pairs / batch 40 → 4 batches,
merged `drop_ids` correct; per-batch degraded signal.

### A4. Surface section truncation (stop losing tasks silently)

**Problem.** `extraction.py:267-274` (and `coverage_check.py`, `classifier.py`) silently truncate at
`max_section_chars` (16000 / 16000 / 4000) — dense pages lose tasks with only an in-prompt note.

**Change (incremental):**
- *Now:* log `SECTION_TRUNCATED` audit + add a `TRUNCATION` flag on affected tasks; expose `max_section_chars`
  via config so big docs can raise it.
- *Optional (med):* `adaptive_extract(section_text, max_chars, overlap)` — split oversize sections into
  overlapping chunks, extract each, merge by title/description similarity (reuse the StateAgent dedup notion).

**Risk:** additive flag (`"TRUNCATION" in task.flags`). **Tests:** oversize section → flag + audit; adaptive
path merges without dupes.

---

## Phase B — Throughput (offline-now, config-gated)

### B1. Parallelize the per-node loop ⭐ biggest wall-clock win

**Design (see §1 for why it's safe):**
1. *(additive)* `core/agent_runner.py`: optional `complete_structured_async` over `litellm.acompletion` — OR
   simpler, keep sync and use threads (recommended: LLM latency dominates, threading overhead negligible).
2. Extract the per-node body (`orchestrator.py:256-310`: classify+extract+critic+coverage for one node) into a
   pure `_process_node(node, …) -> NodeResult` that makes **no shared-state mutation** (returns tasks +
   coverage marks; does NOT touch the shared `CoverageTracker`/`all_closed_tasks`).
3. Run `_process_node` across nodes via `ThreadPoolExecutor(max_workers=N)`; collect results **in node order**.
4. Apply `StateAgent` continuation/merge + `CoverageTracker.mark_covered` **sequentially** on the ordered
   results; then the existing run-wide dedup + gap-recovery.
5. `max_workers` from env (`SOW_NODE_CONCURRENCY`, default 6); `=1` reproduces today's sequential behavior.

**Risk:** medium — must keep per-node code free of shared mutation; rate-limit interaction (A1's backoff makes
concurrent 429s safe). **Tests:** parallel vs sequential on a 20-node fixture → identical task count / coverage /
dedup; inflight-call counter ≤ `max_workers`; `=1` == legacy path. **Expected:** ~4-5× (150 nodes:
~25-40 min → ~6-10 min).

---

## Phase C — Durability & long-running process lifecycle

### C1. Per-node checkpoint + resume (offline-now)

**Problem.** Only a final checkpoint exists (`orchestrator.py:393`); a crash at node 45/60 loses everything.
Precedent: the `skip_indexing`/`document_tree.json` cache (`orchestrator.py:123-127`).

**Change:** write `data/sessions/{run_id}/extraction_checkpoint.json` after each node (atomic temp+move) holding
`{processed_node_ids, all_closed_tasks, open_tasks_for_next_node, coverage_state, last_index, ts}`. On run start,
`_should_resume()` → `_load_checkpoint()` → skip done nodes, continue. Add `CoverageTracker.to_dict/from_dict`;
add `ManagedTask` UUID round-trip validator (`id`, `merged_from`); validate the checkpoint matches the current
node set (else fresh run). Gate via `RunConfig.enable_resumption` (default on). Delete checkpoint on success.

**Risk:** medium — serialization round-trip correctness + idempotency. **Tests:** simulate crash at node K,
resume, assert no dup tasks + full coverage.

### C2. `cancel_check` seam + filesystem status fallback (offline-now interim)

**Change:** `PipelineOrchestrator.__init__(..., cancel_check: Optional[Callable[[], bool]] = None)` defaulting to
`self.stop_event.is_set()` (no behavior change). Call it between nodes/stages. Mirror run status to a
filesystem checkpoint so partial status survives a restart even before C3. This is the clean seam C3 binds to
(Redis `cancel:{run_id}`).

### C3. Durable worker (Arq + Redis) + Postgres run state — **needs-services (Wave 2)**

**Target.** Move the run **off the web process** into a durable background worker (Render):
- `pipeline/worker.py` (Arq worker, `job_timeout≈7200s`, `on_startup=configure_litellm_once`) +
  `pipeline/jobs.py::run_pipeline_job(run_id)` reading `RunConfig` from Postgres and calling
  `PipelineOrchestrator.run()` (already the transaction boundary — minimal change).
- `ui/server.py`: enqueue (`arq.enqueue("run_pipeline_job", run_id)`) instead of `add_task`; read status from
  Postgres/Redis instead of in-memory dicts; cancel via `redis.set("cancel:{run_id}")`.
- **Fallback:** if `REDIS_URL`/`DATABASE_URL` unset → today's in-process `BackgroundTasks` + dicts (dev/CLI
  unaffected). Lands in one PR once Wave 1 (Postgres) + the worker exist.

**Constraint:** must **not** revive/modify the `BIFROST_*`/`llm_router` credential paths (Bifrost design owns
them). Job carries per-run config + the already-resolved provider, not new credential resolution.

---

## 3. Recommended sequencing

1. **A1** (resilience) — finishes C-5 honestly; without it, parallelism (B1) *amplifies* transient-failure loss.
2. **A2 + A3 + A4** (integrity) — cheap, independent, stop silent data loss.
3. **B1** (parallelism) — the wall-clock win; depends on A1/A2 so concurrent failures are handled.
4. **C1 + C2** (resumable, cancellable) — make long runs survivable in-process.
5. **C3** (durable worker) — when Wave 2 services land; the structural fix for long-running processes.

**First increment to execute:** **A1** — small, offline, TDD-able, and the highest-leverage hardening for a
multi-hundred-call run.
