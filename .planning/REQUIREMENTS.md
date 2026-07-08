# Requirements: SOW-to-Jira Portable Extraction Engine

**Defined:** 2026-03-30
**Core Value:** Given a complex SOW, the system must reliably produce actionable Jira-ready tasks with transparent run status and logs.

## v1 Requirements

### Provider Routing

- [x] **PROV-01**: User can switch LLM provider in settings and have the new provider applied to subsequent runs without server restart.
- [x] **PROV-02**: System uses provider-correct model identifiers for inference calls.
- [x] **PROV-03**: Stored credentials are loaded consistently after restart.
- [x] **PROV-04**: Model list refresh clears stale options when provider changes.

### Pipeline Runtime

- [x] **PIPE-01**: Each pipeline run logs active provider/model/base exactly once at run start.
- [x] **PIPE-02**: Cancellation cleanly stops retries/background processing.
- [x] **PIPE-03**: PageIndex logging path never crashes when logger callbacks are missing.

### Observability

- [x] **OBS-01**: Application logs are shipped to Loki through configured endpoint/auth.
- [x] **OBS-02**: Trace data is emitted and visible through Tempo/Grafana.
- [x] **OBS-03**: Telemetry events include `run.started`, `step.completed`, `llm.call`, and `run.completed`.
- [x] **OBS-04**: Terminal output uses consistent symbols and concise status lines.

### Deployment

- [x] **DEP-01**: `docker-compose.yml` boots core app stack successfully with healthy services.
- [x] **DEP-02**: `docker-compose.ollama.yml` overlay supports local Ollama testing.
- [x] **DEP-03**: Tempo service starts with a valid config.
- [x] **DEP-04**: Docker image runs as non-root and persists data in mounted volume.

### Distribution
- [ ] **DIST-01**: Automated installer (`install.sh`) handles Docker dependency and OS detection.
- [ ] **DIST-02**: Distribution-ready `docker-compose.yml` uses pre-built images from `calib.dev`.
- [ ] **DIST-03**: Automated `.env` generation with hardcoded observability backbone credentials.
- [ ] **DIST-04**: Interactive wizard for user-specific Jira and LLM credentials.
- [ ] **DIST-05**: Persistent shell alias (`sjt`) for easy stack management.

### Production UI
- [ ] **UI-01**: Implement a modern, production-grade layout with cohesive typography and spacing.
- [ ] **UI-02**: Improve empty states and visual feedback mechanisms (toast notifications, spinners).
- [ ] **UI-03**: Add dark mode toggle and responsive design layout.

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| PROV-01 | Phase 1 | Completed |
| PROV-02 | Phase 1 | Completed |
| PROV-03 | Phase 1 | Completed |
| PROV-04 | Phase 1 | Completed |
| PIPE-01 | Phase 2 | Completed |
| PIPE-02 | Phase 2 | Completed |
| PIPE-03 | Phase 2 | Completed |
| OBS-01 | Phase 3 | Completed |
| OBS-02 | Phase 3 | Completed |
| OBS-03 | Phase 3 | Completed |
| OBS-04 | Phase 3 | Completed |
| DEP-01 | Phase 4 | Completed |
| DEP-02 | Phase 4 | Completed |
| DEP-03 | Phase 4 | Completed |
| DEP-04 | Phase 4 | Completed |
| INT-01 | Phase 12 | Pending |
| INT-02 | Phase 12 | Pending |
| INT-03 | Phase 12 | Pending |
| INT-04 | Phase 12 | Pending |
| INT-05 | Phase 12 | Pending |
| INT-06 | Phase 12 | Pending |
| INT-07 | Phase 12 | Pending |

**Coverage:**
- v1 requirements: 15 total
- Completed: 15 / 15 ✅
- v1.1 (corrective) requirements: 7 added (Phase 12)
- Unmapped: 0 ✓

---
## v1.1 — Intelligence Layer Tightening (Phase 12)

Added 2026-05-13 in response to the first end-to-end validation run of the Wave 1-3 intelligence layer (baseline run `20260512-194433-test-sow`).

### Output Quality

- [ ] **INT-01**: `INCOMPLETE` flag rate on a fresh end-to-end run is below 5% of tickets (baseline: 100%).
- [ ] **INT-02**: Dedup yields at least 20 merges on the same SOW the baseline ran on (baseline: 0 merges).
- [ ] **INT-03**: Extraction JSON failure rate is below 2% of nodes (baseline: 29%).
- [ ] **INT-04**: `LOW_CONFIDENCE` flag rate is below 10% of tickets, and every surviving flag is traceable to a non-zero critic confidence in the audit log (baseline: 29% with conf=0.00).

### Observability & Reviewer Signal

- [ ] **INT-05**: `coverage_reports.json` is filtered run-wide against the final dedup'd task corpus and tiered (`drop` ≥0.85, `likely_overlap` 0.70-0.85, `uncovered` <0.70) with no cross-section duplicates.
- [ ] **INT-06**: Full pytest suite remains green (94+ tests, no regression).
- [ ] **INT-07**: A `BEFORE_AFTER.md` artifact exists in the phase directory comparing the rerun vs. baseline `20260512-194433-test-sow` across INT-01..05.

---
*Requirements verified: 2026-03-31*
*v1.1 requirements added: 2026-05-13*
