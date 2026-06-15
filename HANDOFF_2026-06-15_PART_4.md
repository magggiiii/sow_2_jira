# Handoff — C-4 coverage restructure SHIPPED + INV-4 cassette recorder seam → live 103-node recording

**Generated:** 2026-06-15 (PART 4; continues `HANDOFF_2026-06-15_PART_3.md`)
**Branch:** `elevation/wave-0` (HEAD = the `fix(orchestrator): persist section_coverage_reports…` commit `ab4c82f`, ~60 ahead of `main` — run `git log -1` to confirm).
**Linear:** [SOW-to-Jira Elevation](https://linear.app/calibraint-ai/project/sow-to-jira-elevation-883972f170ac) (team CAL). **Still DEFERRED to the S2J team migration** — no comment posted, intentional.
**Status:** **C-4 / STEP 3.3 fully shipped (run-wide, post-dedup, confidence-gated coverage gate) AND the INV-4 cassette recorder seam shipped.** Suite **427 passed / 1 skipped, EXIT 0**. `main` untouched, **nothing pushed**.

## 1. What's done this session (all TDD, suite green before each commit)

| Area | Commit | Status |
|------|--------|--------|
| **CoverageGate primitive** — run-wide, report-level, post-dedup; reuses the shared `ConfidenceGate` (same logic `should_flag_section_incomplete` delegates to); pure / numpy-free; 9 unit tests | `fefb6a9` | ✅ |
| **Orchestrator wiring (C-4)** — removed inline per-section INCOMPLETE flagging from `_apply_node`; added `_run_coverage_verify()` applied AFTER dedup + gap recovery on the final task set; `_coverage_floor()` (env `SOW_COVERAGE_MIN_CONFIDENCE`, default = checker `min_confidence` 0.6); `COVERAGE_GATE_APPLIED` audit + `self.coverage_gate_result`; 4 tests | `8ce7310` | ✅ |
| **Tracker** — `UPGRADES.html` C-4 card + Next updated | `afa8c69` | ✅ |
| **Cassette RECORDER (INV-4)** — `pipeline/evals/recorder.py`: `CassetteRecorder.wrap()` captures per-call `(agent, node_id, output)` at the `complete_structured` seam (passthrough); `record_cassette()` ctx-mgr patches/restores the class seam; `_metadata` block; round-trips through the real `Cassette`/`make_structured_replay`; 5 tests | `7dd5da8` | ✅ |
| **Tracker** — recorder seam noted | `c7a67e9` | ✅ |
| **Resume fix (C-4 follow-up)** — persist + restore `section_coverage_reports` in the C1 extraction checkpoint (else a resumed run under-flags pre-crash sections — a regression vs the old inline path); 1 test | `ab4c82f` | ✅ |
| Suite | `venv/bin/pytest tests/ -q -p no:warnings` → **427 passed / 1 skipped, EXIT 0** | ✅ |
| Self-test | `scripts/check_structured_output.py --self-test` → **6/6** | ✅ |
| graphify | refreshed via CLI `graphify update .` after C-4 and after the recorder | ✅ |

**Files touched (whole session, vs baseline `7fcd913`):** `core/guardrails.py`, `pipeline/orchestrator.py`, `pipeline/evals/recorder.py`, `.planning/elevation/UPGRADES.html`, + 4 test files. **No `llm_router.py` / `BIFROST_*` / `os.environ` / settings / keyfile touched** (locked).

## 2. The C-4 design as shipped (so you don't re-derive it)

- **Mapping:** a surviving task → its section(s) via `ManagedTask.source_refs[].node_id`. Dedup's `_merge_tasks` does `a.source_refs.extend(b.source_refs)`, so a merged survivor carries every origin node — the gate flags it if ANY origin section is confident+missed.
- **Predicate:** `CoverageGate.report_flags(missed_count, checker_confidence, floor)` = `missed_count > 0 AND ConfidenceGate(floor).admit_value(conf)`. Same `ConfidenceGate` the per-section gate uses → no drift.
- **Why the rate drops:** report-level (not blanket) + post-dedup (merged-away duplicates are gone before the gate runs, so the rate reflects survivors). The embedding-tier corpus filter (drop misses already covered elsewhere, ≥0.85 sim) is the FURTHER drop — deliberately deferred to the feature stage (STEP 3.5 says it's not in numpy-free core).
- **INV-4 guard, verified:** healthy run GREEN (`incomplete_rate ≤ 0.40`); `coverage_missed=True` flagbomb STILL RED (`> 0.40`) through the new post-dedup gate; zero-merge + conf-zero controls still RED. `tests/test_eval_harness.py` unchanged and green.

## 3. Next session's mission — the FAITHFUL 103-node cassette (needs a live run)

The recorder SEAM exists (`record_cassette()` + `CassetteRecorder`, round-trip-tested). The remaining work is **blocked on a live LLM run** (gemini-2.5-flash + Mode.JSON on a real ~103-node SOW) — it cannot be done in the offline suite. Steps:

1. **Record.** Add `scripts/record_cassette.py` (thin): build `LLMClient` from flags exactly like `check_structured_output.py::build_client` (READ-ONLY, no `llm_router` change), set `skip_indexing` against a cached `document_tree.json` for the seed SOW, run `PipelineOrchestrator.run()` inside `with record_cassette(metadata=build_metadata(...)) as rec:` then `rec.write(path)`. Pin `SOW_NODE_CONCURRENCY=1` (determinism). `_metadata` = provider/model/mode/embed-model(MiniLM)/schema-version.
2. **The dedup-UUID problem (the key blocker — see memory `inv4-cassette-dedup-uuid`).** A recorded `DeduplicationAgent` entry embeds the recording run's fresh task UUIDs (`task_id_a/b`); a replay run generates DIFFERENT UUIDs, so the recorded decision won't match. The synthetic harness sidesteps this with the closure-shared `captured["pair"]` + `replay_or_dedup`. A FAITHFUL replay needs a **replay-time remapper** that rewrites recorded dedup `task_id_a/b` onto the current run's task ids (match by title/position/source node), OR deterministic task-id seeding. Solve this before the recorded cassette can drive dedup.
3. **Swap + flip.** Replace `harness.build_cassette` (hand-authored placeholder) with the recorded cassette behind `EvalHarness(external_cassette=…)` (small harness add: branch `Cassette.from_dict(external)` vs `build_cassette`, and use plain `cassette_replay` — NOT `replay_or_dedup` — once dedup remap lands). Then flip the band test to a **required CI gate** (INV-4), unblocking the STEP 3.7 PipelineRunner flip. The flip's own golden test must assert identical task_count/merge/flag set before vs after.

## 4. Open behavioral risks / things NOT to break
- **Don't re-add per-section flagging.** Coverage is post-dedup only now (`_run_coverage_verify`). `_apply_node` only PRODUCES + stores the report.
- **`section_coverage_reports` is stored only when `report.missed_items` is non-empty** (a report with no misses can't flag anyway). It now survives resume (`ab4c82f`).
- **Cancelled runs** return before dedup → the gate doesn't run (partial output, unflagged). Acceptable; the cancel test nulls the checker.
- **`merge_count` = `sum(len(merged_from))` over survivors** (merged tasks dropped). **`zero_conf_flags` = conf==0.0 AND LOW_CONFIDENCE.** Don't "fix" the bands.
- **Replay seam is `AgentRunner.complete_structured`** (keyword-only after self), NOT the provider. The recorder + replay both bind a `runner_self`-first function onto the class.

## 5. Locked (unchanged from PART_3)
- Do NOT touch `pipeline/llm_router.py` / `BIFROST_*` / `os.environ` creds (Bifrost owns them).
- No platform work (Postgres/Redis/Render/auth) = Waves 1/2/4. No Linear bulk edits/team/milestone. **No push/PR/deploy without go-ahead.** Don't remove `LLMMode.LOCAL`/Ollama/Bifrost. gemini-2.5-flash + `Mode.JSON` is the validated structured-output default.

## 6. Key files
- `core/guardrails.py` — `CoverageGate` (+ `CoverageGateResult`, `CoverageReportDecision`), `ConfidenceGate`.
- `pipeline/orchestrator.py` — `_apply_node` (report only, no flag), `_run_coverage_verify` / `_coverage_floor` (run-wide gate, called post-gap-recovery in `run()`), checkpoint persists `section_coverage_reports`.
- `pipeline/evals/recorder.py` — `CassetteRecorder` / `record_cassette`. `pipeline/evals/{cassette,replay,harness,bands}.py` — the gate machinery.
- `pipeline/agents/coverage_check.py` — `should_flag_section_incomplete` (the per-section predicate, still the canonical reference; CoverageGate shares its `ConfidenceGate`).
- `.planning/elevation/IMPLEMENTATION-PLAN.md` STEP 3.3/3.5/3.7/3.8, INV-4 `:1291`; `AUDIT.md:67-71` (C-4); `ARCHITECTURE.md:196-199`.

## 7. How to verify "done" next session
- [ ] `scripts/record_cassette.py` records a real ≥100-node run; cassette has `_metadata` + per-call entries.
- [ ] Dedup-UUID remap solved; replaying the recorded cassette is faithful (identical bands across runs).
- [ ] `EvalHarness(external_cassette=…)` replays it; band test flipped to a required gate (`incomplete_rate ≤ 0.40 AND merge_count ≥ 1 AND zero_conf_flags == 0 AND not DEGRADED`).
- [ ] Suite ≥ 427 green; no `llm_router`/BIFROST touched; no push/PR; Linear deferred.
