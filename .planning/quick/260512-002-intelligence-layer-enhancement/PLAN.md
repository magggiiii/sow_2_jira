---
quick_id: 260512-002
slug: intelligence-layer-enhancement
date: 2026-05-12
mode: quick-multi-wave
---

# Quick Task 260512-002 — Intelligence layer enhancement (6-improvement program)

## Goal

Implement the 6 improvements scoped in the deep-analysis turn (see chat history 2026-05-12). The intelligence layer must produce better ticket content, real Epic/Story/Sub-task hierarchies, and verifiable coverage — not just structural coverage.

## Improvements (1–6, owner = subagent)

1. **Hierarchy preservation** — carry PageIndex `parent_id`/`depth` through `flatten_tree` → `SourceRef` → `JiraClient`. Group containers by parent_id, not section_title string.
2. **Semantic coverage** — new `CoverageChecker` agent: after each section's extraction, second LLM call asks what concrete deliverables were missed. Flag INCOMPLETE or trigger targeted gap recovery.
3. **Classifier + few-shot extraction** — small classifier tags sections actionable|context|legal|defs|signature. Real extractor runs only on actionable, with 2-3 in-context exemplars and a scratchpad field.
4. **Self-critique pass** — new `Critic` agent: rates extracted tasks on verb-first title, testable AC, atomic scope. Auto-fixes trivial issues, flags others.
5. **Persisted hierarchical dedup** — embeddings written to `data/sessions/<run_id>/embeddings.npz` and project-level index. FAISS/sklearn replaces O(n²). Cross-run hits flagged with prior Jira key.
6. **Structured ACs + dependencies** — `acceptance_criteria` → `list[{id, condition, type, verified_by}]`. New `dependencies` field. Jira push materializes deps as "Blocks" links.

## Sequencing

### Wave 1 — Schema foundation (serial, main repo, NO worktrees)

Schema changes must land authoritatively before wave 2 forks. Each agent works on `main`, commits atomically, then I review.

- **1A — Improvement #1 (hierarchy)** — modifies `pipeline/indexer.py`, `models/schemas.py` (SourceRef), `pipeline/agents/state.py`, `integrations/jira_client.py`. Adds parent_id propagation and tree-aware grouping.
- **1B — Improvement #6 (structured ACs + deps)** — modifies `models/schemas.py` (RawTask, ManagedTask, AcceptanceCriterion, Dependency), `pipeline/agents/extraction.py` prompt, `pipeline/agents/state.py` merge, `integrations/jira_client.py` for issue links. Depends on 1A being merged.

### Wave 2 — Additive intelligence (parallel, isolated worktrees)

All four agents work in their own git worktrees. They DO NOT modify `pipeline/orchestrator.py` — instead they expose a clean class API I integrate in Wave 3.

- **2A — Improvement #2 (semantic coverage)** — new `pipeline/agents/coverage_check.py` with `CoverageChecker.check_section(node, section_text, extracted_tasks) -> list[MissedItem]`. Tests in `tests/test_coverage_check.py`.
- **2B — Improvement #3 (classifier + few-shot)** — new `pipeline/agents/classifier.py` with `SectionClassifier.classify(node, section_text) -> SectionType`. Update `pipeline/agents/extraction.py` to add few-shot exemplars and scratchpad field. Tests in `tests/test_classifier.py`.
- **2C — Improvement #4 (self-critique)** — new `pipeline/agents/critic.py` with `TaskCritic.critique(tasks, section_text) -> tuple[fixed_tasks, flagged_tasks]`. Tests in `tests/test_critic.py`.
- **2D — Improvement #5 (persisted cross-run dedup)** — modifies `pipeline/agents/deduplication.py` to use sklearn `NearestNeighbors` (avoid FAISS wheel issue), new `pipeline/agents/cross_run_index.py` for project-level embedding persistence. Tests in `tests/test_dedup.py`.

### Wave 3 — Integration + verification (me)

After all four wave-2 branches merge:
1. Wire each new agent into `PipelineOrchestrator.run()` in the right step.
2. Run `venv/bin/pytest tests/` — full suite green.
3. Run `tests/test_hierarchical_judge.py` eval (if dataset present) to sanity-check quality.

## Risk register

| Risk | Mitigation |
|---|---|
| Schema drift between 1A and 1B | 1B reads the post-1A state; explicit contract in 1B prompt |
| Wave-2 agents stomp orchestrator.py | Each agent is forbidden to touch orchestrator.py; integration is Wave 3 only |
| Worktree branches diverge from main between launch and merge | Wave 1 commits before Wave 2 launches; Wave 2 worktrees branch from up-to-date main |
| Pipeline cost/latency regression | Improvements #2 and #4 add ~1.5x LLM calls per section; document this in SUMMARY and make them togglable via env flag |
| Agent claims success without working tests | Each agent must include a `pytest` invocation and quote the green output in its SUMMARY.md |

## Non-goals

- UI changes (per-improvement UI work is a separate task)
- Eval-harness expansion (existing Phase 11 evals are the regression gate)
- Frontend rendering of new fields (Jira-side rendering only)
- Performance tuning (correctness first; perf passes later)
