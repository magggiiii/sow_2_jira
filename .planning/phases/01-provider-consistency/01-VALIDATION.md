---
phase: 1
slug: provider-consistency
status: draft
nyquist_compliant: true
wave_0_complete: false
created: 2026-03-30
---

# Phase 1 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x |
| **Config file** | none — test files in repo root |
| **Quick run command** | `python -m pytest test_settings.py test_discovery.py` |
| **Full suite command** | `python -m pytest` |
| **Estimated runtime** | ~10 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest {test_file_if_applicable}` or py_compile check
- **After every plan wave:** Run `python -m pytest test_settings.py test_discovery.py`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 10 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 1-01-00 | 01 | 1 | PROV-02 | check | `grep -E "pytest" requirements.txt` | ✅ | ⬜ pending |
| 1-01-01 | 01 | 1 | PROV-02 | compile | `python -m py_compile config/settings.py` | ❌ W0 | ⬜ pending |
| 1-01-02 | 01 | 1 | PROV-02 | compile | `python -m py_compile config/settings.py` | ❌ W0 | ⬜ pending |
| 1-01-03 | 01 | 1 | PROV-03 | unit | `python -m pytest test_settings.py` | ❌ W0 | ⬜ pending |
| 1-02-01 | 02 | 2 | PROV-01 | compile | `python -m py_compile models/schemas.py` | ✅ | ⬜ pending |
| 1-02-02 | 02 | 2 | PROV-01 | compile | `python -m py_compile pipeline/llm_router.py` | ✅ | ⬜ pending |
| 1-02-03 | 02 | 2 | PROV-01 | compile | `python -m py_compile pipeline/llm_client.py pipeline/orchestrator.py pageindex/utils.py` | ✅ | ⬜ pending |
| 1-03-01 | 03 | 3 | PROV-04 | check | `grep -E "httpx|pytest-asyncio" requirements.txt` | ✅ | ⬜ pending |
| 1-03-02 | 03 | 3 | PROV-04 | compile | `python -m py_compile ui/server.py` | ✅ | ⬜ pending |
| 1-03-03 | 03 | 3 | PROV-04 | unit | `python -m pytest test_discovery.py` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `test_settings.py` — stubs for PROV-02, PROV-03
- [ ] `test_discovery.py` — stubs for PROV-04

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Load UI without re-prompting | PROV-02 | End-to-end integration | Open UI, verify settings from settings.json are loaded. |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references
- [x] No watch-mode flags
- [x] Feedback latency < 10s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
