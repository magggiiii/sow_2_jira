---
wave: 2A
quick_id: 260512-002
improvement: 2
title: Semantic coverage check
date: 2026-05-12
status: complete
---

# Wave 2A — Semantic coverage check Summary

Structural coverage answered "does this PageIndex node have ≥1 extracted task?" — it cannot see dense sections where one task was pulled from three pages of dense spec. Wave 2A adds a per-section semantic audit: given the section text plus the titles + acceptance-criteria conditions of the tasks that WERE extracted, ask the LLM "what concrete actionable deliverables in this section are NOT covered?" and surface them as `MissedItem` records. The orchestrator (Wave 3) can then either auto-flag the section INCOMPLETE or trigger targeted gap recovery, while existing structural coverage and the gap_recovery zero-task path keep working unchanged.

The checker is intentionally conservative (the prompt biases against false positives) and gracefully degrades — short sections, empty task lists, LLM exceptions, and malformed JSON responses all return an empty report so the rest of the pipeline cannot break on a flaky audit.

## Files changed

- **New `pipeline/agents/coverage_check.py`** — `CoverageChecker` class with `check_section(node, section_text, extracted_tasks) -> SectionCoverageReport`. Two module-local Pydantic models (`MissedItem`, `SectionCoverageReport`) kept inside this agent module per the plan's "self-contained agent" constraint. Imports `normalize_acceptance_criteria` from `models.schemas` so the extracted-task summary it builds handles both post-1B structured ACs and legacy plain-string ACs from old `pipeline_output.json` checkpoints. LLM call goes through `LLMClient.complete_json` exclusively; on `ValueError`/`RuntimeError` the checker logs `COVERAGE_CHECK_ERROR` and returns an empty report. Audit trail: `COVERAGE_CHECK_SKIPPED` on short/no-task paths, `COVERAGE_CHECK` summary row per real call, `COVERAGE_MISS` row per surfaced miss, `COVERAGE_CHECK_ERROR` for LLM/parse failures, `COVERAGE_MISS_PARSE_ERROR` for individual malformed items inside an otherwise-valid list.

- **New `tests/test_coverage_check.py`** — 6 unit tests covering the short-section short-circuit, the no-tasks short-circuit, normal LLM parsing of two misses, `min_confidence` filtering, LLM-error graceful degradation, and non-list-response graceful degradation. All collaborators mocked with `unittest.mock.MagicMock` — no live LLM calls.

## Files explicitly NOT modified

- `pipeline/orchestrator.py` — Wave 3 integrates the checker; this wave does not touch the orchestrator.
- `pipeline/agents/extraction.py`, `pipeline/agents/gap_recovery.py`, `pipeline/agents/state.py`, `pipeline/agents/deduplication.py` — agent code outside scope.
- `models/schemas.py` — `MissedItem` and `SectionCoverageReport` stay in `pipeline/agents/coverage_check.py` per the plan's instruction to keep the agent module self-contained.
- `requirements.txt` — no new dependencies.
- Any UI file or anything under `pageindex/`.

## Branch / commit hashes

Branch: `wave-2a-coverage-check` (created at `babf6b4` — the post-1B tip on the worktree).

| Commit | Subject |
|---|---|
| `f151d6c` | feat(quick-260512-002-2A): add CoverageChecker for semantic per-section audit |
| `4e71058` | test(quick-260512-002-2A): add coverage check unit tests |

## Test output

### New tests (`tests/test_coverage_check.py`)

```
collected 6 items

tests/test_coverage_check.py::test_short_section_returns_empty PASSED    [ 16%]
tests/test_coverage_check.py::test_no_extracted_tasks_returns_empty PASSED [ 33%]
tests/test_coverage_check.py::test_missed_items_parsed_from_llm PASSED   [ 50%]
tests/test_coverage_check.py::test_low_confidence_items_filtered_when_below_threshold PASSED [ 66%]
tests/test_coverage_check.py::test_llm_error_returns_empty_report_not_crash PASSED [ 83%]
tests/test_coverage_check.py::test_invalid_json_response_returns_empty_report PASSED [100%]

========================= 6 passed, 1 warning in 1.70s =========================
```

### Regression: Waves 1A + 1B

```
venv/bin/pytest tests/test_hierarchy_preservation.py tests/test_structured_acceptance.py -v
========================= 19 passed, 7 warnings in 1.58s =========================
```

### Full suite (excluding pre-existing broken collectors)

```
venv/bin/pytest tests/ --ignore=tests/test_hierarchical_judge.py --ignore=tests/test_phase11_evals.py
========================= 38 passed, 7 warnings in 1.47s =========================
```

Breakdown: 6 from `test_hierarchy_preservation.py` (Wave 1A) + 13 from `test_structured_acceptance.py` (Wave 1B) + 8 from `test_phase2_runtime_reliability.py` + 5 from `test_routing.py` + 6 new from `test_coverage_check.py` = 38. No regressions.

The same two pre-existing-broken collectors that Waves 1A and 1B flagged remain out of scope here:

- `tests/test_hierarchical_judge.py` — `ModuleNotFoundError: No module named 'langchain_openai'`
- `tests/test_phase11_evals.py` — `sys.path` boilerplate missing, `from models.eval_schemas import …` fails

Both reproduce on `babf6b4` before any Wave 2A change.

## Public API contract (for Wave 3)

```python
from pipeline.agents.coverage_check import CoverageChecker, SectionCoverageReport

checker = CoverageChecker(
    llm_client=self.llm_client,           # the run's LLMClient
    audit_logger=self.audit_logger,
    run_id=self.run_id,
    min_confidence=0.6,                   # default; tighter -> fewer false positives
    max_section_chars=16000,              # matches ExtractionAgent's section cap
)

report: SectionCoverageReport = checker.check_section(
    node=node_dict,                       # the same PageIndex node dict the extractor receives
    section_text=section_text,            # the same text the extractor saw
    extracted_tasks=tasks_for_this_node,  # list[ManagedTask] just produced for this node
)

if report.missed_items:
    # Wave 3: orchestrator decides — flag INCOMPLETE on the section,
    # or pass these into a targeted gap-recovery pass with the misses as hints.
    ...
```

`SectionCoverageReport` fields:

- `node_id: str`
- `extracted_count: int` (how many tasks were already extracted for this node)
- `missed_items: list[MissedItem]` (filtered by `min_confidence`; can be empty)
- `checker_confidence: float` (mean confidence across surviving missed items; 0.0 if empty)
- `checked_at: datetime.datetime`

`MissedItem` fields: `description: str`, `confidence: float` in `[0, 1]`, `reason: str`.

## Orchestrator integration sketch

The cleanest seam in `PipelineOrchestrator.run()` is **after** `state_agent.process(...)` returns its `(updated_open_tasks, newly_closed_tasks)` tuple for the current node — at that point we know which tasks belong to this node and we still hold the node dict and `section_text` from the extractor call. The exact placement depends on whether the orchestrator runs a multi-chunk per-node loop or a single pass per node; both shapes work:

### Pattern A — single-pass per node (one section_text per node_id)

```python
# inside the per-node loop, immediately after state_agent.process(...)
section_tasks = [t for t in updated_open_tasks + newly_closed_tasks
                 if any(ref.node_id == node["node_id"] for ref in t.source_refs)]

if os.getenv("SEMANTIC_COVERAGE", "1") == "1":          # togglable per the risk register
    report = coverage_checker.check_section(
        node=node,
        section_text=section_text,
        extracted_tasks=section_tasks,
    )
    section_coverage_reports[node["node_id"]] = report
    if report.missed_items:
        for t in section_tasks:
            if TaskFlag.INCOMPLETE not in t.flags:
                t.flags.append(TaskFlag.INCOMPLETE)
        # OR (alternative): hand `report.missed_items` to a targeted
        # GapRecoveryAgent.recover_with_hints(...) pass — Wave 3 picks one.
```

### Pattern B — multi-chunk per node (call once after the chunk loop closes the node)

If the orchestrator iterates over chunks of a single node before "closing" it, defer the check until the node closes — otherwise the checker sees a partial extracted_tasks list and over-reports misses. Pseudocode:

```python
# after the chunk loop for `node` completes:
section_tasks = [t for t in all_node_tasks if t.status in (TaskStatus.OPEN, TaskStatus.CLOSED)]
report = coverage_checker.check_section(node=node, section_text=full_section_text,
                                         extracted_tasks=section_tasks)
```

### Persistence (suggestion for Wave 3)

`SectionCoverageReport` is a Pydantic model, so dumping is one call:

```python
reports_path = Path(f"data/sessions/{run_id}/coverage_reports.json")
reports_path.parent.mkdir(parents=True, exist_ok=True)
reports_path.write_text(json.dumps(
    {nid: r.model_dump(mode="json") for nid, r in section_coverage_reports.items()},
    indent=2,
))
```

This pairs naturally with the `node_index.json` that Wave 1A already persists at the same path.

### Toggle (per the plan's risk register)

Improvements #2 and #4 add ~1.5x LLM calls per section. Suggest an env flag the orchestrator honors when instantiating / invoking the checker. The CoverageChecker class itself is intentionally toggle-agnostic — keep the gating in the orchestrator so its behavior is observable from one place.

## Backward compatibility notes

- **Legacy ManagedTask checkpoints** (plain-string ACs) feed the checker without a hiccup — `_summarize_extracted` calls `normalize_acceptance_criteria` on the AC list before pulling `.condition` out, so both shapes render uniformly.
- **Nodes without the post-1A fields** (`parent_id`, `parent_chain`, `depth`) work too — the checker only reads `node_id`, `title`, `page_start`, `page_end` via `.get()` with safe defaults. Wave 1A's structural fields are not required.
- **`AuditLogger` schema** unchanged — only new `action` strings (`COVERAGE_CHECK`, `COVERAGE_MISS`, `COVERAGE_CHECK_SKIPPED`, `COVERAGE_CHECK_ERROR`, `COVERAGE_MISS_PARSE_ERROR`) appear in the `audit_log.action` text column.

## Scope confirmation

- No changes to `pipeline/orchestrator.py`, `pipeline/agents/extraction.py`, `pipeline/agents/gap_recovery.py`, `pipeline/agents/state.py`, `pipeline/agents/deduplication.py`, `pipeline/coverage.py`, or `models/schemas.py`.
- No UI files or `pageindex/` files touched.
- No new dependencies added to `requirements.txt`.
- No CLAUDE.md directives violated (snake_case naming, minimal docstrings, error handling at boundaries via try/except, no plaintext credential handling, LLM calls only through `LLMClient`).

## Caveats

- The branch was created from `babf6b4` (post-1B summary commit). The worktree HEAD was at `44ca803` on `main`, which does not yet contain the Waves 1A/1B commits. A new branch `wave-2a-coverage-check` was created at `babf6b4` to ensure the Wave 2A work builds on the correct post-1B baseline; the original `worktree-agent-a98320f3a9b892d55` branch was not touched.
- The checker prompt deliberately biases toward conservativism. If Wave 3 finds it under-reports on real SOWs, the right tuning surface is the prompt's "BE CONSERVATIVE" line — not lowering `min_confidence` (that would let the LLM's own low-confidence guesses through too).
- Audit volume: one row per call plus one per miss. On a 50-section SOW with a typical 2-misses-per-section rate this is ~150 rows, which is well within the existing `audit.db` profile.
