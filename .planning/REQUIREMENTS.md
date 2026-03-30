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

**Coverage:**
- v1 requirements: 15 total
- Completed: 15 / 15 ✅
- Unmapped: 0 ✓

---
*Requirements verified: 2026-03-31*
