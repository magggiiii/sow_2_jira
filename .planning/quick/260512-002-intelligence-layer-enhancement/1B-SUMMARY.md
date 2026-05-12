---
wave: 1B
quick_id: 260512-002
improvement: 6
title: Structured acceptance criteria + dependencies
date: 2026-05-12
status: complete
---

# Wave 1B — Structured acceptance criteria + dependencies Summary

Acceptance criteria are no longer opaque strings — each one is a typed object with `condition`, `type` (functional/nonfunctional/security/performance/usability), `verified_by` (test/review/demo/inspection), and a stable id. Tasks can declare `dependencies` pointing at sibling tasks by title; on Jira push, those refs are resolved against the pushed issue keys and materialized as "Blocks"/"Duplicate"/"Relates" issue links in a second pass. The whole change is backward-compatible: legacy `pipeline_output.json` checkpoints with plain-string ACs still deserialize, and the extraction prompt still accepts the legacy shape so partially-updated LLMs don't break.

## Files changed

- `models/schemas.py` — new `AcceptanceCriterionType` enum, `AcceptanceCriterion` model (id/condition/type/verified_by), and `TaskDependency` model (target_ref/reason/kind). `RawTask.acceptance_criteria` is now `Optional[list[Union[AcceptanceCriterion, str]]]` so the LLM may return either shape; `RawTask.dependencies` added. `ManagedTask.acceptance_criteria` is `Optional[list[AcceptanceCriterion]]` with a `@field_validator(mode="before")` that runs `normalize_acceptance_criteria` so legacy string-only checkpoints still load. New module-level `normalize_acceptance_criteria(items)` is the backward-compat seam.
- `pipeline/agents/extraction.py` — `EXTRACTION_PROMPT_TEMPLATE` updated: AC section documents both structured-object (preferred) and plain-string (legacy) forms; new DEPENDENCIES section explains `target_ref` matches another task's title within the same SOW; OUTPUT FORMAT block shows the new shape. After `RawTask(**raw)` parses, `extract()` calls `normalize_acceptance_criteria(task.acceptance_criteria)` so the rest of the pipeline only sees structured ACs.
- `pipeline/agents/state.py` — two new module-level helpers `_merge_acceptance_criteria` (dedup by case-insensitive condition) and `_merge_dependencies` (dedup by `(target_ref, kind)`). `_merge` uses both for continuation merges; the old `list(set(...))` for ACs is removed (it deduped by Python object id, which was meaningless for the new object form). `_promote` normalizes ACs and copies dependencies onto the `ManagedTask`.
- `pipeline/agents/deduplication.py` — `_merge_tasks` uses the same `_merge_acceptance_criteria` and `_merge_dependencies` helpers from `pipeline.agents.state` so cross-section dedup combines AC lists and dependency lists correctly.
- `integrations/jira_client.py` — `_build_description` calls a new `_render_acceptance_criterion(ac)` helper that emits Jira wiki markup `* [ ] <condition>` and appends `_(type: <type>, verified by: <verified_by>)_` in italics only when those fields are non-default (keeps simple ACs uncluttered). The helper also has a defensive branch for plain strings. New `_create_dependency_links(tasks, results)` runs after the per-task push pass: builds a normalized `title -> jira_issue_key` map, walks each task's `dependencies`, and calls `jira.create_issue_link(type=..., inwardIssue=source, outwardIssue=target)` for resolvable refs. `kind` maps to Jira link types as blocks→"Blocks", duplicates→"Duplicate", relates_to→"Relates". Unresolvable refs and per-link failures audit-log and are reflected in `JiraPushResult.warning` (no schema change to `JiraPushResult` was needed). The dep-link pass is wrapped in a top-level try/except inside `push_tasks` so a flaky link API cannot corrupt the main push contract.
- `tests/test_structured_acceptance.py` — new file, 13 unit tests covering normalization shapes, ManagedTask validator legacy-checkpoint loading, state-agent dependency propagation, state-agent continuation merge dedup, Jira renderer for both shapes, and the dependency-link resolution pass (including failure and kind-mapping branches).

## Files explicitly NOT modified

- `pipeline/orchestrator.py` — no signature changes; the dependency-link pass lives inside `JiraClient.push_tasks` as instructed.
- `pipeline/agents/gap_recovery.py` — left on the legacy AC format; the `normalize_acceptance_criteria` seam in `_promote` handles its output cleanly.
- `pipeline/indexer.py`, all UI files — out of scope.

## Commit hashes

| Commit | Subject |
|---|---|
| `9f06c65` | feat(quick-260512-002-1B): add AcceptanceCriterion and TaskDependency schemas |
| `51888cf` | feat(quick-260512-002-1B): teach extraction prompt structured ACs and dependencies |
| `905cc2e` | feat(quick-260512-002-1B): state agent normalizes ACs and carries dependencies |
| `9f84fc1` | feat(quick-260512-002-1B): dedup merge handles AcceptanceCriterion and dependencies |
| `474318f` | feat(quick-260512-002-1B): render structured ACs and materialize dependency links |
| `aa27ca6` | test(quick-260512-002-1B): add structured AC and dependency-link tests |

## Test output

### New tests (`tests/test_structured_acceptance.py`)

```
collected 13 items

tests/test_structured_acceptance.py::test_normalize_string_acceptance_criteria PASSED [  7%]
tests/test_structured_acceptance.py::test_normalize_dict_acceptance_criteria PASSED [ 15%]
tests/test_structured_acceptance.py::test_normalize_already_structured PASSED [ 23%]
tests/test_structured_acceptance.py::test_normalize_mixed_and_empty PASSED [ 30%]
tests/test_structured_acceptance.py::test_managed_task_legacy_string_acs_normalize_via_validator PASSED [ 38%]
tests/test_structured_acceptance.py::test_managed_task_carries_dependencies PASSED [ 46%]
tests/test_structured_acceptance.py::test_state_agent_merge_dedupes_acs_and_deps PASSED [ 53%]
tests/test_structured_acceptance.py::test_jira_description_renders_structured_ac PASSED [ 61%]
tests/test_structured_acceptance.py::test_jira_description_handles_legacy_string_ac PASSED [ 69%]
tests/test_structured_acceptance.py::test_dependency_link_resolution_creates_links_for_resolvable_refs PASSED [ 76%]
tests/test_structured_acceptance.py::test_dependency_link_resolution_skips_when_source_push_failed PASSED [ 84%]
tests/test_structured_acceptance.py::test_dependency_link_resolution_records_warning_on_link_failure PASSED [ 92%]
tests/test_structured_acceptance.py::test_dependency_link_resolution_maps_kind_to_link_type PASSED [100%]

======================== 13 passed, 1 warning in 0.44s =========================
```

### Full suite (excluding pre-existing broken collections, same exclusions 1A documented)

```
venv/bin/pytest tests/ --ignore=tests/test_hierarchical_judge.py --ignore=tests/test_phase11_evals.py

32 passed, 7 warnings in 1.73s
```

Breakdown: 6 from `test_hierarchy_preservation.py` (Wave 1A), 8 from `test_phase2_runtime_reliability.py`, 5 from `test_routing.py`, 13 from the new `test_structured_acceptance.py`. No regressions.

The same two pre-existing-broken collectors that Wave 1A flagged remain out of scope here:

- `tests/test_hierarchical_judge.py` — `ModuleNotFoundError: No module named 'langchain_openai'`
- `tests/test_phase11_evals.py` — `sys.path` boilerplate missing, `from models.eval_schemas import …` fails

Both reproduce on `c31de01` (post-1A, pre-1B), so they are not caused by this wave.

## Backward compatibility notes

- **Old `pipeline_output.json` files** with plain-string ACs (`acceptance_criteria: ["[ ] foo"]`) deserialize via the `@field_validator(mode="before")` on `ManagedTask`. Verified by `test_managed_task_legacy_string_acs_normalize_via_validator`.
- **Old extraction LLM output** with plain-string ACs is accepted by `RawTask` (the `Union[AcceptanceCriterion, str]` member) and then normalized post-parse. The prompt still teaches the legacy shape so partially-updated providers do not break.
- **Old `RawTask` dicts without `dependencies`** default to `[]`. No migration needed.
- **`JiraPushResult`** schema is unchanged. Dependency-link failures are surfaced via the existing `warning` field (pipe-separated when multiple).
- **Gap recovery** still emits plain-string ACs from its (untouched) prompt; the `_promote` normalizer turns them into `AcceptanceCriterion` objects with default `type=functional`/`verified_by=test`. No downstream consumer cares.

## Behaviour notes for Wave 3 integration

- `JiraClient.push_tasks` is unchanged externally — still `(tasks: list[ManagedTask]) -> list[JiraPushResult]`. Wave 3 wiring does not need to touch the orchestrator's push call site.
- The dependency-link pass uses a normalized lowercased title lookup. Title collisions across the same SOW (two distinct tasks with identical titles) are best-effort: the lookup keeps the **last** key seen for that title, so links will route to whichever was pushed last. This is acceptable per the plan's "best-effort string match" contract.
- The dep-link pass runs only on tasks that pushed successfully and have a `jira_issue_key`. Failed pushes contribute neither a source nor a target.
- Link calls do **not** retry. A flaky 5xx leaves `JiraPushResult.warning` with the message and moves on.

## Scope confirmation

- No changes to `pipeline/orchestrator.py`, `pipeline/indexer.py`, `pipeline/agents/gap_recovery.py`, or any UI file.
- No new dependencies added to `requirements.txt`.
- No CLAUDE.md directives violated (snake_case naming, minimal docstrings, no over-engineering, error handling at boundaries).
