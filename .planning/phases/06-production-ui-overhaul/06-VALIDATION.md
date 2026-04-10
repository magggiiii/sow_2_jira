---
phase: 06
slug: production-ui-overhaul
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-01
---

# Phase 06 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | None — Vanilla JS UI phase |
| **Config file** | none |
| **Quick run command** | `open ui/index.html` (Manual check) |
| **Full suite command** | `open ui/index.html` (Manual check) |
| **Estimated runtime** | ~60 seconds |

---

## Sampling Rate

- **After every task commit:** Run `open ui/index.html` (Manual check)
- **After every plan wave:** Run `open ui/index.html` (Manual check)
- **Before `/gsd:verify-work`:** Full UI manual check
- **Max feedback latency:** 60 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 06-01-01 | 01 | 1 | REQ-UI-01 | manual | `none` | ✅ | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

*If none: "Existing infrastructure covers all phase requirements."*
Existing infrastructure covers all phase requirements.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| UI components look professional | REQ-UI-01 | UI visual changes | Open index.html and verify visually |
| All existing features work | REQ-UI-02 | Verifying old app.js integration | Test Jira push and upload in UI |

*If none: "All phase behaviors have automated verification."*

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 60s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** {pending / approved YYYY-MM-DD}
