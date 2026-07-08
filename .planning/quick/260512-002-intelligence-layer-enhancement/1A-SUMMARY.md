---
wave: 1A
quick_id: 260512-002
improvement: 1
title: Hierarchy preservation
date: 2026-05-12
status: complete
---

# Wave 1A — Hierarchy preservation Summary

Real Epic/Story/Sub-task hierarchies now flow through the pipeline structurally. The system stops grouping by `section_title` strings and instead carries the PageIndex tree's `parent_id` / `parent_chain` / `depth` from indexer → `SourceRef` → JiraClient, so two sections that share a heading under different parents land in different containers.

## Files changed

- `pipeline/indexer.py` — replaced the flat `structure_to_list` walk in `flatten_tree` with a parent-aware DFS that emits `parent_id`, `parent_chain`, `depth`, `node_index`. Also built `node_index_map: dict[node_id, node]` and initialized it on `DocumentIndexer.__init__`.
- `models/schemas.py` — `SourceRef` gained `parent_id: Optional[str]`, `parent_chain: list[str]`, `depth: int` (all backward-compatible defaults). Added `JiraHierarchy` docstring noting that grouping is now structural and STORY_SUBTASK can honor a real 3-level structure when the tree has ≥ 2 levels of depth.
- `pipeline/agents/state.py` — `process()` now populates the new `SourceRef` fields from the incoming node dict with `.get()` fallbacks so legacy nodes don't crash.
- `pipeline/orchestrator.py` — after the PageIndex step, persists `data/sessions/<run_id>/node_index.json` mapping `node_id → {title, parent_id, parent_chain, depth, node_index, page_start, page_end}`. Failure is logged but non-fatal.
- `integrations/jira_client.py` — replaced the `section_title`-keyed `epic_cache` / `story_cache` with a structural cache keyed by the root ancestor `node_id` (via new `_container_key_for(task)` helper). Loads the orchestrator-persisted `node_index.json` automatically; also accepts `node_index` as a constructor kwarg for unit tests. Container titles are now resolved from the parent node's actual title (root ancestor preferred, immediate parent next, finally falls back to `section_title`). The existing parent-less retry fallback is preserved.
- `tests/test_hierarchy_preservation.py` — new file, 6 unit tests, all green.

## Commit hashes

| Commit | Subject |
|---|---|
| `65fa075` | feat(quick-260512-002-1A): propagate parent_id/depth in flatten_tree |
| `21b0b5d` | feat(quick-260512-002-1A): add parent_id/parent_chain/depth to SourceRef |
| `9c1247a` | feat(quick-260512-002-1A): persist node_index.json with hierarchy metadata |
| `68399fc` | feat(quick-260512-002-1A): group Jira containers by parent_id, not section_title |
| `f6314c1` | test(quick-260512-002-1A): add hierarchy preservation tests |

## Test output

### New tests (`tests/test_hierarchy_preservation.py`)

```
============================= test session starts ==============================
collected 6 items

tests/test_hierarchy_preservation.py::test_flatten_tree_emits_parent_id PASSED [ 16%]
tests/test_hierarchy_preservation.py::test_flatten_tree_handles_empty_and_legacy_inputs PASSED [ 33%]
tests/test_hierarchy_preservation.py::test_source_ref_carries_parent_chain PASSED [ 50%]
tests/test_hierarchy_preservation.py::test_source_ref_defaults_when_node_missing_hierarchy PASSED [ 66%]
tests/test_hierarchy_preservation.py::test_jira_grouping_uses_parent_id PASSED [ 83%]
tests/test_hierarchy_preservation.py::test_jira_grouping_legacy_tasks_fall_back_to_section_title PASSED [100%]

======================== 6 passed, 7 warnings in 1.72s =========================
```

### Full suite (excluding pre-existing broken collections)

```
collected 19 items

tests/test_hierarchy_preservation.py ......                              [ 31%]
tests/test_phase2_runtime_reliability.py ........                        [ 73%]
tests/test_routing.py .....                                              [100%]

======================== 19 passed, 7 warnings in 1.51s =========================
```

## Pre-existing test failures (not caused by Wave 1A)

Two test modules fail at *collection time* on `main` independently of this work:

- `tests/test_hierarchical_judge.py` — `ModuleNotFoundError: No module named 'langchain_openai'` (root cause is `pipeline/evals/judges.py` importing an uninstalled dep; the shallow error reported by pytest is `No module named 'pipeline'` because the test file lacks the `sys.path.insert` boilerplate other tests have).
- `tests/test_phase11_evals.py` — same `sys.path` issue; `from models.eval_schemas import ...` fails because tests aren't run from the project root pythonpath.

Both errors reproduce on the parent commit (`44ca803`) before any Wave 1A change. They are dependency/conftest issues, out of scope for this task.

## Migration note for in-flight runs

The `pipeline_output.json` checkpoints written before this PR have `SourceRef` objects without the new `parent_id` / `parent_chain` / `depth` fields. Pydantic accepts them because the new fields all have defaults (`None`, `[]`, `0`). Downstream:

- `JiraClient._container_key_for` checks `ref.parent_chain` first, then `ref.parent_id`, finally falls back to the source `section_title`, so legacy tasks still get grouped (under their section name) instead of crashing. This is exercised by `test_jira_grouping_legacy_tasks_fall_back_to_section_title`.
- `node_index.json` is per-session, written at run time. If a run was started before this PR landed and is resumed/pushed after, the file won't exist. `JiraClient._load_node_index` returns `{}` in that case, which routes through the legacy fallback. No errors are raised.

For a fully structural rollup on a resumed legacy run, re-run the PageIndex step (drop `data/sessions/<run_id>/document_tree.json` and `pipeline_output.json`).

## Scope confirmation

Files NOT touched (reserved for other waves):
- `pipeline/agents/extraction.py` — Wave 1B / 2B
- `pipeline/agents/deduplication.py` — Wave 2D
- `pipeline/agents/gap_recovery.py`
- Any UI file

No changes to `JiraHierarchy` enum values; only added a docstring.
