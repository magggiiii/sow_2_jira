---
wave: 2B
quick_id: 260512-002
improvement: 3
title: Section classifier + few-shot extraction
date: 2026-05-12
status: complete
---

# Wave 2B — Section classifier + few-shot extraction Summary

Today the extraction prompt runs on every PageIndex node, even pure context, legal, glossary, and signature sections. That wastes tokens and pollutes the task list with noise. Wave 2B splits the work in two: a cheap `SectionClassifier` pass tags each section, and the orchestrator (Wave 3) will only call the full extractor on sections the classifier judges actionable or mixed. The extraction prompt itself also gets three in-context few-shot exemplars and a `scratchpad` field — the LLM thinks out loud once before listing tasks, and that reasoning is audit-logged but never propagates downstream. The change is fully backward-compatible: smaller models that return a bare task array (legacy shape) still work, and prior 1A/1B tests pass unchanged.

## Files changed

- `pipeline/agents/classifier.py` — **new module**. Exposes `SectionType` (actionable / context / legal / definitions / signature / mixed), `ClassificationResult` (node_id, type, confidence, reason), and `SectionClassifier`. The prompt asks for a single JSON object, biases toward "actionable" on ambiguity, and reads only the first 4000 chars by default. `classify()` defaults to MIXED with confidence 0 on any LLM error, non-dict response, or unknown enum value — safe fallback that keeps the extractor running. `should_extract()` returns True on ACTIONABLE/MIXED **or** when confidence is below `min_confidence` (default 0.7) — false negatives are more expensive than false positives. Audit-logs `CLASSIFIED`, `CLASSIFY_ERROR`, `CLASSIFY_PARSE_ERROR`, `CLASSIFY_INVALID_TYPE`. Confidence values outside [0,1] are clamped.
- `pipeline/agents/extraction.py` — prompt now requests a single JSON object `{"scratchpad": "...", "tasks": [...]}` instead of a bare array. Three compact in-context examples (feature work / integration / data migration) live inline in `EXTRACTION_PROMPT_TEMPLATE` per the plan; each example shows verb-first titles, structured AC objects, per-task `confidence`, and at least one dependency. `extract()` parses the new wrapper, falls back to a bare list when the LLM returns the legacy shape, and audit-logs the scratchpad with `action="EXTRACTION_SCRATCHPAD"` (max 1000 chars) — it never appears on `RawTask` or anywhere downstream. The existing `LOW_CONFIDENCE` auto-flag at `confidence_threshold` is preserved.
- `tests/test_classifier.py` — **new file**, 10 unit tests covering happy paths, the low-confidence override, every fallback branch (RuntimeError, ValueError, non-dict response, unknown type), the short-section short-circuit, and the >1.0 confidence clamp.
- `tests/test_extraction.py` — **new file**, 7 unit tests covering: wrapper parsing + scratchpad audit isolation, wrapper-without-scratchpad, malformed-tasks-field, legacy bare-list shape, unsupported response types, LOW_CONFIDENCE auto-flag unchanged, and the existing <50 char short-section gate.

## Files explicitly NOT modified

- `pipeline/orchestrator.py` — Wave 3 integrates the classifier. See **Orchestrator integration sketch** below.
- `pipeline/agents/state.py`, `pipeline/agents/deduplication.py`, `pipeline/agents/gap_recovery.py` — out of scope.
- `integrations/jira_client.py`, any UI file, anything in `pageindex/` — out of scope.
- `requirements.txt` — no new dependencies.

## Commit hashes

| Commit | Subject |
|---|---|
| `d0435d7` | feat(quick-260512-002-2B): add SectionClassifier agent |
| `049fc59` | feat(quick-260512-002-2B): few-shot exemplars and scratchpad in extraction prompt |
| `f70efa8` | test(quick-260512-002-2B): cover SectionClassifier and extraction wrapper |

## Test output

### New tests (`tests/test_classifier.py` + `tests/test_extraction.py`)

```
$ venv/bin/pytest tests/test_classifier.py tests/test_extraction.py -v

collected 17 items

tests/test_classifier.py::test_classifier_actionable_section PASSED      [  5%]
tests/test_classifier.py::test_classifier_legal_section PASSED           [ 11%]
tests/test_classifier.py::test_classifier_mixed_section_extracts PASSED  [ 17%]
tests/test_classifier.py::test_classifier_low_confidence_extracts PASSED [ 23%]
tests/test_classifier.py::test_classifier_llm_error_defaults_to_mixed PASSED [ 29%]
tests/test_classifier.py::test_classifier_value_error_defaults_to_mixed PASSED [ 35%]
tests/test_classifier.py::test_classifier_invalid_json_defaults_to_mixed PASSED [ 41%]
tests/test_classifier.py::test_classifier_invalid_type_defaults_to_mixed PASSED [ 47%]
tests/test_classifier.py::test_classifier_short_section_short_circuits_to_mixed PASSED [ 52%]
tests/test_classifier.py::test_classifier_confidence_outside_range_is_clamped PASSED [ 58%]
tests/test_extraction.py::test_extraction_parses_scratchpad_wrapper PASSED [ 64%]
tests/test_extraction.py::test_extraction_scratchpad_missing_is_ok PASSED [ 70%]
tests/test_extraction.py::test_extraction_wrapper_with_non_list_tasks_returns_empty PASSED [ 76%]
tests/test_extraction.py::test_extraction_falls_back_to_bare_array PASSED [ 82%]
tests/test_extraction.py::test_extraction_unsupported_response_type_returns_empty PASSED [ 88%]
tests/test_extraction.py::test_extraction_low_confidence_flag_unchanged PASSED [ 94%]
tests/test_extraction.py::test_extraction_short_section_skipped PASSED   [100%]

======================== 17 passed, 1 warning in 1.49s =========================
```

### Wave 1A + 1B regression check

```
$ venv/bin/pytest tests/test_hierarchy_preservation.py tests/test_structured_acceptance.py -v

======================== 19 passed, 7 warnings in 1.50s ========================
```

All 6 hierarchy tests and all 13 structured-AC/dependency tests still green — no regression from the prompt rewrite or the new audit `EXTRACTION_SCRATCHPAD` action.

### Full suite (excluding pre-existing broken collections)

```
$ venv/bin/pytest tests/ --ignore=tests/test_hierarchical_judge.py --ignore=tests/test_phase11_evals.py

49 passed, 7 warnings in 1.96s
```

Breakdown: 6 hierarchy + 13 structured AC + 8 phase2 runtime reliability + 5 routing + 10 classifier + 7 extraction = 49.

The two pre-existing-broken collectors (`test_hierarchical_judge.py` missing `langchain_openai`; `test_phase11_evals.py` `sys.path` boilerplate) that Waves 1A and 1B both flagged remain out of scope — they reproduce on the parent commit `babf6b4` before any Wave 2B change.

## Behaviour notes

- **Bias is intentional.** `SectionClassifier.should_extract` returns True on `ACTIONABLE`, `MIXED`, **and** any low-confidence result. The extractor's own short-section gate and downstream agents are the last line of defense; we never want the classifier to silently swallow real work.
- **Classifier prompt budget.** Default `max_section_chars=4000` (vs. the extractor's 16000) keeps the gating pass cheap — roughly one classifier call is 1/4 the prompt of one extraction call, before the extractor's exemplars are counted.
- **Scratchpad is audit-only.** It is logged with `action="EXTRACTION_SCRATCHPAD"` truncated to 1000 chars. It is **never** set on `RawTask` (the schema has no such field) and never persists in `pipeline_output.json`. Reviewers can correlate it with the extracted tasks via `run_id` + `node_id`.
- **Legacy LLM output still works.** A bare list from older or smaller providers parses unchanged. The fallback log line is silent on success and only emits `EXTRACTION_ERROR` when the response is neither a wrapper dict nor a list.

## Orchestrator integration sketch

Wave 3 wires the classifier into `PipelineOrchestrator.run()`. The recommended shape (do not implement here — this is for the integrator):

```python
# In PipelineOrchestrator.__init__, after llm_client is built:
from pipeline.agents.classifier import SectionClassifier, SectionType

self.classifier = SectionClassifier(
    llm_client=self.llm_client,
    audit_logger=self.audit_logger,
    run_id=run_config.run_id,
    min_confidence=0.7,         # or pull from config/sow_config.json
    max_section_chars=4000,
)

# In the per-node loop inside run(), BEFORE the existing call to
# self.extraction_agent.extract(node, section_text, ...):
classification = self.classifier.classify(node, section_text)
if not self.classifier.should_extract(classification):
    # Audit-logged inside classify(); just skip extraction here.
    self._set_status(f"Skipping non-actionable section: {node['title']} "
                     f"({classification.type.value}, conf={classification.confidence:.2f})")
    continue   # next node — no tasks emitted for this section

raw_tasks = self.extraction_agent.extract(node, section_text, hierarchy=self.hierarchy,
                                          status_callback=self.status_callback)
```

Two operational notes for the integrator:

1. The orchestrator already persists `node_index.json` and `pipeline_output.json` per `run_id`. The classifier doesn't write any new files — its decisions live in `data/audit.db`. If desired, Wave 3 can also persist a `classifications.json` mapping `node_id → ClassificationResult.model_dump()` alongside the node index, but it is not required.
2. The gating should be **toggleable**: per the plan's risk register, Wave 2 work adds 1.5x LLM calls per section and must be controllable via env flag. Suggest `SOW_CLASSIFIER_ENABLED=1` (default on) with a `0` bypass that calls `self.extraction_agent.extract` directly. The wiring is one if-check around the snippet above.

## Backward compatibility

- **Older LLM providers** that ignore the new prompt and return a bare task array continue to parse cleanly (`test_extraction_falls_back_to_bare_array`).
- **Existing `pipeline_output.json` checkpoints** are unaffected — no schema change to `RawTask`/`ManagedTask`.
- **Existing audit consumers** see one new action: `EXTRACTION_SCRATCHPAD`. Filtering by `action` continues to work; existing dashboards just have a new optional row type.
- **CLAUDE.md directives observed:** snake_case module + function naming, minimal docstrings, error handling at the LLM boundary, no plaintext secret writes, no `litellm` imports outside `pipeline/llm_client.py`, no changes to `requirements.txt`.

## Scope confirmation

Files NOT touched (per the plan):

- `pipeline/orchestrator.py` — Wave 3
- `pipeline/agents/state.py` — Wave 1A territory
- `pipeline/agents/deduplication.py` — Wave 2D territory
- `pipeline/agents/gap_recovery.py` — out of scope
- `integrations/jira_client.py` — Wave 1B territory
- Any UI file or `pageindex/*` — out of scope
- `requirements.txt` — unchanged
