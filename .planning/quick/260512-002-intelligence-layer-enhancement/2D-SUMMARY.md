---
wave: 2D
quick_id: 260512-002
improvement: 5
title: Persisted hierarchical dedup with cross-run awareness
date: 2026-05-12
status: complete
---

# Wave 2D — Persisted hierarchical dedup with cross-run awareness Summary

The intra-run deduplication agent stops doing an O(n^2) Python loop over every
task pair, computes embeddings just once per run, persists them to disk, and
optionally consults a project-scoped index so a second SOW pushed to the same
Jira project a month later flags overlap with the first run's tasks (with the
prior Jira issue key when available). The public `deduplicate(tasks)` signature
is unchanged, the cross-run path is opt-in via a new `project_key` constructor
arg, and there are no schema changes — cross-run match details are encoded in
the audit log to keep the `ManagedTask` contract stable.

## Files changed

- `requirements.txt` — added `scikit-learn>=1.3` (was already transitively installed via `sentence-transformers`, but Wave 2D imports `sklearn.neighbors.NearestNeighbors` directly so it deserves an explicit pin).
- `pipeline/agents/deduplication.py` — replaced the brute-force double loop with `_find_candidate_pairs()` using `sklearn.NearestNeighbors(metric="cosine", algorithm="brute")` and `radius_neighbors` with `radius = (1 - threshold) + 1e-9` (cushion so a similarity exactly at threshold still matches the legacy `>=` semantic). Added `_persist_embeddings()` that writes `data/sessions/<run_id>/embeddings.npz` via `np.savez_compressed` on every dedup pass — including the short-circuit branches (n<2, no candidates, LLM error) so evals/replay tooling always finds the artifact. Added `_apply_cross_run_matches()` that, when `project_key` is set, instantiates `ProjectEmbeddingIndex`, calls `search(exclude_run_id=self.run_id)`, flags each match with `TaskFlag.POTENTIAL_DUPLICATE`, audit-logs `action="CROSS_RUN_MATCH"` with `sim`/`prior_run`/`prior_task`/`prior_jira`/`prior_title` encoded into `detail`, then appends this run's embeddings via `index.add_run()`. New constructor args: `project_key: Optional[str] = None`, `cross_run_threshold: Optional[float] = None` (defaults to `similarity_threshold`), `sessions_dir`/`project_indices_dir` (overridable for tests). When `project_key is None` the agent behaves exactly like the legacy intra-run-only dedup — no index files are created, no cross-run audit rows are written. `_merge_tasks` is unchanged.
- `pipeline/agents/cross_run_index.py` — new module. `CrossRunMatch` Pydantic model (`task_id`, `prior_task_id`, `prior_run_id`, `prior_jira_key`, `similarity`, `prior_title`). `ProjectEmbeddingIndex(project_key, base_dir="data/project_indices")` exposes `add_run(run_id, tasks, embeddings)`, `search(embeddings, task_ids, threshold, exclude_run_id)`, and `update_jira_keys(mapping)`. On-disk layout per project key: `<base_dir>/<project_key>/index.npz` (arrays: `embeddings` float32 `(N, 384)`, `task_ids`, `run_ids`, `jira_keys`, `titles`) and `<base_dir>/<project_key>/manifest.json` (`version`, `project_key`, `last_updated`, `task_count`). Writes go through a `.tmp` path and `os.replace` for atomicity. Search uses `NearestNeighbors(metric="cosine").kneighbors(n=1)` then filters by threshold so each query yields at most one prior match.
- `tests/test_dedup.py` — new file, 5 unit tests.
- `tests/test_cross_run_index.py` — new file, 7 unit tests.

## Files explicitly NOT modified

- `pipeline/orchestrator.py` — intentionally untouched. Wave 3 will pass `RunConfig.jira_project_key` into the agent.
- `pipeline/agents/extraction.py`, `pipeline/agents/state.py`, `pipeline/agents/gap_recovery.py` — out of scope.
- `integrations/jira_client.py` — no change. `update_jira_keys()` is a hook Wave 3 will wire from the push flow.
- `models/schemas.py` — no change. Cross-run match details are audit-log-only by deliberate design (keeps the `ManagedTask` contract stable; the `POTENTIAL_DUPLICATE` flag already exists in `TaskFlag` from Wave 1).
- All UI files, `pageindex/` — untouched.

## Commit hashes

| Commit | Subject |
|---|---|
| `960be23` | deps(quick-260512-002-2D): pin scikit-learn for NearestNeighbors dedup |
| `4e61184` | feat(quick-260512-002-2D): add ProjectEmbeddingIndex for cross-run dedup |
| `de6ef66` | feat(quick-260512-002-2D): replace O(n^2) dedup loop with sklearn NearestNeighbors and persist embeddings |
| `c147f24` | test(quick-260512-002-2D): unit cover dedup rewrite and cross-run index |

## Test output

### New tests (`tests/test_dedup.py` + `tests/test_cross_run_index.py`)

```
collected 12 items

tests/test_dedup.py::test_dedup_same_results_as_legacy_brute_force PASSED [  8%]
tests/test_dedup.py::test_dedup_below_threshold_no_pairs PASSED          [ 16%]
tests/test_dedup.py::test_dedup_persists_embeddings_npz PASSED           [ 25%]
tests/test_dedup.py::test_dedup_with_llm_error_returns_unmodified PASSED [ 33%]
tests/test_dedup.py::test_dedup_no_project_key_skips_cross_run_index PASSED [ 41%]
tests/test_cross_run_index.py::test_index_creates_files_on_first_add PASSED [ 50%]
tests/test_cross_run_index.py::test_search_finds_prior_run_match PASSED  [ 58%]
tests/test_cross_run_index.py::test_search_excludes_current_run PASSED   [ 66%]
tests/test_cross_run_index.py::test_update_jira_keys PASSED              [ 75%]
tests/test_cross_run_index.py::test_add_run_appends_across_runs PASSED   [ 83%]
tests/test_cross_run_index.py::test_search_against_empty_index_returns_empty PASSED [ 91%]
tests/test_cross_run_index.py::test_project_key_required PASSED          [100%]

======================== 12 passed, 1 warning in 36.92s ========================
```

(The 36s wall time is dominated by the first `SentenceTransformer("all-MiniLM-L6-v2")` load in `test_dedup_same_results_as_legacy_brute_force`; subsequent tests reuse it.)

### Prior-wave regression (`tests/test_hierarchy_preservation.py` + `tests/test_structured_acceptance.py`)

```
collected 19 items

tests/test_hierarchy_preservation.py::test_flatten_tree_emits_parent_id PASSED [  5%]
tests/test_hierarchy_preservation.py::test_flatten_tree_handles_empty_and_legacy_inputs PASSED [ 10%]
tests/test_hierarchy_preservation.py::test_source_ref_carries_parent_chain PASSED [ 15%]
tests/test_hierarchy_preservation.py::test_source_ref_defaults_when_node_missing_hierarchy PASSED [ 21%]
tests/test_hierarchy_preservation.py::test_jira_grouping_uses_parent_id PASSED [ 26%]
tests/test_hierarchy_preservation.py::test_jira_grouping_legacy_tasks_fall_back_to_section_title PASSED [ 31%]
tests/test_structured_acceptance.py::test_normalize_string_acceptance_criteria PASSED [ 36%]
tests/test_structured_acceptance.py::test_normalize_dict_acceptance_criteria PASSED [ 42%]
tests/test_structured_acceptance.py::test_normalize_already_structured PASSED [ 47%]
tests/test_structured_acceptance.py::test_normalize_mixed_and_empty PASSED [ 52%]
tests/test_structured_acceptance.py::test_managed_task_legacy_string_acs_normalize_via_validator PASSED [ 57%]
tests/test_structured_acceptance.py::test_managed_task_carries_dependencies PASSED [ 63%]
tests/test_structured_acceptance.py::test_state_agent_merge_dedupes_acs_and_deps PASSED [ 68%]
tests/test_structured_acceptance.py::test_jira_description_renders_structured_ac PASSED [ 73%]
tests/test_structured_acceptance.py::test_jira_description_handles_legacy_string_ac PASSED [ 78%]
tests/test_structured_acceptance.py::test_dependency_link_resolution_creates_links_for_resolvable_refs PASSED [ 84%]
tests/test_structured_acceptance.py::test_dependency_link_resolution_skips_when_source_push_failed PASSED [ 89%]
tests/test_structured_acceptance.py::test_dependency_link_resolution_records_warning_on_link_failure PASSED [ 94%]
tests/test_structured_acceptance.py::test_dependency_link_resolution_maps_kind_to_link_type PASSED [100%]

======================== 19 passed, 7 warnings in 1.81s ========================
```

### Full suite (same exclusions as 1A/1B documented)

```
venv/bin/pytest tests/ --ignore=tests/test_hierarchical_judge.py --ignore=tests/test_phase11_evals.py

44 passed, 7 warnings in 32.07s
```

Breakdown: 6 from `test_hierarchy_preservation.py` (Wave 1A), 13 from `test_structured_acceptance.py` (Wave 1B), 5 from `test_dedup.py` (Wave 2D), 7 from `test_cross_run_index.py` (Wave 2D), 8 from `test_phase2_runtime_reliability.py`, 5 from `test_routing.py`. Same two pre-existing-broken collectors (`tests/test_hierarchical_judge.py`, `tests/test_phase11_evals.py`) remain out of scope per Waves 1A/1B summaries.

## Orchestrator integration sketch

Wave 3 will be a ~10-line change in `pipeline/orchestrator.py`:

1. **Source of `project_key`.** It's already on the `RunConfig` (`models/schemas.py:203`): `RunConfig.jira_project_key: str`. Read it where the orchestrator currently instantiates `DeduplicationAgent` and pass it through:
   ```python
   dedup = DeduplicationAgent(
       llm_client=llm,
       audit_logger=audit,
       run_id=self.run_id,
       similarity_threshold=DEDUP_SIMILARITY_THRESHOLD,
       project_key=self.run_config.jira_project_key or None,
   )
   ```
   If you want cross-run flagging to be more conservative than intra-run merge (recommended — false positives across SOWs are more disruptive than within one), also pass `cross_run_threshold=0.90` (default is the same as the intra-run threshold).

2. **Optional: feedback the Jira keys after push.** After `JiraClient.push_tasks` returns, the orchestrator can call `ProjectEmbeddingIndex(project_key).update_jira_keys({str(task.id): result.jira_issue_key for ...})` so future runs see the prior issue keys in `CROSS_RUN_MATCH` audit details. This is purely additive — skip it for a first integration pass and it still works (matches just won't carry a Jira key).

3. **No new env flags needed.** The cross-run feature toggles on iff `RunConfig.jira_project_key` is set (it already is for every real run; the field has no default).

4. **No UI change.** Cross-run matches surface today as the `POTENTIAL_DUPLICATE` flag, which the UI already renders (Wave 1 / earlier).

## On-disk artifacts and .gitignore

Two new on-disk paths:

- `data/sessions/<run_id>/embeddings.npz` — already covered by the existing `data/` rule in `.gitignore` (line 10).
- `data/project_indices/<project_key>/{index.npz, manifest.json}` — also under `data/`, so already gitignored. **No `.gitignore` change needed.**

Verified: `cat .gitignore | grep '^data/'` returns `data/`.

If a future operator wants to nuke the cross-run state (e.g. a project-key rename), `rm -rf data/project_indices/<project_key>/` is safe — the agent recreates the directory on the next run.

## Backward compatibility notes

- **Default constructor behavior.** `DeduplicationAgent(llm_client, audit_logger, run_id)` (no `project_key`) preserves byte-identical intra-run dedup. The only externally visible difference vs. pre-2D is that `data/sessions/<run_id>/embeddings.npz` now exists; nothing reads from it yet, so nothing breaks.
- **No new `ManagedTask` fields.** Resumed `pipeline_output.json` checkpoints from prior runs deserialize untouched. Cross-run match information lives only in the audit log (`action="CROSS_RUN_MATCH"`).
- **Index versioning.** `manifest.json.version = 1`. If we later change the embedding model (e.g. to `bge-small`), the version bump in `cross_run_index.py:INDEX_VERSION` and a dimension check in `add_run` will cause the agent to refuse to merge incompatible runs rather than silently corrupting search results. The dimension guard is already in place.
- **Thresholding.** Default `similarity_threshold=0.85`. The dedup agent still emits `POTENTIAL_DUPLICATE` only when the LLM confirms an intra-run pair (via the existing `_merge_tasks` path) OR when the cross-run search finds a prior match. Both code paths converge on the same flag, so existing UI treatment of `POTENTIAL_DUPLICATE` covers cross-run hits automatically.

## Performance notes

- The brute-force loop was O(n^2) per run. The new path is O(n^2) too (NearestNeighbors with `algorithm="brute"` and cosine), but the inner work is BLAS-accelerated `np.dot` over the full matrix instead of a Python interpreter loop. For the ~50 task case we're seeing on real SOWs the difference is sub-second either way; the real win is that we now have a clean place to swap in `algorithm="ball_tree"` or FAISS later without touching `deduplicate()`.
- `_persist_embeddings()` runs on every dedup pass. For a 384-dim float32 matrix of N rows, the compressed npz is ~1.5 KB per row — negligible.
- Cross-run search reloads the project npz on every pipeline run. At project-level scale (low thousands of tasks), the load is single-digit milliseconds. No caching is needed.

## Scope confirmation

- Wave 2D does NOT touch `pipeline/orchestrator.py`, `pipeline/agents/extraction.py`, `pipeline/agents/state.py`, `pipeline/agents/gap_recovery.py`, `integrations/jira_client.py`, `models/schemas.py`, or any UI/pageindex file.
- The new module is `pipeline/agents/cross_run_index.py`; the new tests are `tests/test_dedup.py` and `tests/test_cross_run_index.py`.
- One requirements line added (`scikit-learn>=1.3`).
- All four required deliverables shipped (dedup rewrite + persistence; cross-run index module; dedup-side wiring with `project_key`; tests).

## Self-Check: PASSED

- `pipeline/agents/cross_run_index.py` exists
- `pipeline/agents/deduplication.py` updated (imports `from sklearn.neighbors import NearestNeighbors`, `from pipeline.agents.cross_run_index import ProjectEmbeddingIndex`)
- `tests/test_dedup.py` exists, 5 tests, all green
- `tests/test_cross_run_index.py` exists, 7 tests, all green
- `requirements.txt` includes `scikit-learn>=1.3`
- All four commits (`960be23`, `4e61184`, `de6ef66`, `c147f24`) present in `git log`
- 19/19 prior-wave tests still green (no regression)
- `data/` already in `.gitignore`; no gitignore change needed
