# Roadmap: SOW-to-Jira Portable Extraction Engine

## Overview

This roadmap stabilizes the existing brownfield system in four execution phases: provider correctness, pipeline/logging reliability, observability stack bring-up, and deployment hardening. Each phase maps to explicit v1 requirements and yields operator-visible improvements.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 1: Provider Consistency** - Fix provider/model routing and credential persistence behavior (Completed)
- [ ] **Phase 2: Runtime Logging Reliability** - Standardize run-time logs, cancellation behavior, and PageIndex logger safety
- [ ] **Phase 3: Observability Bring-Up** - Make Loki/Tempo/Grafana/Bifrost telemetry path operational end-to-end
- [ ] **Phase 4: Docker Production Readiness** - Harden compose/images and finalize reproducible local deployment

## Phase Details

### Phase 1: Provider Consistency
**Goal**: Provider switching, model execution, and persisted credentials behave deterministically across runs.
**Depends on**: Nothing (first phase)
**Requirements**: [PROV-01, PROV-02, PROV-03, PROV-04]
**Success Criteria** (what must be TRUE):
  1. User switches provider and next run uses selected provider/model without stale carryover.
  2. Inference calls do not fail with provider-prefix or wrong-provider key errors when settings are valid.
  3. Restarting the server preserves working provider credentials and model execution without manual re-entry.
**Plans**: 5 plans

Plans:
- [x] 01-01-PLAN.md — Centralize settings logic and model formatting
- [x] 01-02-PLAN.md — Enforce per-run immutable LLM config snapshot in orchestration path
- [x] 01-03-PLAN.md — Refactor UI endpoints to use centralized SettingsManager and implement async model discovery
- [x] 01-04-PLAN.md — Gap Closure: UI Persistence & Isolation
- [x] 01-05-PLAN.md — Gap Closure: Backend Pipeline Concurrency

### Phase 2: Runtime Logging Reliability
**Goal**: Pipeline output is clean, consistent, and resilient under retries/cancel events.
**Depends on**: Phase 1
**Requirements**: [PIPE-01, PIPE-02, PIPE-03]
**Success Criteria** (what must be TRUE):
  1. Every run prints exactly one active provider/model/base summary before step execution.
  2. Cancel action stops ongoing retry loops and no orphaned error spam continues after cancellation.
  3. PageIndex logger calls never crash the run when optional logger references are absent.
**Plans**: 3 plans

Plans:
- [ ] 02-01: Refactor log emission points to one canonical terminal formatter
- [ ] 02-02: Wire cancellation and retry control through all LLM call sites
- [ ] 02-03: Patch PageIndex logger plumbing and add regression checks

### Phase 3: Observability Bring-Up
**Goal**: Logs, telemetry, and traces are visible in local observability stack.
**Depends on**: Phase 2
**Requirements**: [OBS-01, OBS-02, OBS-03, OBS-04]
**Success Criteria** (what must be TRUE):
  1. Application logs appear in Loki and are queryable from Grafana.
  2. Tempo starts with valid config and traces for API/pipeline requests are visible.
  3. Structured telemetry events (`run.started`, `step.completed`, `llm.call`, `run.completed`) are emitted with required fields.
**Plans**: 4 plans

Plans:
- [ ] 03-01: Validate and fix compose networking/endpoints for Bifrost, Loki, Tempo, Grafana
- [ ] 03-02: Patch telemetry emitter configuration/auth defaults and endpoint resolution
- [ ] 03-03: Instrument trace spans and verify end-to-end visibility in Grafana
- [ ] 03-04: Add observability smoke checks and operator troubleshooting doc

### Phase 4: Docker Production Readiness
**Goal**: Core and overlay compose flows are reproducible, healthy, and aligned with runtime requirements.
**Depends on**: Phase 3
**Requirements**: [DEP-01, DEP-02, DEP-03, DEP-04]
**Success Criteria** (what must be TRUE):
  1. `docker compose up` brings up app + observability stack with passing health states.
  2. Ollama overlay can be enabled for local testing without breaking core services.
  3. Runtime container runs as non-root and data persists correctly across restarts.
**Plans**: 3 plans

Plans:
- [ ] 04-01: Finalize Dockerfile runtime user, dependencies, and healthcheck behavior
- [ ] 04-02: Harden compose files (core + ollama) with pinned/compatible service configs
- [ ] 04-03: Create deployment validation checklist and runbook

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Provider Consistency | 5/5 | Completed | 2026-03-31 |
| 2. Runtime Logging Reliability | 0/3 | Not started | - |
| 3. Observability Bring-Up | 0/4 | Not started | - |
| 4. Docker Production Readiness | 0/3 | Not started | - |
