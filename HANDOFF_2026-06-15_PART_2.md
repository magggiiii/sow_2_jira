# Handoff — C-5 closed live + scale-hardening plan → execute scale-hardening A1

**Generated:** 2026-06-15 (PART 2; continues `HANDOFF_2026-06-15.md`)
**Branch:** `elevation/wave-0` (HEAD = this PART_2 checkpoint commit; last content commit `97e95b5`, ~37 ahead of `main`)
**Checkpoint commit:** the `docs: session checkpoint … (PART 2)` commit landing this handoff — run `git log -1` to confirm HEAD
**Linear:** [SOW-to-Jira Elevation](https://linear.app/calibraint-ai/project/sow-to-jira-elevation-883972f170ac) (team CAL). No per-commit tickets; Linear updates still deferred to the S2J team migration (user creates team first).
**Status:** **C-5 fully closed — live-verified 6/6 on gemini-2.5-flash.** Scale-hardening for 50-60pg SOWs is planned (`SCALE-HARDENING.md`) and on the tracker. Suite **342 passed / 1 skipped**. `main` untouched, nothing pushed.

## 1. What's done (this session, all verified green before commit)

| Area | Status |
|------|--------|
| Per-agent Instructor migration — all 6 agents on `complete_structured` (classifier→critic→coverage→extraction→gap_recovery→dedup) | ✅ `0d49a63`…`99b9ab2`, TDD, zero `complete_json` call sites in `pipeline/agents/` |
| Live structured-output smoke-gate (`scripts/check_structured_output.py`) | ✅ `6dbb2d9` (+ `2815713`, `4942f16`) — `--self-test` offline, `--mode/--model` live, `--record` |
| **C-5 defect #1**: `complete_structured` never forwarded `api_key`/`api_base` → 401 | ✅ fixed `a99b8f7` (threads creds + extra_headers, only when present) |
| **C-5 defect #2**: default `Mode.TOOLS` stringified nested `list[Model]` (gemini) | ✅ fixed `a9d8b12` (provider-aware `Mode.JSON`; TOOLS kept for openai/anthropic; `S2J_INSTRUCTOR_MODE` override) |
| Drop redundant `Field(ge/le)` on confidence → unlock native structured-output on Anthropic | ✅ `4b7e001` (clamp via `UnitInterval` alone; all 6 response models emit no min/max) |
| Live gate result | ✅ **6/6 on gemini-2.5-flash AND sonnet-4.6**; gemini-2.5-pro is worse (1/6) + ~10× slower — avoid |
| Scale-hardening plan + tracker | ✅ `a869b8c` (`SCALE-HARDENING.md`), `97e95b5` (UPGRADES.html: 8th track + 4 Next cards) |
| Suite | `venv/bin/pytest tests/` → **342 passed, 1 skipped, EXIT 0** |

## 2. Branch state (this session, oldest at bottom)
```
(HEAD) docs: session checkpoint 2026-06-15 (PART 2) — C-5 closed live + scale-hardening plan
97e95b5 docs(elevation): tracker — add scale-hardening upgrades (big SOWs & long runs)
a869b8c docs(elevation): scale-hardening plan for 50-60pg SOWs & long runs
4b7e001 fix(schema): drop redundant Field(ge/le) on confidence — unlock native structured output (C-5)
4942f16 chore(scripts): smoke-gate --model override reuses settings credentials
97c33ba docs(elevation): tracker — C-5 closed live (smoke-gate 6/6 + 2 fixes)
a9d8b12 fix(harness): select Instructor JSON mode to fix nested-object stringification (C-5)
2815713 chore(scripts): smoke-gate records full failure detail for diagnosis
a99b8f7 fix(harness): thread provider api_key/api_base into complete_structured (C-5)
6dbb2d9 feat(scripts): live structured-output smoke-gate for the 6 agents (C-5)
ea0421d docs(elevation): tracker — per-agent Instructor migration shipped (C-5 complete)
99b9ab2 feat(agents): route deduplication onto Instructor complete_structured (C-5 done, TDD)
82f7cd4 feat(agents): route gap_recovery onto Instructor complete_structured (C-5, TDD)
33f28e2 feat(agents): route extraction onto Instructor complete_structured (C-5, TDD)
3937627 feat(agents): route coverage_check onto Instructor complete_structured (C-5, TDD)
0d10e2b feat(agents): route critic onto Instructor complete_structured (C-5, TDD)
0d49a63 feat(agents): route classifier onto Instructor complete_structured (C-5, TDD)
33e2ea1 docs: session checkpoint 2026-06-15 — SOW-to-Jira Elevation, Instructor foundation  (prior checkpoint)
```

## 3. The next session's mission — execute scale-hardening **A1** (then A2→C2)

Source of truth: **`.planning/elevation/SCALE-HARDENING.md`** (phased, code-anchored). Do **A1 first** — parallelism (B1) without it amplifies transient-failure task-loss.

1. **A1 — restore `complete_structured` resilience** (`core/agent_runner.py:complete_structured`, ~162-265). TDD.
   - Add `timeout` to `create_kwargs` (default 60; 3600 for Ollama: `mode==LLMMode.LOCAL` or `model.startswith("ollama/")`; env `S2J_STRUCTURED_TIMEOUT`).
   - Wrap `client.chat.completions.create(...)` in a retry loop **reusing** (import-only, no modification) `extract_retry_hint` / `compute_wait_seconds` / `is_retryable_remote_error` from `pipeline/llm_client.py` (defs at :135/:189/:197). Budget via env `LLM_STRUCTURED_MAX_ATTEMPTS` (8) / `_MAX_ELAPSED_S` (300) / `_MAX_WAIT_S` (300). Re-raise `InstructorError` only after budget exhausted (callers already catch it).
   - Tests in `tests/test_agent_runner_structured.py` (extend the instructor stub to count attempts + timing): retries-then-succeeds; budget-exhausted→`InstructorError`; non-retryable(401)→1 call; `timeout` kwarg present (60 / env / 3600 Ollama); `Retry-After` respected.
   - **Done signal:** `venv/bin/pytest tests/ -q -p no:warnings` ≥ 342 + new tests green; `complete_structured` survives a transient 429 in the stub.
2. **A2** — per-node error isolation + graceful `max_nodes` (`orchestrator.py:184-188` RuntimeError → cap+partial+`DEGRADED_CAPACITY`; wrap per-node body 256-310 in try/except, continue).
3. **A3** — batch dedup pairs (`deduplication.py:389-418` single call → chunk ~40, merge `DedupDecisionList`s).
4. **A4** — surface section truncation as warn+flag (`extraction.py`/`coverage_check.py`/`classifier.py` `max_section_chars`).
5. **B1** — parallelize the per-node loop (ThreadPoolExecutor over nodes; extract a pure `_process_node` with NO shared-state mutation; apply StateAgent+coverage+dedup sequentially after; env `SOW_NODE_CONCURRENCY` default 6, `=1` == legacy). ~4-5×.
6. **C1/C2** — per-node checkpoint+resume; `cancel_check` seam. **C3** (Arq+Redis worker) = Wave 2, needs-services.

## 4. Open behavioral risks to look for
- **A1 ordering:** do A1 before B1 — parallelism multiplies concurrent 429s; without backoff that's net task-loss.
- **B1 shared state:** `_process_node` must not touch the shared `CoverageTracker` / `all_closed_tasks` from threads; collect results, then apply `StateAgent.process` + `mark_covered` **in node order** (continuation semantics depend on order). `state.py:61` is literally "No LLM — string similarity + continues_to_next" → nodes are independent, but the *merge* is order-sensitive.
- **Rate limits under concurrency:** A1's backoff is the safety net; cap `SOW_NODE_CONCURRENCY` conservatively (6).
- **The smoke-gate `--record` files** (`data/sm_*.json`, gitignored) are authentic seed data for the INV-4 eval cassette — don't delete blindly.
- **graphify manifest is stale** — `detect_incremental` falsely reports the whole codebase deleted; use the targeted AST-merge pattern (see memory) or a deliberate code-scoped full rebuild, not bare `--update`.

## 5. Things explicitly NOT in scope (locked)
- **Do NOT touch `pipeline/llm_router.py` / `BIFROST_*` / `os.environ` credential resolution** — Bifrost design owns them. A1-C2 only *read* `llm_client` helpers; C3's worker carries already-resolved config, not new credential resolution.
- **No platform work** (Postgres/Redis/Render/auth) — C3/Wave 1/2/4 wait on provisioned services.
- **No Linear bulk edits / team / milestone creation** — bundled into the S2J team migration (user creates team in UI first). A single progress comment is fine only with go-ahead.
- **No push / PR / deploy** without explicit go-ahead. Don't relitigate the locked Bifrost/Langfuse decisions or the gemini-flash + `Mode.JSON` default (validated 6/6).
- Don't re-run gemini-2.5-pro for structured output (1/6, ~10× slower) — flash is the validated model.

## 6. Key files for the deep dive
- `.planning/elevation/SCALE-HARDENING.md` — the plan being executed (phases A1→C3, code anchors).
- `core/agent_runner.py` — `complete_structured` (A1 target), `_resolve_instructor_mode`, credential threading.
- `pipeline/llm_client.py` — `extract_retry_hint`/`compute_wait_seconds`/`is_retryable_remote_error`/`LLMTruncationError` (A1 reuses; do not modify the router path).
- `pipeline/orchestrator.py` — per-node loop `243-313` (B1), `max_nodes` `184-188` (A2), final checkpoint `393` (C1).
- `pipeline/agents/state.py` — `TaskStateAgent` (`61`: rule-based; the parallelism crux).
- `pipeline/agents/deduplication.py` — single dedup call `389-418` (A3).
- `scripts/check_structured_output.py` — the live regression gate (`--self-test` offline; `--mode custom` live).
- `tests/test_agent_runner_structured.py` — the instructor-stub pattern for A1's tests.

## 7. Test data + how to verify
- Suite: `venv/bin/pytest tests/ -q -p no:warnings` → 342 passed / 1 skipped (no network; all LLM boundaries stubbed).
- Offline wiring check: `venv/bin/python scripts/check_structured_output.py --self-test` → 6/6.
- Live (billable, needs go-ahead): `venv/bin/python scripts/check_structured_output.py --mode custom` (uses the encrypted OpenRouter key in settings → gemini-2.5-flash) → expect 6/6.
- venv: `venv/bin/python` (3.11.15); `instructor==1.15.1`, `litellm`, `pydantic` v2.

## 8. Documents to read in order
1. This handoff.
2. `.planning/elevation/SCALE-HARDENING.md` — the mission.
3. `.planning/elevation/UPGRADES.html` — living tracker (8 tracks; scale-hardening = Next).
4. `.planning/elevation/IMPLEMENTATION-PLAN.md` + `HARNESS-ARCHITECTURE.md` — the harness arc.
5. `HANDOFF_2026-06-15.md` (PART 1) — the per-agent migration handoff this continues.
6. Memory: `~/.claude/projects/-Users-magi-Documents-projects-sow-to-jira/memory/` (`elevation-render-saas-direction.md`, `linear-guidebook.md`, `keep-upgrades-tracker-updated.md`).

## 9. How to verify "done" next session
- [ ] A1: `complete_structured` has a per-call timeout + bounded retry/backoff reusing `llm_client` helpers; new tests assert retry/timeout/budget; suite ≥ 342 green; no `llm_router`/BIFROST file touched.
- [ ] (If continuing) A2-A4 land TDD, suite green between each, partial-output on `max_nodes`, dedup batched, truncation flagged not silent.
- [ ] (If continuing) B1: parallel == sequential on a fixture (same tasks/coverage/dedup); `SOW_NODE_CONCURRENCY=1` reproduces legacy.
- [ ] Each increment: re-run suite yourself, commit, update `UPGRADES.html` (move shipped items + bump metrics), refresh graphify (targeted AST-merge).
- [ ] No push/PR; Linear still deferred.
