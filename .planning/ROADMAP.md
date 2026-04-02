# Roadmap: SOW-to-Jira Portable Extraction Engine

## Overview

This roadmap stabilizes the existing brownfield system in four execution phases: provider correctness, pipeline/logging reliability, observability stack bring-up, and deployment hardening. Each phase maps to explicit v1 requirements and yields operator-visible improvements.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 1: Provider Consistency** - Fix provider/model routing and credential persistence behavior (Completed)
- [x] **Phase 2: Runtime Logging Reliability** - Standardize run-time logs, cancellation behavior, and PageIndex logger safety (Completed)
- [x] **Phase 3: Observability Bring-Up** - Make Loki/Tempo/Grafana/Bifrost telemetry path operational end-to-end (Completed)
- [x] **Phase 4: Docker Production Readiness** - Harden compose/images and finalize reproducible local deployment (Completed)
- [x] **Phase 5: Docker Distribution** - Distribute application via one-command curl installer and pre-built images. (Completed)
- [x] **Phase 6: Production UI Overhaul** - Redesign the UI to a professional, production-grade standard. (Completed)
- [x] **Phase 7: Argus Global Observability Overhaul** - Redesign observability stack for reliable fleet-wide tracing and metrics. (Completed)

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
**Plans**: 1 consolidated plan

Plans:
- [x] 02-01-PLAN.md — Magi-Optics Telemetry & Logging Overhaul (Consolidated Step 2.1, 2.2, 2.3)

### Phase 3: Observability Bring-Up
**Goal**: Logs, telemetry, and traces are visible in local observability stack.
**Depends on**: Phase 2
**Requirements**: [OBS-01, OBS-02, OBS-03, OBS-04]
**Success Criteria** (what must be TRUE):
  1. Application logs appear in Loki and are queryable from Grafana.
  2. Tempo starts with valid config and traces for API/pipeline requests are visible.
  3. Structured telemetry events (`run.started`, `step.completed`, `llm.call`, `run.completed`) are emitted with required fields.
**Plans**: 1 consolidated plan

Plans:
- [x] 03-01-PLAN.md — Flexible Observability Bring-Up (Externalized endpoints and auth)

### Phase 4: Docker Production Readiness
**Goal**: Core and overlay compose flows are reproducible, healthy, and aligned with runtime requirements.
**Depends on**: Phase 3
**Requirements**: [DEP-01, DEP-02, DEP-03, DEP-04]
**Success Criteria** (what must be TRUE):
  1. `docker compose up` brings up app + observability stack with passing health states.
  2. Ollama overlay can be enabled for local testing without breaking core services.
  3. Runtime container runs as non-root and data persists correctly across restarts.
**Plans**: 1 consolidated plan

Plans:
- [x] 04-01-PLAN.md — Docker Production Hardening (Consolidated services and security)

### Phase 5: Docker Distribution
**Goal**: Automate distribution to end-users via pre-built images and a single-command installer script.
**Depends on**: Phase 4
**Requirements**: [DIST-01, DIST-02, DIST-03, DIST-04, DIST-05]
**Success Criteria** (what must be TRUE):
  1. `curl | bash` installer completes without error.
  2. Users run instances that connect to the central observability deck.

Plans:
- [x] 05-01-PLAN.md — Build image, create install.sh

### Phase 6: Production UI Overhaul
**Goal**: Redesign the existing functional UI into a professional, high-quality production application.
**Depends on**: None
**Requirements**: [UI-01, UI-02, UI-03]
**Success Criteria** (what must be TRUE):
  1. The UI looks professional and uses modern design system components.
  2. All existing features (provider selection, upload, task review, Jira push) remain fully functional.

Plans:
- [x] 06-01-PLAN.md — Complete UI overhaul

### Phase 7: Argus Global Observability Overhaul
**Goal**: Redesign observability stack for reliable fleet-wide tracing and metrics using a Store-and-Forward architecture.
**Depends on**: Phase 5
**Requirements**: [ARG-01, ARG-02, ARG-03, ARG-04]
**Success Criteria** (what must be TRUE):
  1. OTel Collector buffers data locally when HQ is offline.
  2. Fleet metrics (cost, token usage) are visible in HQ Grafana per unique Instance ID.
  3. Langfuse captures high-fidelity AI traces via OTLP.

Plans:
- [x] 07-01-ARGUS-OVERHAUL.md — Implementation of Edge/HQ collectors and unified OTel stack.

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6 → 7

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Provider Consistency | 5/5 | Completed | 2026-03-31 |
| 2. Runtime Logging Reliability | 1/1 | Completed | 2026-03-31 |
| 3. Observability Bring-Up | 1/1 | Completed | 2026-03-31 |
| 4. Docker Production Readiness | 1/1 | Completed | 2026-03-31 |
| 5. Docker Distribution | 1/1 | Completed | 2026-04-01 |
| 6. Production UI Overhaul | 1/1 | Completed | 2026-04-01 |
| 7. Argus Overhaul | 1/1 | Completed | 2026-04-02 |
