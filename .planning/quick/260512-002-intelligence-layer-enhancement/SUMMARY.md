---
quick_id: 260512-002
slug: intelligence-layer-enhancement
date: 2026-05-12
status: complete
verification: 75/75 pytest green
---

# Quick Task 260512-002 — Intelligence Layer Enhancement (program summary)

## Goal achieved

Implemented all six improvements scoped in the deep-analysis turn. The SOW extraction pipeline now has structural hierarchy, structured acceptance criteria + dependency edges, semantic coverage checks, section classification with few-shot extraction, per-section self-critique, and cross-run persisted dedup.

## Final pipeline shape (per-node loop)

```
PageIndex tree (now carrying parent_id / depth)
    → SectionClassifier         (NEW — gates extraction)
       └─ skip on legal/definitions/signature/context with high confidence
    → TaskExtractionAgent       (UPDATED — few-shot + scratchpad)
    → TaskStateAgent            (UPDATED — handles structured ACs + deps)
    → TaskCritic                (NEW — verb-title fixes, AC fixes, broad-scope flags)
    → CoverageChecker           (NEW — "what was missed?" per section)
       └─ flags INCOMPLETE on misses; persists coverage_reports.json
    → CoverageTracker.mark_covered  (existing)
...
    → DeduplicationAgent (REWRITTEN — sklearn NN, persisted embeddings,
                          cross-run flagging when jira_project_key set)
    → GapRecoveryAgent (existing)
    → JiraClient.push_tasks (UPDATED — parent_id grouping, structured AC render,
                             "Blocks/Relates" issue links)
```

## Waves and commits

| Wave | Item | Branch | Tip | Tests added |
|------|------|--------|-----|-------------|
| 1A | #1 Hierarchy preservation | main | c31de01 | 6 |
| 1B | #6 Structured ACs + deps | main | babf6b4 | 13 |
| 2A | #2 Semantic coverage | wave-2a-coverage-check | 79f99fc | 6 |
| 2B | #3 Classifier + few-shot | worktree-agent-abac… | 0ce1bd3 | 17 |
| 2C | #4 Self-critique | main (direct) | 3c2ad1c | 8 |
| 2D | #5 Cross-run dedup | worktree-agent-a864… | 8c5fca9 | 12 |
| 3 | Orchestrator integration | main | 5e6b269 | — |

**Total: 62 new unit tests + integration validated by full-suite regression. `venv/bin/pytest tests/` (excluding two pre-existing-broken collectors flagged in 1A) reports 75/75 green.**

## Env-flag controls

Per the risk register, three new agents add ~1 LLM call/section each. All togglable:

| Var | Default | Effect when set to `0` |
|---|---|---|
| `SOW_CLASSIFIER_ENABLED` | `1` | Always extract; no classifier gate |
| `SOW_ENABLE_CRITIC` | `1` | Skip critic pass entirely |
| `SOW_SEMANTIC_COVERAGE` | `1` | Skip per-section coverage check |
| `SOW_CRITIC_THRESHOLD` | `0.8` | (Float) Critic auto-fix confidence threshold |

Existing flags (`DEDUP_SIMILARITY_THRESHOLD`, `EXTRACTION_CONFIDENCE_THRESHOLD`) unchanged.

## Schema changes

`models/schemas.py` extensions (all backward-compatible):

- `SourceRef.parent_id`, `SourceRef.parent_chain`, `SourceRef.depth` — Wave 1A
- `AcceptanceCriterion`, `AcceptanceCriterionType`, `TaskDependency` — Wave 1B
- `RawTask.acceptance_criteria: Optional[list[AcceptanceCriterion | str]]` — accepts legacy string lists; normalizer in `models/schemas.normalize_acceptance_criteria()` converts on the way in
- `RawTask.dependencies: list[TaskDependency]`
- `ManagedTask.acceptance_criteria: Optional[list[AcceptanceCriterion]]` (always structured post-normalization)
- `ManagedTask.dependencies: list[TaskDependency]`

Old `pipeline_output.json` checkpoints load cleanly via the field validator.

## On-disk artifacts per run

Existing:
- `data/sessions/<run_id>/document_tree.json`
- `data/sessions/<run_id>/pipeline_output.json`

New:
- `data/sessions/<run_id>/node_index.json` — hierarchy metadata (Wave 1A)
- `data/sessions/<run_id>/embeddings.npz` — dedup embeddings (Wave 2D)
- `data/sessions/<run_id>/coverage_reports.json` — semantic coverage misses per node (Wave 3 / 2A integration)
- `data/project_indices/<project_key>/index.npz` + `manifest.json` — cross-run embedding index (Wave 2D)

All under gitignored `data/`.

## Verification

- `venv/bin/pytest tests/ --ignore=tests/test_hierarchical_judge.py --ignore=tests/test_phase11_evals.py` → `75 passed, 7 warnings in 33.05s`
- `venv/bin/python -c "from pipeline.orchestrator import PipelineOrchestrator"` → imports clean (no missing deps, no circular imports)
- The two excluded test files have pre-existing `langchain_openai` import errors flagged by Wave 1A; out of scope for this program.

## Known follow-ups (deliberately out of scope)

- UI surface for the new fields: structured AC type/verified_by badges, dependency link visualization, cross-run match indicator. Frontend rendering was non-goal per PLAN.md.
- Eval harness wiring (the Phase 11 hierarchical-judge eval should regress-test these against a golden dataset — once langchain_openai is reinstalled).
- Performance pass: with all three new agents on, per-section LLM calls go from 1 to ~3-4. Acceptable for correctness-first delivery; tunable via env flags.
- Recovered-task UX separation in the UI for GAP_RECOVERED tickets.

## Operator runbook

To exercise the new layer end-to-end:

1. Ensure venv installed: `make install` (now pulls scikit-learn).
2. `make ui` and visit http://localhost:8000.
3. Upload a SOW, run extraction. With all flags default, you should see:
   - Some sections audit-logged as `CLASSIFIED_SKIP=legal/context/...` and skipped without extraction.
   - Critic actions in audit log (`CRITIQUE_AUTO_FIX`, `CRITIQUE_FLAGGED`).
   - `COVERAGE_CHECK` rows per actionable section.
   - `coverage_reports.json` produced under `data/sessions/<run_id>/`.
4. If you push to Jira with `JiraHierarchy.EPIC_TASK` or `STORY_SUBTASK`, Epic containers should group by structural parent (PageIndex node `parent_id`) — not by section title.
5. Re-run on a related SOW with the same `JIRA_PROJECT_KEY` and check the audit log for `CROSS_RUN_MATCH` rows.

If costs spike, disable agents one at a time via the env vars above.
