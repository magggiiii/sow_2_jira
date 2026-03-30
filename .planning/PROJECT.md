# SOW-to-Jira Portable Extraction Engine

## What This Is

SOW-to-Jira is a local-first system that converts Statement of Work documents into structured, reviewable Jira work items. It provides a web UI and API for upload, extraction, task review, and Jira push, with pluggable LLM providers through LiteLLM. Current focus is stabilizing provider switching and observability so production debugging is reliable.

## Core Value

Given a complex SOW, the system must reliably produce actionable Jira-ready tasks with transparent run status and logs.

## Requirements

### Validated

- ✓ User can upload SOW PDFs and run extraction sessions via FastAPI UI/API — existing
- ✓ Pipeline orchestrates indexing, extraction, deduplication, and persistence to session artifacts — existing
- ✓ Users can review/edit tasks and push approved tasks to Jira — existing
- ✓ LLM provider settings are persisted with encrypted-at-rest storage in `data/settings.json` — existing
- ✓ Structured logging/telemetry components exist (Loguru + Loki emission path) — existing

### Active

- [ ] Provider switching is deterministic and run-isolated (no stale provider/model bleed)
- [ ] Model discovery and model execution use consistent provider semantics
- [ ] Observability stack (Bifrost, Loki, Tempo, Grafana) is operational in local Docker
- [ ] Terminal output is clean, uniform, and aligned with handoff logging format
- [ ] Docker packaging and compose overlays are production-ready and reproducible

### Out of Scope

- Mobile clients — web and API are sufficient for this milestone
- Multi-tenant auth/SSO — current scope is trusted local or controlled deployment
- Replacing PageIndex extraction strategy — stabilization is prioritized over parser re-architecture

## Context

This is a brownfield Python 3.11 codebase with a central orchestrator and FastAPI UI. The system already supports multiple providers (OpenAI-compatible and native paths), encrypted settings, and Jira integration. Current pain points are provider credential inconsistencies, cross-run configuration leakage, and observability bring-up issues (especially Tempo/Loki/Bifrost/Grafana wiring and signal visibility). Existing `.planning/codebase/` analysis identifies security and reliability risks that should be folded into execution phases.

## Constraints

- **Runtime**: Python 3.11 + FastAPI + LiteLLM — maintain compatibility with current production image and dependencies
- **Deployment**: Docker-first local bring-up — observability must run through compose in a repeatable way
- **Security**: Persisted credentials must remain encrypted at rest — no plaintext fallback
- **Stability**: Pipeline behavior cannot regress for existing extraction and Jira push flows
- **Observability**: Logs and traces must be debuggable from terminal and Grafana/Bifrost in local environment

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Treat this initialization as brownfield and seed validated capabilities from existing implementation | Prevent re-planning already shipped behaviors | ✓ Good |
| Use YOLO + standard granularity for planning defaults | Keeps momentum while preserving phase structure | — Pending |
| Keep research/plan-check/verifier enabled in workflow config | Reduces execution risk for a reliability-heavy milestone | — Pending |
| Prioritize observability and provider reliability before new feature expansion | Current blockers are operational, not feature scarcity | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `$gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `$gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-03-30 after initialization*
