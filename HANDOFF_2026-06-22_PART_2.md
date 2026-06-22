# Handoff — STEP 5.4 + STEP 3.6 shipped → STEP 3.7 (PipelineRunner, from scratch)

**Generated:** 2026-06-22 (continues `HANDOFF_2026-06-22.md`)
**Branch:** `elevation/wave-0` (HEAD = the checkpoint commit below, 73 ahead of `main`; run `git log -1` for the exact SHA — amend rewrites the hash so it isn't embedded here)
**Checkpoint commit:** the most recent `docs: session checkpoint 2026-06-22 (PART 2) …` commit — run `git log -1`.
**Linear:** [SOW-to-Jira Elevation](https://linear.app/calibraint-ai/project/sow-to-jira-elevation-883972f170ac) (team CAL). **DEFERRED — no comment posted (locked).**
**Status:** **The ENTIRE offline-verifiable backlog is COMPLETE — STEP 5.4 + STEP 3.6 (a+b) + STEP 3.7 all shipped** (TDD + adversarial-review, 0 confirmed defects each). Suite **554 passed / 1 skipped, EXIT 0** (was 470). `main` untouched, **nothing pushed**. ~74 ahead of main.

> **UPDATE (post-checkpoint):** §3 below was written when 3.7 was still pending; **3.7 is now DONE** (`7ff37d4` — `core/pipeline/` substrate + `run()` refactored into 8 `_stage_*` methods + `run_via_pipeline()` behind `RunConfig.use_pipeline_runner` + offline equivalence golden `tests/test_pipeline_runner_equivalence.py`; `run()` NOT deleted). The PART_1 premise that `core/pipeline/runner.py` "existed from W0" was false — it was built this session from scratch. **Remaining elevation work is all BLOCKED** (see §5): STEP 3.8 cassette + 3.7 final `run()` deletion need a LIVE run; Waves 1/2/4 + 3.9 need provisioned services. A fresh session has no offline-buildable increment without unblocking one of those — confirm the wall with the user first.

## 1. What's done (this session)

| Increment | Commit | Verify | Status |
|-----------|--------|--------|--------|
| **STEP 5.4 — tuning knobs → `RunConfig`**: 11 `Optional` typed fields on `RunConfig` (`models/schemas.py`), each defaulting to `None`. Orchestrator resolves every knob as **config-field > env / app_config > original default** (`__init__` + `_node_concurrency`/`_coverage_floor`/`_coverage_filter_enabled`). `None` reproduces prior behavior byte-for-byte (env still a fallback; legacy checkpoints load). | `1ed67aa` | 21 tests | ✅ review 0 defects |
| **STEP 3.6a — prompts registry**: byte-faithful move of the 6 agents' 12 system/user prompt constants into `prompts/*.v1.txt`, served by `prompts/registry.py` (`load(name)`, lru_cached). Agents reference `registry.load(...)`; literal lives once. Frozen sha256 snapshots pin all 12. | `5f597d7` | 39 tests | ✅ review 0 defects |
| **STEP 3.6b — AgentSpec adoption**: new `AgentRunner.run_structured(spec, payload, *, node_id="")` (spec-driven complement to `run()`, forwards identical kwargs to `complete_structured`). All 6 agents build `self.spec = AgentSpec(...)` in `__init__` and call `run_structured`. | `86f5f4c` | 16 tests | ✅ review 0 defects |
| Suite | `venv/bin/pytest tests/ -q -p no:warnings` → **546 passed / 1 skipped, EXIT 0** | | ✅ |
| graphify | `graphify update .` after each increment (7628 nodes) | | ✅ |
| Tracker | `.planning/elevation/UPGRADES.html` — 3 Shipped cards added, metrics bumped (546 tests / 35 upgrades / 41 commits), Harness-core 98% | | ✅ |

**Verify count rigorously, not from dots:** the `rtk` output filter strips pytest's summary line. Use `--junit-xml=/tmp/x.xml` then parse, or trust `EXIT=0` + the JUnit `tests/failures/errors/skipped` attrs. (470 baseline + 21 + 39 + 16 = 546.)

## 2. Branch state (newest first; since prior handoff `b40eb12`)
```
(HEAD) docs: session checkpoint 2026-06-22 (PART 2)  ← this commit (run git log -1 for SHA)
86f5f4c refactor(agents): adopt AgentSpec + runner.run_structured across the 6 agents (STEP 3.6b)
5f597d7 refactor(prompts): byte-faithful move of 6 agents' prompts into a registry (STEP 3.6a)
1ed67aa feat(config): tuning knobs -> RunConfig typed fields (STEP 5.4)
b40eb12 docs: session checkpoint 2026-06-22  ← prior handoff
```

## 3. The next session's mission — STEP 3.7 (THE correction is here)

> ⚠️ **The prior handoff was WRONG about 3.7.** It claimed "`core/pipeline/runner.py` + registry already exist from W0." **They do NOT exist.** `git log --all -- 'core/pipeline/*'` returns nothing; `core/pipeline/` is absent. What W0 actually shipped: `core/{ports,results,agent_runner,agent_spec,guardrails,health}.py`. STEP 3.7 is a **from-scratch XL build** (IMPLEMENTATION-PLAN rates `orch-6` = the runner as **XL**), not a wiring task. Scope it as a design-first session.

**What 3.7 needs (per IMPLEMENTATION-PLAN harn-2/harn-3/registry/orch-6/harn-21 + test-13):**

1. **`core/pipeline/` package** (new): `stage.py` (a `Stage` unit — name + `run(ctx)`; a lean `StageStatus`/outcome — NOTE: `core/results.py` already defines a `StageResult` used by `AgentRunner.run`; decide whether to reuse or define a distinct pipeline-phase type, don't duplicate blindly), `context.py` (`PipelineContext` holding the run state), `registry.py` (`StageRegistry`, ordered name→stage), `runner.py` (`PipelineRunner`).
2. **Extract `run()`'s phases into stages** WITHOUT touching `run()`. `run()` (`pipeline/orchestrator.py:713`) decomposes cleanly into ~7 phases that mostly wrap EXISTING orchestrator methods:
   - PageIndex+cap+node_index+CoverageTracker (`_build_or_load_tree` → `cap_nodes` → persist `node_index.json` → `CoverageTracker(nodes)`)
   - Extract loop (`_maybe_resume` → `_extract_all` → `state_agent.close_all_remaining`); **early-return on `cancelled`**
   - Dedup (`dedup_agent.deduplicate`)
   - Gap recovery (if `report["gap_nodes"]>0`: `gap_agent.recover` → re-process → re-dedup)
   - Coverage gate (`_run_coverage_verify` — mutates tasks in place)
   - Health (`build_health_report(...)`)
   - Save (`pipeline_output.json` + `coverage_reports.json` + `_delete_extraction_checkpoint`)
3. **PEV order is ALREADY satisfied** — the C-4 restructure (STEP 3.3) already moved coverage to post-dedup, so `run()`'s current order IS the PEV order. The runner mirrors `run()`'s existing sequence; `harn-21` (PEV reorder) is effectively already done. This makes equivalence achievable by construction.
4. **Behind a flag**: add e.g. `RunConfig.use_pipeline_runner` (default `None`/off) or an env knob; a thin top-of-`run()` delegation `if self._use_runner(): return self._run_via_pipeline()` is acceptable (that's not deletion). **Do NOT delete `run()`** — its final deletion is gated on STEP 3.8 (live cassette, blocked — see memory `inv4-cassette-dedup-uuid`).
5. **OFFLINE equivalence golden test** (`test-13`): reuse `pipeline/evals/harness.py::EvalHarness` machinery — it already drives the real `run()` offline (golden tree + `build_cassette` + `mock.patch.object(AgentRunner, "complete_structured", replay_or_dedup)` + `_find_candidate_pairs` patch + `SOW_NODE_CONCURRENCY=1`). Run the SAME deterministic setup through both `orch.run()` and `PipelineRunner(orch).run()`; assert identical **task_count / merge_count / flag set** from the two `pipeline_output.json`s.

**Cadence (unchanged, proven 3× this session):** design *workflow* (3.7 HAS real forks — stage abstraction, StageResult overlap, context shape, flag, cancel early-exit) → **TDD in main loop** (RED→GREEN, re-run suite yourself) → adversarial-review *workflow* → commit → update `UPGRADES.html` → `graphify update .`. Suggest splitting 3.7 into **3.7a** (scaffolding + runner + equivalence test) and a later **3.7b** if needed.

## 4. Open behavioral risks
- **STEP 5.4 precedence**: config-field beats env when BOTH set (config primary, env fallback). The eval harness relies on the env path (`SOW_NODE_CONCURRENCY=1` via env, `RunConfig.node_concurrency=None`) — keep it that way or the INV-4 determinism breaks. `node_concurrency` config is floored `max(1, …)`.
- **3.6 byte-faithfulness** is pinned by frozen sha256 in `tests/test_prompts_registry.py`. If you edit a `.txt`, update its frozen hash *intentionally* (regenerate + eyeball the diff). `prompts/` ships via Dockerfile `COPY . .` (not in `.dockerignore`).
- **3.7 equivalence is the whole safety net.** The runner MUST reproduce `run()`'s exact artifacts. Watch: the early-return-on-cancel path, the gap-recovery re-dedup, in-place coverage mutation, and the save-side `health`/`coverage_reports` artifacts. If the equivalence test can't go green, the runner is wrong — do NOT relax the assertion.
- **`HIERARCHY_CONTEXT`** (extraction) is still an inline module dict, intentionally NOT moved to the registry (it's prompt-fragment data, not a top-level prompt). Fine for v1.

## 5. Things explicitly NOT in scope (locked)
- **Service-blocked:** Waves 1 (Postgres/secrets), 2 (Redis worker/OAuth), 4.2 (Render), STEP 3.9 (pgvector). Designed, can't be verified offline.
- **Live-run-blocked:** STEP 3.8 faithful 103-node cassette (real gemini + dedup-UUID remap — memory `inv4-cassette-dedup-uuid`); the STEP 3.7 *final* `run()` deletion is gated on it. 3.7's behind-a-flag runner + offline equivalence test IS in scope.
- **STEP 5.1 (remove `LLMMode.LOCAL`/Ollama/Bifrost) — LOCKED.** Don't remove.
- Do NOT touch `pipeline/llm_router.py` / `BIFROST_*` / `os.environ` credential paths. No push/PR/deploy without go-ahead. No Linear comment (deferred). `gemini-2.5-flash` + `Mode.JSON` is the validated structured-output default.

## 6. Key files for the deep dive
- `pipeline/orchestrator.py` — `run()` at **L713–957** (the sequence to mirror); `__init__` L72+ (STEP 5.4 knob resolution); the phase methods `_build_or_load_tree`/`_extract_all`/`_apply_node`/`_run_coverage_verify`/`_maybe_resume`.
- `core/agent_runner.py` — `run()` (StageResult, complete_json), `run_structured()` (NEW, 3.6b), `complete_structured()`. `core/agent_spec.py` — `AgentSpec` + `render()`. `core/results.py` — existing `StageResult` (watch the naming overlap for 3.7).
- `prompts/registry.py` + `prompts/*.v1.txt` — the 3.6a registry.
- `pipeline/evals/harness.py` — `EvalHarness.run()`: the deterministic offline driver to reuse for the 3.7 equivalence test. `pipeline/evals/{bands,cassette,replay,recorder}.py`.
- `.planning/elevation/IMPLEMENTATION-PLAN.md` — search `harn-2`,`harn-3`,`registry.py`,`orch-6`,`harn-21`,`PipelineRunner`,`PEV` (L160/198/214/219/249/789/793). `UPGRADES.html` (living tracker), `AUDIT.md`.

## 7. Test data + how to verify
- Suite: `venv/bin/pytest tests/ -q -p no:warnings` → 546 passed / 1 skipped (offline; LLM stubbed/replayed). Summary line is eaten by `rtk` → use `--junit-xml` + parse, or trust EXIT code.
- New this session: `tests/test_runconfig_tuning_knobs.py`, `tests/test_prompts_registry.py`, `tests/test_agent_runner_run_structured.py`, `tests/test_agent_spec_adoption.py`.
- INV-4 gate: `tests/test_eval_harness.py`. Structured-output wiring: `venv/bin/python scripts/check_structured_output.py --self-test` → 6/6.
- venv: `venv/bin/python` (3.11.15); `instructor`, `litellm`, `pydantic` v2, `sentence-transformers` (MiniLM).

## 8. Documents to read in order
1. This handoff. 2. `HANDOFF_2026-06-22.md` (prior — but treat its §3 3.7 substrate claim as WRONG; this doc §3 supersedes it). 3. `.planning/elevation/IMPLEMENTATION-PLAN.md` (3.7 change-ids above) + `UPGRADES.html` + `AUDIT.md`. 4. Memory: `elevation-progress`, `inv4-cassette-dedup-uuid`, `keep-upgrades-tracker-updated`, `graphify-refresh-method`, `elevation-render-saas-direction`.

## 9. How to verify "done" next session
- [ ] STEP 3.7a: `core/pipeline/` package (stage/context/registry/runner) created; `run()` phases extracted as stages WITHOUT touching `run()`'s body (beyond an optional flag delegation); `run()` NOT deleted.
- [ ] Offline equivalence golden test: `run()` vs `PipelineRunner` on the synthetic cassette → identical task_count / merge_count / flag set. Green.
- [ ] Runner behind a flag (default off → legacy `run()` unchanged for all existing callers/tests).
- [ ] Suite ≥ 546 green; adversarial-review workflow → 0 confirmed defects; commit; `UPGRADES.html` updated; `graphify update .`.
- [ ] No `llm_router`/BIFROST/`os.environ`-cred file touched; no push/PR; Linear deferred; `LLMMode.LOCAL`/Ollama/Bifrost untouched.
