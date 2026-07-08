---
phase: 12-intelligence-layer-tightening
type: overview
plans: 6
waves: 3
locked_decisions: [D-30, D-31, D-32, D-33, D-34, D-35]
requirements: [INT-01, INT-02, INT-03, INT-04, INT-05, INT-06, INT-07]
baseline_run: data/sessions/20260512-194433-test-sow/
target_model: openrouter/google/gemini-2.5-flash
---

# Phase 12 — Plans Overview

Corrective hardening on the Wave 1-3 intelligence layer surfaced by the first end-to-end validation run (`20260512-194433-test-sow`, 509 tasks, 100% INCOMPLETE, 0 dedup merges, 100% conf=0.00 critic flags, 29% extraction JSON failure).

## Wave Structure

| Wave | Plans (parallelizable) | Sequential trigger |
|------|------------------------|--------------------|
| 1 | 12-01 (T1 json-repair foundation + CoverageTracker partial wiring), 12-02 (T2 critic fix), 12-04 (T4 adaptive dedup batching) | none — all 3 run parallel |
| 2 | 12-03 (T3 coverage advisory + tiered post-dedup filter), 12-05 (T5 extraction granularity prompt) | both depend on 12-01 (file overlap on llm_client/extraction) |
| 3 | 12-06 (T6 rerun + BEFORE_AFTER.md with 2 diagnostic distribution rows) | depends on all of 12-01..12-05 |

File overlap analysis:
- 12-01 modifies `pipeline/agents/extraction.py` (RawTask wrapper path + return-tuple signature), `pipeline/llm_client.py`, `pageindex/utils.py`, `pipeline/coverage.py` (NEW — partial state), `pipeline/orchestrator.py` (NEW — mark_partial wiring + tuple unpack), `requirements.txt`
- 12-05 modifies `pipeline/agents/extraction.py` (prompt-template granularity section) → must run after 12-01
- 12-03 modifies `pipeline/orchestrator.py` and `pipeline/agents/coverage_check.py` — Wave 2 → must run AFTER 12-01 (both touch orchestrator.py; 12-01 adds the per-node-loop mark_partial line BEFORE the mark_covered loop, 12-03 adds a NEW post-dedup Step 4C after the existing dedup step). Conflict-free as long as 12-01 lands first.
- 12-02 modifies only `pipeline/agents/critic.py` — non-overlapping
- 12-04 modifies only `pipeline/agents/deduplication.py` — non-overlapping
- 12-06 is verification-only (no source mutations)

## Dependency Graph

```
                 ┌──────────────────────┐
                 │ 12-01 (T1)           │  json-repair foundation
                 │ extraction.py        │  + partial-recovery detector
                 │ llm_client.py        │  + CoverageTracker.mark_partial
                 │ pageindex/utils.py   │    (12-ASSUMPTIONS GA-1 BLOCKER fix)
                 │ coverage.py          │  + orchestrator tuple-unpack + mark_partial call
                 │ orchestrator.py      │
                 │ requirements.txt     │
                 └──────┬───────────────┘
                        │ (extraction.py + orchestrator.py edited)
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
  ┌──────────┐    ┌──────────┐    ┌──────────┐
  │ 12-02 T2 │    │ 12-03 T3 │    │ 12-05 T5 │
  │critic.py │    │orchestr- │    │extraction│
  │          │    │ator.py + │    │.py prompt│
  │          │    │coverage_ │    │granularity│
  │          │    │check.py  │    │          │
  └─────┬────┘    └─────┬────┘    └─────┬────┘
        │               │               │
  ┌──────────┐          │               │
  │ 12-04 T4 │          │               │
  │dedup.py  │──────────┘               │
  │adaptive  │                          │
  └─────┬────┘                          │
        │                               │
        └───────────────┬───────────────┘
                        ▼
                 ┌──────────────┐
                 │ 12-06 (T6)   │
                 │ rerun on     │
                 │ test_sow.pdf │
                 │ BEFORE_AFTER │
                 │     .md      │
                 │ (+ 2 diag.   │
                 │  rows from   │
                 │  RESEARCH §9)│
                 └──────────────┘
```

Note: 12-02 and 12-04 do NOT actually need 12-01 — they touch different files. Wave 1 parallel set: {12-01, 12-02, 12-04}. Wave 2: {12-03, 12-05} (both wait on 12-01).

## Plan-to-Requirement Mapping

| Plan | Tasks | Requirements addressed | D- Decisions |
|------|-------|------------------------|--------------|
| 12-01 | T1 (json-repair + partial-recovery + CoverageTracker partial wiring) | INT-03 | D-33 (full lifecycle: detect → audit → CoverageTracker.mark_partial → get_gaps → gap_recovery) |
| 12-02 | T2 (critic confidence gating + remove LIKELY_DUPLICATE) | INT-04 | D-30, D-31 |
| 12-03 | T3 (drop per-section INCOMPLETE, add tiered post-dedup filter) | INT-01, INT-05 | D-32 |
| 12-04 | T4 (adaptive dedup batching + reconciliation) | INT-02 | D-34 |
| 12-05 | T5 (extraction granularity prompt) | INT-01 (partial), INT-04 (partial) | D-35 |
| 12-06 | T6 (verification rerun + BEFORE_AFTER.md + 2 diagnostic distribution rows + pytest green) | INT-06, INT-07 | none (verifies all) |

Coverage check: every INT-01..INT-07 appears in at least one plan's `requirements` frontmatter. ✓

## Must-Haves (Phase-level, goal-backward)

**Truths (observable on a same-SOW rerun of `test_sow.pdf` with `provider=openrouter`, `model=openrouter/google/gemini-2.5-flash`, `jira_hierarchy=epic_task`, `max_nodes=200`):**

1. Less than 5% of final tasks carry `TaskFlag.INCOMPLETE` (baseline: 100%).
2. Dedup performs ≥ 20 merges (audit-DB `DEDUP_MERGE` rows for the new run).
3. Extraction JSON failures (`EXTRACTION_ERROR` + `TASK_PARSE_ERROR` audit rows) under 2% of node count.
4. `LOW_CONFIDENCE` flag rate < 10%; every surviving flag has a `CRITIQUE_FLAGGED` audit row with `conf >= 0.5`.
5. `coverage_reports.json` has tier field on every missed item; no item exists with `tier="drop"` (drops are removed); cross-section duplicates absent.
6. `venv/bin/pytest tests/ --ignore=tests/test_hierarchical_judge.py --ignore=tests/test_phase11_evals.py` exits 0; total tests ≥ 94 + new tests.
7. `BEFORE_AFTER.md` exists in `.planning/phases/12-intelligence-layer-tightening/`, contains a 7-row table comparing baseline vs. new run on INT-01..05 plus the pytest-green and rerun-completion claims AND 2 diagnostic distribution rows (coverage tier histogram + dedup batch-halve frequency) AND the partial-recovery wiring closure summary.
8. **(NEW from 12-01 Task 1.3)** Every `EXTRACTION_PARTIAL` audit row's `node_id` is also present in a downstream `RECOVERED_TASK | RECOVERY_PARSE_ERROR | RECOVERY_ERROR | NO_GAPS_RECOVERED` audit row — proves the consumer chain is wired (no dead-letter audit signals; closes 12-ASSUMPTIONS.md GA-1 BLOCKER).

**Artifacts:**

- `requirements.txt` — contains `json-repair>=0.59`
- `pipeline/llm_client.py` — `complete_json` has json-repair fallback path; accepts `recovery_flag: dict | None = None` kwarg; contains the deliberate-two-step comment per 12-RESEARCH §1
- `pageindex/utils.py` — `extract_json` uses json-repair as final fallback; contains the matching deliberate-two-step comment
- `pipeline/agents/extraction.py` — `extract()` returns `tuple[list[RawTask], bool]` (partial_recovered flag); emits `EXTRACTION_PARTIAL` audit row when recovered count < `max(1, attempted // 2)`
- `pipeline/coverage.py` — **NEW (12-01 Task 1.3):** `CoverageTracker.mark_partial(node_id)` method; `partial: bool = False` per-node state; `get_gaps(include_partial=True)` returns `covered=False OR partial=True` nodes; `coverage_report()` exposes `partial_nodes` count
- `pipeline/orchestrator.py` — the per-node `TaskFlag.INCOMPLETE` auto-append block (12-03) removed; **NEW (12-01 Task 1.3):** per-node loop unpacks `(raw_tasks, partial_recovered)` from `extraction_agent.extract(...)` and calls `coverage.mark_partial(node_id)` if partial before the existing `mark_covered` loop; new post-dedup step (12-03) runs `coverage_checker.filter_against_corpus(self.section_coverage_reports, deduplicated)` and overwrites `coverage_reports.json`
- `pipeline/agents/critic.py` — `LIKELY_DUPLICATE` enum removed; `_apply` gates ALL flag-add paths on `critique.confidence >= self.min_flag_confidence`; constructor accepts `min_flag_confidence: float = 0.5`
- `pipeline/agents/coverage_check.py` — `MissedItem` carries `tier: str` field, post-dedup tiered filter implemented or exposed
- `pipeline/agents/deduplication.py` — `pair_batch_size: int = 30` constructor arg; `_run_batches()` and `_reconcile()` helpers; audit actions `DEDUP_BATCH`, `DEDUP_BATCH_HALVE`, `DEDUP_RECONCILE`
- `pipeline/agents/extraction.py` — `EXTRACTION_PROMPT_TEMPLATE`'s TASK GRANULARITY section contains atomic-unit rule + 2 negative examples; no hard task count cap
- `tests/test_json_repair_fallback.py` (now includes Task 1.3 CoverageTracker partial-wiring tests), `tests/test_critic_confidence_gating.py`, `tests/test_tiered_coverage_filter.py`, `tests/test_adaptive_dedup_batching.py`, `tests/test_extraction_granularity_prompt.py` — one new test file per non-verification plan
- `tests/test_extraction.py` — migrated to tuple unpacking (`tasks, partial = agent.extract(...)`) per 12-01 Task 1.2's signature change
- `.planning/phases/12-intelligence-layer-tightening/BEFORE_AFTER.md` — verification artifact with 7-row INT comparison + 2 diagnostic distribution rows (coverage tier histogram + dedup batch-halve frequency) + partial-recovery wiring closure summary

**Key links:**

- from: `pipeline/llm_client.py:complete_json` → to: `json_repair.loads` — via: `try/except json.JSONDecodeError → fallback path`, pattern: `json_repair.loads(`
- from: `pipeline/llm_client.py:complete_json` → recovery_flag dict — via: optional kwarg, set to `{"recovered": True}` on fallback fire — pattern: `recovery_flag["recovered"] = True`
- from: `pipeline/agents/extraction.py:extract` → to: `pipeline/orchestrator.py` per-node loop — via: return-tuple `(tasks, partial_recovered)`, pattern: `return tasks, partial_recovered`
- from: `pipeline/orchestrator.py` per-node loop → to: `pipeline/coverage.py:mark_partial` — via: `if partial_recovered: coverage.mark_partial(node["node_id"])` before the mark_covered loop, pattern: `coverage.mark_partial`
- from: `pipeline/coverage.py:get_gaps(include_partial=True)` → to: `pipeline/agents/gap_recovery.py:recover` — via: orchestrator Step 4B passes `coverage.get_gaps(...)` directly to `self.gap_agent.recover(gaps, self.indexer)`, pattern: `coverage.get_gaps`
- from: `pipeline/orchestrator.py` (post-dedup step) → to: `pipeline/agents/coverage_check.py:filter_against_corpus` — via: direct call after `Step 4: Deduplication`, pattern: `coverage_checker.filter_against_corpus(`
- from: `pipeline/agents/deduplication.py:deduplicate` → to: `_run_batches` + `_reconcile` — via: replacing the single-shot `complete_json` call with a batching loop, pattern: `pair_batch_size`
- from: `pipeline/agents/critic.py:_apply` → to: `self.min_flag_confidence` — via: gating every `_add_flag(task, ...)` call, pattern: `critique.confidence >= self.min_flag_confidence`
- from: `pipeline/agents/critic.py` (CritiqueIssue enum) → no link to `LIKELY_DUPLICATE` — pattern: removed; `grep "LIKELY_DUPLICATE" pipeline/agents/critic.py` exits 1
- from: `pipeline/agents/extraction.py:EXTRACTION_PROMPT_TEMPLATE` → atomic-unit rule + negative examples — pattern: `Do NOT sub-split a sprint-sized task`

## Locked Decisions (do NOT reopen)

| ID | Decision | Implemented by |
|----|----------|----------------|
| **D-30** | Remove `LIKELY_DUPLICATE` from `CritiqueIssue` enum and from the critic prompt | 12-02 |
| **D-31** | Belt-and-suspenders critic confidence: prompt-side require confidence + `_apply` gates all flag paths on `>= min_flag_confidence` (default 0.5) | 12-02 |
| **D-32** | Drop per-section `TaskFlag.INCOMPLETE` auto-append; add post-dedup tiered embed-filter (`drop≥0.85`, `likely_overlap 0.70-0.85`, `uncovered<0.70`) | 12-03 |
| **D-33** | Partial-recovery detector on json-repair fallback: mark `EXTRACTION_PARTIAL` when recovered count < `max(1, attempted // 2)`; **FULL LIFECYCLE WIRING (added post-checker per 12-ASSUMPTIONS.md GA-1 BLOCKER):** `CoverageTracker.mark_partial(node_id)` + `get_gaps(include_partial=True)` so gap_recovery actually receives partial-recovery nodes (the audit row is no longer a dead letter) | 12-01 (Tasks 1.0-1.3; Task 1.3 is the consumer-side wiring) |
| **D-34** | Adaptive dedup batching with reconciliation: `pair_batch_size=30`, halve on parse failure (min 5), reconcile in ≤2 rounds | 12-04 |
| **D-35** | Granularity prompt tune ONLY — no hard cap. Add atomic-unit rule + 2 negative examples | 12-05 |

## Claude's discretion (not pre-committed)

- Exact `min_flag_confidence` default (locked at 0.5 unless verification shows it materially helps to retune)
- Exact wording of the 2 granularity negative examples
- Whether `coverage_check.py` gets a `filter_against_corpus` method OR a new module helper — planner picks method-on-CoverageChecker for cohesion (D-32 says "use the dedup agent's MiniLM embedder" — picks pragmatic injection via `embedder` arg)
- Whether `tier` field is surfaced in Jira description (deferred per CONTEXT.md "probably yes — but a Phase 12 nice-to-have, not a must-have")

## Adversarial-probe coverage (post-revision)

This phase went through `/gsd:discuss-phase` (assumptions mode) and `/gsd:research-phase` retroactively with adversarial briefs. Findings:

- **BLOCKER (12-ASSUMPTIONS.md GA-1):** EXTRACTION_PARTIAL audit signal had no consumer — `mark_covered` would fire for any extracted task, hiding partial nodes from `get_gaps()`. **Closed by 12-01 Task 1.3** (CoverageTracker partial state + orchestrator wiring).
- **MEDIUM (12-RESEARCH.md §1):** Deliberate two-step `json.loads → json_repair.loads` pattern flagged by library author as "doubly-slow"; we retain it intentionally to preserve the `recovery_flag` discriminator. **Closed by 12-01 Tasks 1.1 + 1.2** (code comments in both `pipeline/llm_client.py` and `pageindex/utils.py`).
- **LOW (12-RESEARCH.md §9):** Additive diagnostic rows for BEFORE_AFTER.md — coverage tier histogram + dedup batch-halve frequency. **Closed by 12-06 Tasks 6.2 + 6.4** (Task 6.2 computes; Task 6.4 emits to BEFORE_AFTER.md).
- WARNING items (calibration/instrumentation reservations in 12-ASSUMPTIONS.md GA-2..GA-7) — accepted as not blocking; future-tuning notes captured in BEFORE_AFTER.md callouts.

## Verification Gate for Phase 12

Phase 12 is complete when 12-06 succeeds AND every must_have truth above is observable in the new run's artifacts AND `BEFORE_AFTER.md` exists with all 7 INT-XX rows + 2 diagnostic rows + partial-recovery wiring closure filled AND zero dead-letter EXTRACTION_PARTIAL nodes are detected end-to-end.
