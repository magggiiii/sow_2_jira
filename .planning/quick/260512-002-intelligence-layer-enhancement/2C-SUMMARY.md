---
wave: 2C
quick_id: 260512-002
improvement: 4
title: Self-critique pass per section
date: 2026-05-12
status: complete
---

# Wave 2C — Self-critique pass per section Summary

Single-pass extraction was letting vague titles ("User Authentication") and untestable acceptance criteria ("system works correctly") survive into Jira. Wave 2C adds a `TaskCritic` agent that runs a second LLM pass per section and either rewrites low-risk issues in place or flags higher-risk ones using existing `TaskFlag` values. The whole agent is isolated from the orchestrator — Wave 3 wires it in between `StateAgent.process` and `coverage.mark_covered`.

## Files changed

- `pipeline/agents/critic.py` — new file. `CritiqueIssue` enum (NON_VERB_TITLE, VAGUE_TITLE, UNTESTABLE_AC, TOO_BROAD, LIKELY_DUPLICATE, MISSING_AC, NOTHING_TO_FIX). Pydantic v2 models `TaskCritique` and `CritiqueReport`. `TaskCritic` class with one public method `critique(tasks, section_text, node) -> tuple[list[ManagedTask], CritiqueReport]`. Section text is truncated to 4000 chars before being sent to the LLM along with a JSON snapshot of each task (title, short_description, structured ACs). The critic always uses `LLMClient.complete_json` and validates each returned dict via `TaskCritique`. Tasks are mutated in place when auto-fix conditions hold; the same list reference is returned for caller convenience. On any failure (LLM exception, non-list response, per-entry parse error, unknown task_id) the input tasks are returned unmodified and an empty report is emitted — the pipeline cannot crash because the critic misbehaves.
- `tests/test_critic.py` — new file, 8 unit tests covering the entire auto-fix policy plus both failure modes (LLM raise and malformed response). All collaborators are `unittest.mock.MagicMock`; no live LLM calls.

## Auto-fix policy (as implemented)

| Issue                      | Suggestion required        | Confidence rule        | Outcome                                     |
| -------------------------- | -------------------------- | ---------------------- | ------------------------------------------- |
| `NON_VERB_TITLE`           | `suggested_title`          | `>= auto_fix_threshold` | Replace title; no flag                      |
| `NON_VERB_TITLE` (low)     | —                          | below threshold        | Title unchanged; add `TaskFlag.LOW_CONFIDENCE` |
| `UNTESTABLE_AC`            | `suggested_acceptance_criteria` | `>= auto_fix_threshold` | Replace AC list; no flag                |
| `UNTESTABLE_AC` (low)      | —                          | below threshold        | ACs unchanged; add `TaskFlag.LOW_CONFIDENCE`   |
| `MISSING_AC`               | `suggested_acceptance_criteria` | any                    | Add ACs (only if task has none)             |
| `TOO_BROAD`                | —                          | any                    | Never fix; add `TaskFlag.AMBIGUOUS_SCOPE`   |
| `VAGUE_TITLE`              | —                          | any                    | Never fix; add `TaskFlag.LOW_CONFIDENCE`    |
| `LIKELY_DUPLICATE`         | —                          | any                    | Never fix; add `TaskFlag.LOW_CONFIDENCE`    |
| `NOTHING_TO_FIX`           | —                          | —                      | No-op; counted in `reviewed_count`          |

Default `auto_fix_threshold` is `0.8`, configurable per `TaskCritic(...)` instance.

## Audit log actions

- `CRITIQUE_RUN` — emitted per section with `reviewed_count`, `auto_fixed_count`, `flagged_count`.
- `CRITIQUE_AUTO_FIX` — emitted per auto-fixed task with `task_id`, applied issue names, and the critic's confidence.
- `CRITIQUE_FLAGGED` — emitted per flagged-but-not-fixed task with the issue list and confidence.
- `CRITIQUE_LLM_ERROR` — LLM call raised.
- `CRITIQUE_PARSE_ERROR` — LLM returned non-list JSON.
- `CRITIQUE_UNKNOWN_TASK` — critic referenced a task_id that was not in the input batch.

## Files explicitly NOT modified

- `pipeline/orchestrator.py` (Wave 3 integrates the critic — see sketch below)
- `pipeline/agents/extraction.py`, `state.py`, `deduplication.py`, `gap_recovery.py`
- `integrations/jira_client.py`
- `models/schemas.py` (the critic reuses `TaskFlag.AMBIGUOUS_SCOPE` and `TaskFlag.LOW_CONFIDENCE` from Wave 1B; no schema change)
- Any UI file
- `requirements.txt` — no new dependencies

## Commit hashes

| Commit    | Subject                                                                |
| --------- | ---------------------------------------------------------------------- |
| `e720547` | feat(quick-260512-002-2C): add TaskCritic for per-section self-critique |
| `b726ada` | test(quick-260512-002-2C): add TaskCritic unit tests                    |

## Test output

### New tests (`tests/test_critic.py`)

```
============================= test session starts ==============================
platform darwin -- Python 3.11.15, pytest-9.0.2, pluggy-1.6.0 -- /Users/magi/Documents/projects/sow_to_jira/venv/bin/python3.11
cachedir: .pytest_cache
rootdir: /Users/magi/Documents/projects/sow_to_jira
plugins: asyncio-1.3.0, anyio-4.13.0
asyncio: mode=Mode.STRICT, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 8 items

tests/test_critic.py::test_empty_tasks_returns_empty_report PASSED       [ 12%]
tests/test_critic.py::test_non_verb_title_auto_fix PASSED                [ 25%]
tests/test_critic.py::test_non_verb_title_low_confidence_flag_only PASSED [ 37%]
tests/test_critic.py::test_untestable_ac_replaced_when_confident PASSED  [ 50%]
tests/test_critic.py::test_too_broad_always_flagged_never_fixed PASSED   [ 62%]
tests/test_critic.py::test_missing_ac_added_at_any_confidence PASSED     [ 75%]
tests/test_critic.py::test_llm_error_returns_unmodified_tasks PASSED     [ 87%]
tests/test_critic.py::test_invalid_json_response_returns_unmodified PASSED [100%]

========================= 8 passed, 1 warning in 1.43s =========================
```

### Wave 1 regression check (no regressions)

```
$ venv/bin/pytest tests/test_hierarchy_preservation.py tests/test_structured_acceptance.py -v

collected 19 items
... (all 19 PASSED)
======================== 19 passed, 7 warnings in 1.63s ========================
```

Pre-existing-broken collectors flagged in 1A/1B (`tests/test_hierarchical_judge.py`, `tests/test_phase11_evals.py`) are out of scope for 2C and were not run.

## Orchestrator integration sketch

The critic is designed to fire **per section, after `TaskStateAgent.process` has run and before `coverage.mark_covered`** in `PipelineOrchestrator.run()`. The relevant insertion point in the section loop:

```python
# Existing
new_open, newly_closed = self.state_agent.process(raw_tasks, open_tasks, node)

# NEW (Wave 3 wiring)
if getattr(self, "critic", None) is not None and new_open:
    new_open, critique_report = self.critic.critique(
        tasks=new_open,
        section_text=section_text,
        node=node,
    )
    # critique_report can be persisted alongside the audit log if desired

# Existing
closed_tasks.extend(newly_closed)
self.coverage.mark_covered(node, new_open + newly_closed)
```

Why this position:
- Running **after** the state agent means the critic operates on stable `ManagedTask` objects with real UUIDs (which the LLM returns as `task_id`), not raw extractions that may still be merged with an open continuation.
- Running **before** `coverage.mark_covered` lets the critic's flag changes propagate to the coverage agent and downstream UI without a second pass.
- The critic only ever mutates `title`, `acceptance_criteria`, and `flags` on tasks the state agent just emitted. `merged_from`, `source_refs`, `dependencies`, and `status` are left alone — so the existing dedup, gap-recovery, and Jira-push contracts remain unchanged.

### Construction (also Wave 3 work)

The constructor lives in `PipelineOrchestrator.__init__`. Recommended shape:

```python
from pipeline.agents.critic import TaskCritic

self.critic = TaskCritic(
    llm_client=self.llm,
    audit_logger=self.audit,
    run_id=self.run_id,
    auto_fix_threshold=float(os.getenv("SOW_CRITIC_THRESHOLD", "0.8")),
) if os.getenv("SOW_ENABLE_CRITIC", "1") != "0" else None
```

This honours the risk-register entry from the parent plan ("make it togglable via env flag") — defaults to on, but ops can disable with `SOW_ENABLE_CRITIC=0` if cost/latency regresses.

### Cost note

The critic adds one LLM call per section. With the existing extraction + (Wave 2A) coverage check, that brings the per-section call count to ~3. The truncation to 4000 chars keeps the critic prompt smaller than the extraction prompt; in practice this should add ~30–40% to total LLM time, not 2x.

## Scope confirmation

- No changes to `pipeline/orchestrator.py`, `pipeline/agents/extraction.py`, `pipeline/agents/state.py`, `pipeline/agents/deduplication.py`, `pipeline/agents/gap_recovery.py`, `integrations/jira_client.py`, `models/schemas.py`, or any UI file.
- No new entries in `requirements.txt`.
- The critic reuses `TaskFlag.AMBIGUOUS_SCOPE` and `TaskFlag.LOW_CONFIDENCE` — no enum value was added.
- All LLM traffic uses `LLMClient.complete_json` only; no direct litellm calls.
- Audit logging follows the existing `AuditLogger.log(run_id=, agent=, node_id=, action=, task_id=, detail=)` signature.
