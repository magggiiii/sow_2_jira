# Handoff — scale-hardening A1→C2 + INV-4 eval-gate scaffolding done → C-4 coverage restructure (STEP 3.3)

**Generated:** 2026-06-15 (PART 3; continues `HANDOFF_2026-06-15_PART_2.md`)
**Branch:** `elevation/wave-0` (HEAD = this PART_3 checkpoint commit, ~55 ahead of `main` — run `git log -1` to confirm the exact SHA)
**Checkpoint commit:** the `docs: session checkpoint … (PART 3)` commit that landed this handoff — run `git log -1`
**Linear:** [SOW-to-Jira Elevation](https://linear.app/calibraint-ai/project/sow-to-jira-elevation-883972f170ac) (team CAL). **No per-commit tickets; Linear updates remain DEFERRED to the S2J team migration** (user creates the `S2J` team in the UI first). No comment posted this session — intentional, per the locked deferral.
**Status:** **Scale-hardening A1→C2 fully shipped AND the INV-4 offline eval-gate SCAFFOLDING shipped.** Suite **408 passed / 1 skipped, EXIT 0**. `main` untouched, **nothing pushed**.

## 1. What's done (this session, all TDD, suite green before each commit)

| Area | Status |
|------|--------|
| **A1** restore `complete_structured` resilience — per-call timeout (60/3600-Ollama/`S2J_STRUCTURED_TIMEOUT`) + bounded retry/backoff reusing `llm_client` helpers (import-only) | ✅ `1b627a1` |
| **A2** per-node error isolation + graceful `max_nodes` (`cap_nodes`, `RunConfig.node_processing_strategy="degraded"`, `_process_node`/`_extract_all`, health signals) | ✅ `78b4fb8` |
| **A3** batch dedup candidate pairs (`DEDUP_BATCH_SIZE=40`, `_confirm_pairs`, `DEDUP_DEGRADED_BATCH`) | ✅ `034110a` |
| **A4** surface section truncation (`TaskFlag.TRUNCATION` + `SECTION_TRUNCATED` audit in extraction/classifier/coverage) | ✅ `a4e2fd3` |
| **B1** parallelize per-node extraction (`SOW_NODE_CONCURRENCY` default 6; split `_extract_node`(pure)/`_apply_node`(order-sensitive); **parallel==sequential proven**) | ✅ `6f41448` |
| **C1** per-node checkpoint + resume (`extraction_checkpoint.json`, `_maybe_resume`, `CoverageTracker.to_dict/restore_from`, `RunConfig.enable_resumption`) | ✅ `0030581` |
| **C2** `cancel_check` seam + filesystem `status.json` mirror | ✅ `e57a641` |
| **INV-4 scaffolding** — offline eval gate in `pipeline/evals/` (band-math + cassette/replay seam + `EvalHarness` driving real `run()` + 3 negative controls) | ✅ `9f4a183`, `9c31a88` |
| Suite | `venv/bin/pytest tests/ -q -p no:warnings` → **408 passed / 1 skipped, EXIT 0** |
| graphify | refreshed via CLI `graphify update .` after each increment (7290 nodes; `pipeline/evals/*` indexed) |

**Only C3 of scale-hardening remains** (durable Arq+Redis worker + Postgres) — needs Wave-2 services, out of offline scope.

## 2. Branch state (newest at top; since PART_2 checkpoint `3a22406`)
```
(HEAD) docs: session checkpoint 2026-06-15 (PART 3) — this commit
579bd62 docs(elevation): tracker — INV-4 offline eval gate scaffolding shipped
9c31a88 feat(evals): INV-4 offline gate harness + negative controls (test-12 scaffold)
9f4a183 feat(evals): INV-4 foundational layer — band-math + cassette + replay seam
3ff0632 docs(elevation): tracker — C2 cancel seam shipped (A1→C2 offline arc complete)
e57a641 feat(orchestrator): cancel_check seam + filesystem status mirror (C2)
de899d9 docs(elevation): tracker — C1 per-node checkpoint + resume shipped
0030581 feat(orchestrator): per-node checkpoint + resume (C1)
733edd5 docs(elevation): tracker — B1 parallel per-node extraction shipped
6f41448 feat(orchestrator): parallelize per-node extraction, equivalence-preserving (B1)
228a5fa docs(elevation): tracker — A4 truncation surfacing shipped (Phase A complete)
a4e2fd3 feat(agents): surface section truncation as warn + TRUNCATION flag (A4)
91250f8 docs(elevation): tracker — A3 batch dedup pairs shipped
034110a feat(dedup): batch candidate pairs to avoid single-call truncation (A3)
e26ae41 docs(elevation): tracker — A2 per-node isolation + graceful max_nodes shipped
78b4fb8 feat(orchestrator): per-node error isolation + graceful max_nodes (A2)
c1ae0b1 docs(elevation): tracker — A1 structured-output resilience shipped
1b627a1 fix(harness): restore complete_structured resilience — timeout + bounded retry/backoff (A1)
3a22406 docs: session checkpoint 2026-06-15 (PART 2)  ← prior handoff
```

## 3. Next session's mission — C-4 coverage restructure (STEP 3.3), then the faithful INV-4 gate

The INV-4 gate scaffolding now EXISTS and provably fails on the 3 bug classes — but it asserts against the CURRENT per-section pre-dedup coverage path. The real payoff is the C-4 fix it gates:

1. **C-4 / STEP 3.3 — move coverage post-dedup, run-wide, confidence-gated.** Today coverage runs per-node INSIDE the extract loop (`orchestrator.py` `_apply_node` → `coverage_checker.check_section` → `should_flag_section_incomplete` → flags INCOMPLETE per section). The audit (`AUDIT.md:67-71`) shows this flags ~100% of tasks on a real run. The fix (`ARCHITECTURE.md:196-199`, `HARNESS-ARCHITECTURE.md:139`, `IMPLEMENTATION-PLAN.md:781-824`): a run-wide `CoverageGate.apply` that runs AFTER dedup on the final task set, report-level, gated on `checker_confidence >= floor`. TDD; assert the INCOMPLETE *rate* drops. The partial fix (per-section confidence gate `should_flag_section_incomplete`) already landed in Wave 3-F — the remaining work is the post-dedup, run-wide restructure.
2. **Record the faithful 103-node golden cassette** from the post-3.3 codebase. The hand-authored cassette in `pipeline/evals/harness.py::build_cassette` is the placeholder; to record a real one, extend `scripts/check_structured_output.py` (or add `scripts/record_cassette.py`) to capture per-call `(agent_name, node_id, validated_output)` pairs at the `AgentRunner.complete_structured` boundary + a `_metadata` block (provider/model/mode/embed-model/schema version). Today's `--record` stores only final outputs (no call metadata) — insufficient to replay.
3. **Flip the band test to a required CI gate** once the recorded cassette measures target-state behavior, unblocking the STEP 3.7 PipelineRunner flip (the linear→typed Stage-runner). The flip's own golden test (STEP 3.7) must assert identical task_count/merge/flag set before vs after.

**Alternatives if not doing C-4:** Wave 5 cleanup (PushGate; delete unwired `integrations/jira_mcp_client.py`; move tuning knobs to `RunConfig` — but DON'T remove `LLMMode.LOCAL`/Ollama/Bifrost, locked); C3 durable worker (needs services); or **push/PR the branch** (55 ahead, all green, never pushed — needs go-ahead).

## 4. Open behavioral risks to look for
- **INV-4 gate is scaffolding, not the faithful gate.** `build_cassette` is hand-authored against the CURRENT pipeline so the bands pass today; it does NOT yet measure target-state (post-3.3) behavior. Don't mistake green INV-4 for "C-4 fixed."
- **Coverage restructure must not double-flag.** Moving coverage post-dedup means the per-section flagging in `_apply_node` should be REMOVED/disabled (else flags applied twice). The INV-4 negative control `coverage_missed=True` is your regression guard.
- **`merge_count` = `sum(len(merged_from))` over survivors** — merged tasks are dropped from the final list, so `status==MERGED` is always 0 there. Don't "fix" the band to count status.
- **Replay seam is `AgentRunner.complete_structured`, NOT the LLM provider** — the provider only has `complete`/`complete_json`. Any new offline harness must patch the runner method.
- **B1 parallelism + cassette**: the harness forces `SOW_NODE_CONCURRENCY=1` for determinism; a recorded cassette under the default (6) would consume in nondeterministic order — keep `=1` for replay.
- **Dedup determinism**: real MiniLM forms candidate pairs before the LLM call; the harness freezes `_find_candidate_pairs`. A recorded golden run must pin the embedding stack (MiniLM/sklearn/torch) or merge_count drifts.

## 5. Things explicitly NOT in scope (locked)
- **Do NOT touch `pipeline/llm_router.py` / `BIFROST_*` / `os.environ` credential resolution** (Bifrost owns them). A1-C2 + INV-4 only READ `llm_client` helpers / use the seams.
- **No platform work** (Postgres/Redis/Render/auth) — C3/Waves 1/2/4 wait on provisioned services.
- **No Linear bulk edits / team / milestone creation** — bundled into the S2J team migration (user creates team first). No progress comment without explicit go-ahead.
- **No push / PR / deploy** without explicit go-ahead. Don't relitigate the Bifrost/Langfuse decisions or the gemini-flash + `Mode.JSON` structured-output default (validated 6/6); don't re-run gemini-2.5-pro for structured output.
- Don't remove `LLMMode.LOCAL`/Ollama/Bifrost in Wave 5 cleanup (locked).

## 6. Key files for the deep dive
- `pipeline/evals/bands.py` — `compute_bands`/`compute_metrics`; the band contract (`EvalBands`).
- `pipeline/evals/harness.py` — `EvalHarness` (drives real `run()` offline), `build_cassette` (hand-authored — the C-4 swap target), `golden_tree`.
- `pipeline/evals/cassette.py` / `replay.py` — the `(agent,node_id)` cassette + the `AgentRunner.complete_structured` replay seam.
- `pipeline/agents/coverage_check.py` — `check_section`, `should_flag_section_incomplete` (the C-4 per-section gate; the run-wide gate is the restructure target).
- `pipeline/orchestrator.py` — `_apply_node` (where per-section coverage flags today), `_extract_all` (B1 two-phase), Step 4 dedup → coverage flag must move AFTER this.
- `.planning/elevation/IMPLEMENTATION-PLAN.md` (STEP 3.3/3.7/3.8, INV-4 def `:1291`), `ARCHITECTURE.md:196-199`, `HARNESS-ARCHITECTURE.md:139`, `AUDIT.md:67-71` (C-4 finding).

## 7. Test data + how to verify
- Suite: `venv/bin/pytest tests/ -q -p no:warnings` → 408 passed / 1 skipped (offline; all LLM boundaries stubbed/replayed).
- INV-4 gate: `venv/bin/pytest tests/test_eval_harness.py tests/test_eval_bands.py tests/test_eval_cassette.py -q` (the gate + negative controls + band-math; ~no network, ~1-2 min incl. MiniLM load).
- Offline structured-output wiring: `venv/bin/python scripts/check_structured_output.py --self-test` → 6/6.
- Seed data for a future recorded cassette: `data/sm_*.json`, `data/structured_smoke*.json` (gitignored; store ONLY final outputs — see PART_2 §4).
- venv: `venv/bin/python` (3.11.15); `instructor==1.15.1`, `litellm`, `pydantic` v2, `sentence-transformers` (MiniLM).

## 8. Documents to read in order
1. This handoff.
2. `HANDOFF_2026-06-15_PART_2.md` (scale-hardening plan + C-5 close).
3. `.planning/elevation/IMPLEMENTATION-PLAN.md` (STEP 3.3 coverage restructure, 3.7 flip, 3.8 eval gate, INV-4) + `ARCHITECTURE.md` / `HARNESS-ARCHITECTURE.md` / `AUDIT.md` (C-4).
4. `.planning/elevation/SCALE-HARDENING.md` (A1→C3; A1-C2 done).
5. `.planning/elevation/UPGRADES.html` — living tracker (Wave 3 78%, scale-hardening 88%).
6. Memory: `elevation-render-saas-direction.md`, `linear-guidebook.md`, `keep-upgrades-tracker-updated.md`, `graphify-refresh-method.md`.

## 9. How to verify "done" next session
- [ ] C-4: coverage runs post-dedup, run-wide, confidence-gated; per-section flagging removed (no double-flag); INV-4 incomplete-rate band reflects target-state.
- [ ] INV-4 negative controls still RED on the 3 bug classes; healthy gate GREEN.
- [ ] Suite ≥ 408 green; no `llm_router`/BIFROST file touched.
- [ ] Each increment: re-run suite yourself, commit, update `UPGRADES.html`, refresh graphify via CLI `graphify update .`.
- [ ] No push/PR; Linear still deferred.
