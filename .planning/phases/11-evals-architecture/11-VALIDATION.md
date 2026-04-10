---
phase: 11
slug: evals-architecture
status: draft
nyquist_compliant: true
wave_0_complete: false
created: 2026-04-09
updated: 2026-04-09
---

# Phase 11 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest |
| **Config file** | none — Wave 0 installs |
| **Quick run command** | `pytest tests/test_phase11_evals.py` |
| **Full suite command** | `pytest tests/` |
| **Estimated runtime** | ~20 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/test_phase11_evals.py` (if applicable) or specific task verify.
- **After every plan wave:** Run `pytest tests/`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 20 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 11-01-00 | 01 | 0 | - | setup | `ls tests/test_phase11_evals.py` | ✅ | ⬜ pending |
| 11-01-01 | 01 | 1 | EVAL-03 | infra | `docker compose -f infra/admin/docker-compose.admin.yml exec bifrost curl http://host.docker.internal:11434/api/tags` | ✅ | ⬜ pending |
| 11-01-02 | 01 | 1 | EVAL-03 | infra | `ls infra/admin/evaluator/Dockerfile` | ✅ | ⬜ pending |
| 11-01-03 | 01 | 1 | EVAL-03 | infra | `docker compose -f infra/admin/docker-compose.admin.yml ps evaluator --format json | grep -q "running"` | ✅ | ⬜ pending |
| 11-02-01 | 02 | 1 | EVAL-01 | schema | `python3 -c "from models.eval_schemas import HierarchicalDatasetItem"` | ✅ | ⬜ pending |
| 11-02-02 | 02 | 1 | EVAL-01 | integration | `pytest tests/test_phase11_evals.py::test_dataset_seeding` | ✅ | ⬜ pending |
| 11-02-03 | 02 | 1 | EVAL-01 | integration | `pytest tests/test_phase11_evals.py::test_run_eval_dataset_script` | ✅ | ⬜ pending |
| 11-03-01 | 03 | 2 | EVAL-02 | unit | `pytest tests/test_hierarchical_judge.py` | ✅ | ⬜ pending |
| 11-03-02 | 03 | 2 | EVAL-02 | integration | `pytest tests/test_phase11_evals.py::test_evaluator_execution` | ✅ | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [x] `tests/test_phase11_evals.py` — integration tests for evaluator (Created in 11-01-00)
- [ ] `infra/admin/evaluator/Dockerfile` — stub for evaluator service
- [ ] `infra/admin/evaluator/main.py` — stub for evaluator script

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Isolation | EVAL-03 | Requires monitoring network | Verify `s2j-user-bifrost` logs during evaluation run |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references
- [x] No watch-mode flags
- [x] Feedback latency < 20s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
