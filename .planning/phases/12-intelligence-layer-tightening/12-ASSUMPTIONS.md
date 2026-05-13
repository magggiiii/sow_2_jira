# Phase 12 — Assumptions Analysis (retroactive discuss-phase pass)

**Mode:** Adversarial probe of locked decisions D-30..D-35 + gray-area surfacing
**Performed:** 2026-05-13
**Status:** 5 of 6 locked decisions hold; 1 at-risk (D-33); 7 new gray areas identified (1 BLOCKER, 4 WARNING, 2 INFORMATIONAL)

---

## 1. Locked-decision probes

### D-30 — Remove `LIKELY_DUPLICATE` from `CritiqueIssue`

**Probe 1: Is critic-side dedup truly redundant after D-34?**
Dedup is run-wide AFTER all extractions (`pipeline/orchestrator.py:309-321`, Step 4). The critic runs PER-SECTION (line 273-276), so it only sees a section's tasks. Two questions:

a) Can the critic catch intra-section duplicates that the run-wide dedup would also catch? Yes — and the dedup agent's MiniLM embedding cosine threshold is 0.85 (`pipeline/agents/deduplication.py:54`). For any pair the critic would flag, dedup will see them again (they're in the same `tasks` list passed to `dedup_agent.deduplicate(all_closed_tasks)`).

b) But the baseline showed `175 likely_duplicate` flags. Where did dedup get them? Dedup gave up silently on JSON parse failure. With D-34 + json-repair, dedup will not be silent. The critic's claim was redundant signal, not unique signal.

**Probe 2: Are there cross-section duplicates the critic catches that dedup misses?**
No — the critic only sees one section at a time. A cross-section duplicate (same task in section A and B) is invisible to the critic. Only dedup sees both. The 40 cross-section duplicate descriptions found in `coverage_reports.json` (count >=2) confirm this is dedup's responsibility.

**Probe 3: Do reviewers lose signal?**
Per-section duplicates and cross-section duplicates carry different reviewer information. With D-30, every duplicate is treated as cross-section by default — reviewers no longer learn that "this section had 3 redundant copies of the same task." That's a small loss of signal granularity. But the dedup agent's audit log (`DEDUP_MERGE` with `task_id_a` and `task_id_b`) preserves the underlying merge events, so reviewers can reconstruct intra-section dupes if needed.

**Evidence:** `pipeline/agents/critic.py:389` adds LOW_CONFIDENCE for both VAGUE_TITLE and LIKELY_DUPLICATE — they share a flag; removing LIKELY_DUPLICATE doesn't lose a distinct flag color. `sqlite3 data/audit.db ... CRITIQUE_FLAGGED` returns 175+1+51=227 rows; removing the 176 likely_duplicate rows leaves 51 too_broad flags, which all also had conf=0.00. **Holds.**

**If at-risk:** Would need to add a `DEDUP_SECTION_LOCAL` audit row in the dedup agent when both task IDs share `source_refs[0].node_id`. Not currently planned. Acceptable loss; reviewers have audit-row reconstruction.

---

### D-31 — `min_flag_confidence=0.5` + prompt rewrite (belt-and-suspenders)

**Probe 1: Why 0.5 specifically?**
0.5 is the midpoint of [0,1]. CONTEXT.md says "may tune during plan-checker". The default is empirically untested. Auto-fix paths use 0.8 (`pipeline/agents/critic.py:154`), which is also magic-numbered but at least matches the legacy `SOW_CRITIC_THRESHOLD` env semantic. No empirical basis appears in CONTEXT.md or any plan.

**Probe 2: Gemini Flash calibration is unknown.**
The baseline's ONE non-zero CRITIQUE_AUTO_FIX row (`untestable_ac conf=0.90`) is statistically insignificant (n=1). We have NO evidence about Gemini's calibration when forced to emit confidence on non-fix issues. The prompt rewrite (D-31a) tells the LLM "anything below 0.5 will be DROPPED" — this MAY pressure the LLM to systematically inflate confidence above 0.5 to be heard, defeating the gate.

**Probe 3: Could the new prompt produce systematically inflated confidence?**
Yes. By telling the LLM the threshold, the LLM may pattern-match by emitting 0.55-0.65 for borderline cases that should have been 0.3-0.4. The "anchor effect" is well-documented in LLM prompt research. The plan locks the threshold disclosure in (12-02-PLAN.md line 132: "Anything below 0.5 will be DROPPED from the report").

**Evidence:** Baseline 97.8% of extracted tasks have `confidence >= 0.8` from the extraction agent's own self-report, suggesting Gemini overconfidence is the norm on this codebase. **Likely:** if the LLM is honest, 0.5 may be too HIGH (cuts real flags); if the LLM games the threshold, 0.5 may be too LOW (admits noise). No way to know without the rerun. **Holds with reservation** — verification rerun (T6) MUST report the conf distribution to enable a follow-up tune.

**If at-risk:** Plan 12-06's metric ("conf=0 count = 0") can be trivially satisfied by an LLM gaming the threshold. Need a stronger metric: the distribution of `conf` values across CRITIQUE_FLAGGED rows. See GA-3.

---

### D-32 — Tiered embed filter (≥0.85/0.70/<0.70), post-dedup, run-wide

**Probe 1: MiniLM weaknesses on domain-specific phrases.**
MiniLM-L6-v2 is trained on general-purpose semantic similarity (paraphrase pairs, NLI). It's known to produce high cosine similarity (>0.85) on token-overlap-heavy pairs that are semantically distinct in domain. Concrete risk: "Deploy frontend service" vs "Deploy backend service" share 2 of 3 tokens. From baseline data: the section `0058` extracted 15 tasks, several starting with "Integrate <X> API for <Y>" (4 such tasks). These will pairwise score ≥0.85 against each other on MiniLM — but they ARE distinct.

**However:** the filter compares MISSED items (from coverage_check) against FINAL TASK CORPUS — not extracted-task vs extracted-task. A missed item "Implement Stripe charge endpoint" against a final task "Implement Stripe charge endpoint" SHOULD score near 1.0. The concern is the reverse: a genuinely-missed item ("Configure Stripe webhook listener for refunds") could score 0.85+ against a similar-token-but-distinct extracted task ("Configure Stripe webhook listener for charges"), and be incorrectly DROPPED. False-drop rate is unknown.

**Probe 2: Where do 0.85 and 0.70 come from?**
0.85 matches the dedup threshold (`pipeline/agents/deduplication.py:54`). CONTEXT.md D-32 is silent on the empirical basis for 0.70. No calibration data on SOW task pairs. 0.70 is plausible (typical "weak similarity" cutoff in IR literature) but unvalidated for THIS corpus.

**Probe 3: Stale references / corpus correctness.**
The filter is called in 12-03's Step 4C, AFTER dedup and AFTER gap_recovery + final re-dedup (orchestrator lines 309-355). The corpus passed to `filter_against_corpus(self.section_coverage_reports, deduplicated, ...)` is the FINAL post-dedup-with-gap-recovery list. Good — no stale references.

**Probe 4: Cross-section conflation.**
A missed_item from section 0006 scoring 0.90 against an extracted task in section 0008 gets DROPPED. Is that what we want? **Yes, per D-32 verbatim:** "run-wide scope eliminates cross-section duplicates." This is intentional. But if reviewers want to know "section 0006 has an uncovered deliverable that was extracted from section 0008 — possibly mis-attributed," the new design hides this. The audit row `COVERAGE_TIER_DROP` (12-03-PLAN.md line 242) preserves the section_id of the drop, so a determined reviewer can reconstruct. Acceptable.

**Evidence:** Baseline `coverage_reports.json` has 618 misses, 573 distinct, 40 descriptions appearing in ≥2 sections; cross-section conflation is REAL and run-wide filter solves it. **Holds** with reservation on Probe 1 (false-drop risk on token-overlap-heavy distinct deliverables).

**If at-risk:** Could tier into 3 cuts (drop ≥0.92, likely_overlap 0.75-0.92, uncovered <0.75) to be more conservative on drops. Not recommended without empirical data — the current 0.85/0.70 is defensible and consistent with dedup.

---

### D-33 — Partial-recovery threshold `max(1, attempted // 2)` on json-repair fallback

**Probe 1: Is `attempted` reliable?**
Plan 12-01 derives `attempted` from the LLM's scratchpad via regex `(\d+)\s+(?:atomic\s+units|tasks|tickets|items|deliverables)` (12-01-PLAN.md line 333). This is BRITTLE:

a) The model isn't required to mention a count. Plan 12-01 acknowledges this and falls back to "always log partial" when `attempted is None`.

b) Scratchpad can lie. The model may say "10 atomic units" then emit 3, because it ran out of context. Or say "10 atomic units" and emit 12 because it changed its mind. Or never mention a number. Or mention an unrelated number.

c) The regex captures the FIRST integer + unit. If scratchpad mentions "this section spans 30 pages" before "I'll extract 5 tasks", the regex returns 30 — leading to a false partial-recovery flag.

d) Real baseline scratchpads show natural-language reasoning like "I will focus on the core implementation for each" (`sqlite3 data/audit.db ... EXTRACTION_SCRATCHPAD`) — no count mentioned. The fallback path (always-partial-on-recovery) will fire often.

**Probe 2: Is 50% the right cutoff?**
No empirical basis cited. The threshold catches catastrophic truncation (15/30 recovered) but misses subtle truncation (19/20 recovered). Plan 12-01 line 313 implements `is_partial = (attempted is None) or (recovered_count < threshold)` — so subtle truncation is silently accepted.

**Probe 3: Does gap_recovery actually pick up EXTRACTION_PARTIAL nodes?**
**CRITICAL FINDING — this is a BLOCKER.** Read `pipeline/coverage.py:29-42`: `get_gaps()` returns ONLY nodes with `covered=False`. A node is marked covered (`mark_covered`, line 24) whenever ANY task is extracted for it. A partial-recovery node has `len(raw_list) > 0`, which means `coverage.mark_covered` fires in `pipeline/orchestrator.py:291-292`, marking the node `covered=True`. Therefore gap_recovery will NEVER pick up an EXTRACTION_PARTIAL node — the audit row is logged but no recovery action happens.

The plan claims (12-01-PLAN.md line 39): "mark `EXTRACTION_PARTIAL` in audit + state so gap_recovery picks the node up." But there is no `+ state` change. No modification to `CoverageTracker`. No modification to `GapRecoveryAgent.recover()`. The audit row is a dead letter.

**Evidence:** `pipeline/coverage.py:24-27` (mark_covered) is unconditional on `task_ids` being non-empty; `pipeline/coverage.py:34-42` (get_gaps) uses only `entry["covered"]`. No code in 12-01-PLAN.md adds a "partial" mark to CoverageTracker or reads EXTRACTION_PARTIAL in gap_recovery. **At-risk — D-33's stated benefit is unrealized.**

**If at-risk (and it IS):**
Either (a) extend `CoverageTracker` to have a `mark_partial(node_id)` method and modify `get_gaps()` to include partial nodes; or (b) accept that D-33 is just an audit signal (not gap-recovery trigger) and revise CONTEXT.md / 12-01-PLAN.md to remove the "gap_recovery picks it up" claim. The current plan promises behavior the code can't deliver.

---

### D-34 — Adaptive batching (default=30, halve to min=5, max 2 reconciliation rounds)

**Probe 1: Where does 30 come from?**
No empirical basis. CONTEXT.md says "Single-shot dedup on 37+ pairs blows out Gemini Flash's JSON reliability." The baseline failed at a 64362-token completion request (`sqlite3 ... LLM_CALL ... DeduplicationAgent`). 30 pairs at ~500 tokens/pair ≈ 15000 tokens — well under Gemini Flash's 8k output limit. **30 is plausible but unverified.**

**Probe 2: Reconciliation convergence proof.**
Pathological case: A and B above threshold. After merge, A's vector remains (merge keeps A, drops B). C below threshold to B but now scored against A — could happen since A absorbed B's content via `_merge_tasks` (lines 410-448 of dedup), which appends source_refs but does NOT re-embed A's text. So A's embedding is STALE after merge — the reconcile call re-encodes the survivor texts (`_get_embedding_texts(current_survivors)`, 12-04-PLAN.md line 377), so it re-derives A's text from `task.title + task.short_description`. Title and description haven't changed (merge only appends `source_refs`, `acceptance_criteria`, `deliverables`). So A's embedding is essentially the same as pre-merge → if C wasn't near A's title pre-merge, it won't be near now. **Reconciliation may catch fewer transitive merges than the plan implies.**

**Probe 3: 2-round cap convergence.**
Pathological case: every round produces new pairs (artificially). The cap of 2 is hard. After 2 rounds, residual duplicates remain. The plan does NOT log a "TAIL_REMAINING" audit row when the cap is hit. Reviewers see no signal that more pairs were waiting.

**Probe 4: Audit log inflation.**
A 200-pair scenario at batch=5 (degraded) = 40 batches × 1+ audit row per batch (DEDUP_BATCH) + up to 200 DEDUP_KEEP_BOTH rows + up to 2 reconcile rounds × N more = ~280 audit rows for one dedup pass. Baseline dedup logged 0 rows (failed silently). Phase 11 has 5757 total audit rows across 3 runs — adding 280/run scales linearly. After 100 runs, +28000 audit rows. **Not a crisis but not free.** The audit DB is currently 2.1MB; expected growth is ~10MB/100-run, well within SQLite limits.

**Evidence:** `pipeline/agents/deduplication.py:410-448` shows `_merge_tasks` does NOT mutate title/description; embedder texts use only those (line 99). **Holds with reservation** on Probe 2 (reconciliation may be less effective than implied).

**If at-risk:** Add a `DEDUP_TAIL_REMAINING` audit row when the 2-round cap is hit with new_pairs still emerging. Trivial 3-line add to `_reconcile`. Not a blocker.

---

### D-35 — Granularity prompt tune ONLY (no hard cap)

**Probe 1: How do we measure "granularity improved"?**
Success Criteria don't include a metric. The closest proxy is INT-01 (INCOMPLETE rate <5%) — but that's driven by D-32, not D-35. The plan's success_criteria says "fewer 3-and-5-way sub-step splits" without operationalizing it. **No measurable signal.**

**Probe 2: Negative examples teach the model to NOT split design/implement/test.**
But the baseline shows ONLY 1 of 62 sections has a design+implement+test pattern (the `0026` Gold Price Oracle smart contract section, where design and implementation are legitimately separate work items — exactly the exception the plan calls out). The pathological pattern the plan targets is RARE in baseline. **The fix may not move the needle.**

**Probe 3: Legitimate design-only-vs-implement deliverables.**
Plan 12-05's negative example 1 says: BAD = ["Design login screen UI", "Implement login screen", "Test login screen"]. But many SOWs explicitly separate discovery/design contracts from build contracts — and the prompt does call out "The exception: if a section explicitly scopes design and implementation as separate work items..." (12-05-PLAN.md line 101). Good — the prompt acknowledges the exception. But the negative example header still primes the model toward conflation. **Risk: model may now merge legitimately-distinct design and build tasks.**

**Probe 4: Could the prompt reduce task count without improving granularity?**
Yes. If the model interprets "atomic unit" as "be terser", it could ship fewer-but-broader tasks. There's no AC count or scope-text-length floor to prevent this. **Quality could degrade in the opposite direction (too-broad tasks).** The critic's TOO_BROAD flag is the safety net here — but critic confidence on too_broad in baseline was 0.0 across the board, so the safety net hasn't been load-tested.

**Evidence:** Baseline 509 tasks across 62 sections = mean 8.2 tasks/section. Top section has 15 tasks ("Configure APM dashboards..." through "Configure API gateway to enforce role permissions") — these are 15 distinct deliverables, NOT 15 sub-steps of one feature. The granularity claim in CONTEXT.md ("3-task split of design/implement/test for one screen; 5-task split of payment flow sub-steps") **is not strongly evidenced in the baseline output.** D-35 may be solving a phantom problem.

**Holds with significant reservation** — applying it won't hurt, but the expected benefit is overstated.

**If at-risk:** Track a follow-up metric: mean tasks/section across baseline vs new run, and qualitative review of 3 dense sections for sub-step patterns.

---

## 2. Newly-surfaced gray areas (not in 12-CONTEXT.md)

### GA-1 (BLOCKER): EXTRACTION_PARTIAL has no consumer — gap_recovery cannot see it

**The unstated assumption:** Plans 12-01 (D-33) assume that emitting an `EXTRACTION_PARTIAL` audit row will cause gap_recovery to revisit the node.

**Why it matters:** The whole point of D-33 is that "a 22-task section becoming 14 tasks 'successfully' is worse than failing — gap_recovery won't fire." Without the consumer wired up, D-33 logs a signal that NOTHING acts on. INT-03's auxiliary benefit — "partial recoveries get backfilled" — is unmet.

**Probe finding:**
- `pipeline/coverage.py:24-27`: `mark_covered(node_id, task_id)` is called for every task emitted (orchestrator.py:291-292).
- `pipeline/coverage.py:29-42`: `get_gaps()` returns ONLY nodes with `covered=False`.
- A partial-recovery node has at least 1 task → `covered=True` → invisible to `get_gaps()`.
- 12-01-PLAN.md mentions "EXTRACTION_PARTIAL in audit + state" but introduces NO state change beyond the audit row.
- Nothing in `pipeline/agents/gap_recovery.py` reads EXTRACTION_PARTIAL.

**Recommended decision:** Either:
(a) **Wire the consumer.** Add `CoverageTracker.mark_partial(node_id)` and modify `get_gaps()` to also include partial nodes. Add a new task to 12-01 (Task 1.3): "Mark node partial in CoverageTracker so gap_recovery includes it." This is ~10 lines of code + 1 test.
(b) **Re-scope D-33.** Acknowledge it's an audit-only signal for now; remove the "gap_recovery picks the node up" promise from CONTEXT.md, 12-01-PLAN.md, and any verification claim in 12-06. INT-03 then has to stand on json-repair alone.

**Severity:** **BLOCKER** for the literal D-33 promise. Option (a) is the cleanest fix and trivial to add. Recommend doing it in 12-01 (extend Task 1.2 with the CoverageTracker hook).

---

### GA-2 (WARNING): Critic threshold gaming — `min_flag_confidence=0.5` disclosure invites prompt anchoring

**The unstated assumption:** The LLM will emit honest confidence values in the 0-1 range and the threshold gate is a clean filter.

**Why it matters:** Plan 12-02 line 132 explicitly tells the LLM: "Anything below 0.5 will be DROPPED from the report." LLMs are well-documented to anchor to disclosed thresholds. If Gemini Flash systematically emits 0.55-0.65 for everything (to clear the gate), the threshold becomes a no-op AND the conf distribution loses calibration meaning.

**Probe finding:**
- Baseline: 97.8% of EXTRACTED tasks have `confidence >= 0.8` (Gemini overconfidence pattern, distinct context but same model).
- The new critic prompt rewrites confidence semantics for the LLM — there's no calibration data on what Gemini will produce.
- 12-06's verification metric ("conf=0 count = 0") is satisfied by EITHER honest >0.5 confidence OR gaming. The two are indistinguishable.

**Recommended decision:**
Add to 12-06 verification (Task 6.2) a histogram of conf values across CRITIQUE_FLAGGED rows. Acceptance: conf values should NOT cluster in a narrow band just above 0.5 (e.g., >70% of flags at 0.5 <= conf <= 0.6 would be evidence of gaming). The Python script in 12-06-PLAN.md already extracts `m = re.search(r"conf=([\d.]+)", detail)`. Extend to bin into [0.5-0.6, 0.6-0.7, 0.7-0.8, 0.8-0.9, 0.9-1.0] and print the distribution.

**Severity:** WARNING. The fix doesn't change the locked decision; it adds verification rigor. Catch-it-now is cheaper than tune-it-later.

---

### GA-3 (WARNING): Verification BEFORE_AFTER table conflates "task count change" with "fix correctness"

**The unstated assumption:** A baseline task count of 509 and a new task count of N can be apples-to-apples compared.

**Why it matters:** Plan 12-06's `BEFORE_AFTER.md` table shows percentages (INCOMPLETE rate, LOW_CONFIDENCE rate). These are RATES, not counts — so they're robust to the task-count change. Good. But the underlying TOTAL changes:

- D-34 (dedup actually merges) → fewer tasks (≥20 merges = ~5% reduction).
- D-32 (no per-section INCOMPLETE) → no task change, just flag change.
- D-35 (granularity tune) → potentially fewer or more tasks.

If new run has N=400 tasks with 5% INCOMPLETE (20 tickets) vs baseline N=509 with 100% INCOMPLETE (509 tickets), the rate comparison reads PASS. But "how many tasks should there be?" is unanswered. The current plan has NO ground-truth task count to compare against. **The metrics validate the FIXES, but not the OUTPUT QUALITY.**

**Probe finding:**
Plan 12-06-PLAN.md line 252 prints rates only. The table in line 320 lists rate metrics — no absolute count target. INT-02 ("≥20 merges") is the only count metric, and it's a floor not a ceiling.

**Recommended decision:**
Add a "Task count delta" row to the BEFORE_AFTER table: "Baseline 509, New N, Δ=±%". Not pass/fail — informational. Helps the reviewer notice if a fix landed correctly but downstream behavior is unexpected (e.g., N=200 would mean dedup over-merged or granularity over-collapsed).

**Severity:** WARNING. Informational signal, low cost to add.

---

### GA-4 (WARNING): Wave 1 parallelism — file-level isolation OK, behavioral isolation untested

**The unstated assumption:** Plans 12-01, 12-02, 12-04 run in parallel (`Wave 1`) safely because they touch different files.

**Why it matters:** They don't share file edits. But they DO share runtime contracts:
- 12-01 modifies `pipeline/llm_client.py::complete_json` signature (adds `recovery_flag` kwarg).
- 12-04 calls `complete_json` in `_run_batches` (12-04-PLAN.md line 249) — DOES NOT pass `recovery_flag`.

Is that OK? Yes, because `recovery_flag` is optional with `None` default (12-01-PLAN.md line 284). But the contract is now: *if 12-04 lands BEFORE 12-01, 12-04's tests pass. If 12-01 lands BEFORE 12-04, 12-04's tests still pass.* Good — order-independence holds.

But: 12-04's `_run_batches` catches `(ValueError, RuntimeError)`. After 12-01 lands, `complete_json` may NOT raise ValueError anymore for malformed-but-repairable JSON — it'll return a repaired value silently. So 12-04's halving-on-error mechanism will fire LESS often than tests assume. The tests in 12-04-PLAN.md mock `complete_json.side_effect = [ValueError("boom"), ...]` directly, bypassing the json-repair fallback. **Tests pass; production behavior differs.**

**Probe finding:**
Tests mock at the `LLMClient.complete_json` boundary, so they don't see the json-repair path. After 12-01, real LLM calls returning malformed JSON now return repaired JSON — `_run_batches` will see SUCCESS and won't halve. Halving only fires when json-repair ALSO fails (rare). **Net effect: D-34's halve-floor logic is mostly dead code in production, but the tests don't catch it.**

**Recommended decision:**
Add an integration test in `tests/test_adaptive_dedup_batching.py` that mocks at the `litellm.completion` layer (not `complete_json`) and returns malformed-but-json-repairable payload. Assert that `_run_batches` succeeds on the FIRST attempt (no halving). This pins the interaction contract between 12-01 and 12-04.

**Severity:** WARNING. The current plans both pass their isolated tests but a real-world malformation profile may differ from test expectations.

---

### GA-5 (WARNING): Embedding cache reuse on T6 rerun

**The unstated assumption:** The T6 rerun on `test_sow.pdf` produces a fresh task corpus and a fresh embedding set.

**Why it matters:** `pipeline/agents/deduplication.py:114` writes `data/sessions/<run_id>/embeddings.npz`. The path is run-id-scoped, so a NEW run gets a NEW directory. Confirmed: `find data/sessions -name "embeddings.npz"` shows one file per run.

But: the project-level cross-run index (`data/project_indices/<project_key>/`) ACCUMULATES embeddings across runs. Plan 12-06 says "Same RunConfig for the rerun" including `jira_project_key` — but the baseline ran with `project_key=None` (default), so no project index was built. **Conclusion: no cache invalidation issue in practice.**

**Probe finding:**
- `pipeline/orchestrator.py:65`: `project_key=(config.jira_project_key or None)`. CONTEXT.md doesn't specify `jira_project_key` in the RunConfig — it's whatever the UI sends.
- Looking at the baseline session for `jira_project_key`:

**Recommended decision:**
Add an explicit instruction to 12-06 Task 6.1 (Human-verify step): "Confirm `jira_project_key` is NOT set (or matches a fresh project key) so cross-run flag pollution does not contaminate metrics." 1-line addition.

**Severity:** WARNING. Could silently bias the new run's POTENTIAL_DUPLICATE flag rate.

---

### GA-6 (INFORMATIONAL): Test isolation across the new files

**The unstated assumption:** Adding 5 new test files (51+ tests) coexists with the existing 94 tests without shared-state issues.

**Why it matters:** Concerns:
- `sentence-transformers` (MiniLM) load — currently `DeduplicationAgent._get_embedder()` is lazy and per-instance. New `tests/test_tiered_coverage_filter.py` uses a `_FakeEmbedder` (12-03-PLAN.md line 293), avoiding the load.
- `litellm` module-level cache: complete_json mocks at the MagicMock level. No real litellm calls.
- Pydantic schema registration: `MissedItem`'s new `tier` field is purely additive; old tests creating MissedItem without `tier` get the default.

**Probe finding:**
- `tests/test_critic.py` and the new `tests/test_critic_confidence_gating.py` both import `CritiqueIssue` enum. After 12-02 removes `LIKELY_DUPLICATE` from the enum, any prior test that referenced `CritiqueIssue.LIKELY_DUPLICATE` would fail at import time. Grep: `grep -rn "LIKELY_DUPLICATE" tests/` returns nothing. **Safe.**
- `tests/test_dedup.py` and the new `tests/test_adaptive_dedup_batching.py` both create `DeduplicationAgent` instances. 12-04 adds `pair_batch_size: int = 30` AFTER `similarity_threshold` in the kwargs list (12-04-PLAN.md line 128). Test_dedup.py uses kwargs (`tests/test_dedup.py:78`). **Safe.**

**Severity:** INFORMATIONAL. No action needed.

---

### GA-7 (INFORMATIONAL): Wall-clock target absent from Phase 12 success criteria

**The unstated assumption:** Phase 12 doesn't care about wall-clock as long as metrics improve.

**Why it matters:** Baseline took 4.25 hours (confirmed: `2026-05-12T14:19:36` → `2026-05-12T18:34:25`). Phase 12 adds:
- json-repair calls (12-01) — minor overhead.
- Critic prompt rewrites (12-02) — same call count, slightly larger prompt.
- Coverage filter step (12-03) — adds 1 batched embedding call post-dedup (cheap, ~100ms).
- Adaptive dedup batching (12-04) — multiplies dedup LLM calls. Baseline made 1 call (failed). New design makes ~candidate_pairs / 30 calls + up to 2 reconcile rounds. Baseline had ~37 pairs → 2 calls + reconcile = 3-4 calls. Each call is faster (smaller payload).
- Granularity prompt (12-05) — same call count.

Net effect: probably FASTER overall (no 8x retry loop on failing dedup), but no guaranteed bound.

**Recommended decision:**
Add a "Run duration" informational row to BEFORE_AFTER.md (similar to GA-3 task-count delta). Not pass/fail. Useful for tracking.

**Severity:** INFORMATIONAL.

---

## 3. Plan-revision recommendations

| Plan | Recommended change | Severity |
|------|-------------------|----------|
| **12-01** | Add Task 1.3: extend `CoverageTracker` with `mark_partial(node_id)` and modify `get_gaps()` to include partial nodes. Wire `mark_partial` from the orchestrator when audit emits `EXTRACTION_PARTIAL`. Without this, D-33 has no consumer. | **BLOCKER** |
| **12-06** | In Task 6.2 metrics script, add a histogram of CRITIQUE_FLAGGED `conf` values (bins: 0.5-0.6, 0.6-0.7, 0.7-0.8, 0.8-0.9, 0.9-1.0). Flag if >70% cluster in any single 0.1 bin near the threshold. | WARNING |
| **12-06** | Add a "Task count delta" informational row to BEFORE_AFTER.md table comparing baseline 509 vs new N. Not pass/fail. | WARNING |
| **12-04** | Add 1 test in `tests/test_adaptive_dedup_batching.py` that mocks `litellm.completion` (not `complete_json`) with malformed-but-repairable payload, asserts no halving fires after json-repair landed. | WARNING |
| **12-06** | In Task 6.1 (human-verify), add: "Confirm `jira_project_key` is None (or matches baseline) to avoid cross-run flag pollution." | WARNING |
| **12-06** | Add a "Run duration" informational row to BEFORE_AFTER.md. | INFORMATIONAL |
| **12-04** | Add `DEDUP_TAIL_REMAINING` audit row when 2-round cap is hit with `new_pairs` still emerging. 3-line add. | INFORMATIONAL |

**No revision needed for:** 12-02 (D-30, D-31), 12-03 (D-32), 12-05 (D-35) — these decisions stand as locked.

---

## 4. Honest verdict

This retroactive pass surfaced **one BLOCKER** the inline `/gsd:discuss-phase` pass missed: D-33 promises that EXTRACTION_PARTIAL routes a node to gap_recovery, but `pipeline/coverage.py:get_gaps()` cannot see partial-extraction nodes because `mark_covered` fires on the first emitted task — making the audit row a dead letter. The other 6 gray areas are calibration/instrumentation reservations (WARNING/INFORMATIONAL), not refutations of the locked decisions.

Five locked decisions (D-30, D-31, D-32, D-34, D-35) hold under adversarial probe; D-33 is at-risk on its stated *consequence*, not its mechanism — the audit row IS being correctly emitted, but no consumer reacts to it. The 30-minute fix is to extend `CoverageTracker` with a `mark_partial()` method and have `get_gaps()` include partial nodes. This belongs in 12-01 as Task 1.3.

The standout finding is methodological: the inline discussion validated the *signals* (audit rows, flag rates, embedding similarity) but did not verify that each signal had a downstream *consumer*. The same pattern that retired the three magic-number heuristics ("using QC signal as QC trigger is circular") applies here — except the failure mode is one step removed: emitting a signal that nothing acts on. Worth checking on future phases.

**Top-priority recommendation:** Wire the EXTRACTION_PARTIAL → gap_recovery consumer in 12-01 before /gsd:execute-phase. Estimated cost: 15 minutes of code + 1 test. Without it, D-33 ships an audit row to nowhere.
