# Phase 12: Intelligence Layer Tightening — Context

**Gathered:** 2026-05-13
**Status:** Ready for planning (`/gsd:plan-phase 12`)
**Baseline run for verification:** `data/sessions/20260512-194433-test-sow/`

<domain>
## Task Boundary

Tighten the Wave 1-3 intelligence layer (shipped in quick-task `260512-002`) without adding new capabilities. After the first end-to-end validation run, three latent integrity gaps emerged that the happy-path implementation hid:

1. **Coverage check is a flag bomb** — 618 missed_items reported across 76 nodes, almost all duplicates of already-extracted tasks or cross-section overlap. The auto-`INCOMPLETE` flag on every task in affected sections flipped 100% of the 509 tickets to `INCOMPLETE`. The flag became signal-free.

2. **Dedup did zero merges** — the dedup agent's long pair-decision JSON output (37+ pairs from 509 tasks) hit a mid-stream delimiter failure on `openrouter/google/gemini-2.5-flash`. After both retry attempts failed identically, dedup gave up silently. Massive cross-section task duplication shipped unaltered.

3. **Critic flags at conf=0.00 are unconditional** — 100% of the 227 `CRITIQUE_FLAGGED` entries had `conf=0.00`, exclusively from two issue types (`likely_duplicate` ×175, `too_broad` ×51). Root cause: the prompt's "DO NOT provide a fix, only report the issue" framing made the LLM treat confidence as irrelevant for non-fix flags; the `_apply` logic adds the flag unconditionally for these issues (no confidence check, unlike auto-fix).

Bonus: **29 of 30 extraction failures** share the same JSON-delimiter root cause as #2 — a single `json-repair` fallback fixes both.

</domain>

<decisions>
## Implementation Decisions (locked, do not reopen)

### D-30 — Remove `LIKELY_DUPLICATE` from `CritiqueIssue` entirely
**Why:** Single-responsibility violation. Dedup owns duplicate detection. The 175 likely_duplicate flags on this run were just dedup's silent failure leaking. Once dedup is fixed (D-34 + json-repair), there is no value left in the critic doing dedup's job.
**How to apply:** Drop `LIKELY_DUPLICATE` from the enum and from the critic prompt's issue list.

### D-31 — Belt-and-suspenders on critic confidence
**Why:** The LLM omitting confidence and the `_apply` logic ignoring it are two independent failures, both real.
**How to apply:** (a) Rewrite the critic prompt's "DO NOT provide a fix" wording so confidence is always required (e.g. "Set confidence (0.0-1.0) to indicate how sure you are this is a real issue, regardless of whether you provide a fix"). (b) In `_apply`, ALL flag-adding paths must gate on `critique.confidence >= min_flag_confidence` (default 0.5). Auto-fix path stays at `auto_fix_threshold=0.8`.

### D-32 — Tiered embed-filter for coverage misses, run-wide post-dedup
**Why:** Per-section scope leaves cross-section duplicates in the report. Run-wide scope eliminates them. Tiering lets reviewers see "definitely covered" vs "maybe redundant" vs "uncovered" cleanly.
**How to apply:** Drop the per-node `TaskFlag.INCOMPLETE` auto-append in `orchestrator.py`. Add a post-dedup orchestrator step using the dedup agent's MiniLM embedder. For each missed_item: compute max cosine similarity vs all final task titles + first AC condition. Tier: `≥0.85` → drop; `0.70-0.85` → keep with `tier="likely_overlap"`; `<0.70` → keep with `tier="uncovered"`. Persist filtered+tiered back to `coverage_reports.json`.

### D-33 — Partial-recovery detector on json-repair fallback
**Why:** `json-repair` silently truncates malformed long arrays. A 22-task section becoming 14 tasks "successfully" is worse than failing — gap_recovery won't fire.
**How to apply:** When `json-repair` succeeds, compare recovered task count to `max(1, attempted // 2)`. If below, mark `EXTRACTION_PARTIAL` in audit + state so gap_recovery picks the node up.

### D-34 — Adaptive dedup batching with reconciliation pass
**Why:** Single-shot dedup on 37+ pairs blows out Gemini Flash's JSON reliability. Smaller batches are robust but risk transitive-merge inconsistency (batch 1 says A+B merge → B dropped → batch 2's decision on B is stale).
**How to apply:** Default `pair_batch_size=30`. On JSON parse failure, halve (min 5), retry. After all batches: re-cluster survivors by embedding similarity, re-dedup remaining candidate pairs above threshold. Cap reconciliation at 2 rounds. Audit-log `DEDUP_BATCH` and `DEDUP_RECONCILE`.

### D-35 — Granularity prompt tune ONLY (no hard cap)
**Why:** The audit retired three magic-number heuristics yesterday (skip coverage on dense, skip critic on high-conf, cap at 10 tasks) as same-flaw-class circular reasoning. Granularity is a quality problem, not a count problem.
**How to apply:** Update `EXTRACTION_PROMPT_TEMPLATE`'s "TASK GRANULARITY" section with: explicit "Each task is ONE atomic unit. Do NOT sub-split a sprint-sized task into separate tasks for sub-steps; capture sub-steps as deliverables or acceptance criteria within the parent" guidance + 2 negative examples (3-task split of design/implement/test for one screen; 5-task split of payment flow sub-steps). NO hard cap.

### Claude's discretion (not pre-committed)
- `min_flag_confidence` exact value (0.5 default, may tune during plan-checker)
- Exact prompt wording for granularity examples (planner can refine)
- Whether to surface `tier` in the Jira description rendering (probably yes — but a Phase 12 nice-to-have, not a must-have)

</decisions>

<specifics>
## Specific References

- **Baseline run:** `data/sessions/20260512-194433-test-sow/pipeline_output.json` — 509 tasks, all metrics above are computed from this file + `data/audit.db` for the same run_id.
- **Same RunConfig for the rerun (T6):** `provider=openrouter`, `model=openrouter/google/gemini-2.5-flash`, `jira_hierarchy=epic_task`, `max_nodes=200`, against `test_sow.pdf`.
- **json-repair library:** `json-repair>=0.59` on PyPI. MIT, no heavy deps, 100+ versions, widely used.
- **Critic apply logic (current bug):** `pipeline/agents/critic.py` `_apply` method, lines that handle `LIKELY_DUPLICATE` / `TOO_BROAD` / `VAGUE_TITLE` skip the confidence check (only auto-fix paths gate on it).
- **Dedup failure response shape (from audit):** starts with ``` ```json\n[\n  {\n    "task_id_a": ...``` — confirms the markdown-fenced long-array case that fails on Gemini Flash.

</specifics>

<canonical_refs>
## Canonical References

- `.planning/quick/260512-002-intelligence-layer-enhancement/SUMMARY.md` — the program this phase tightens
- `data/sessions/20260512-194433-test-sow/pipeline_output.json` — baseline output to beat
- `data/sessions/20260512-194433-test-sow/coverage_reports.json` — 618-miss artifact, the noise floor
- `data/audit.db` filtered by `run_id='20260512-194433-test-sow'` — flag application trace
- ROADMAP.md Phase 12 entry — Goal + Success Criteria + Requirements [INT-01..07]

</canonical_refs>
