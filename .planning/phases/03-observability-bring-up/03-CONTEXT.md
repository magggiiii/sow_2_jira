# Phase 3 Context: Observability Bring-Up

## Goal
Make the Loki/Tempo/Grafana/Bifrost telemetry path operational end-to-end within the Docker environment. Ensure logs, traces, and structured telemetry are correctly routed and visible in Grafana.

## Requirements Mapping
- **OBS-01**: Application logs are shipped to Loki through configured endpoint/auth without connection errors.
- **OBS-02**: Trace data is emitted and visible through Tempo/Grafana for pipeline and API flows.
- **OBS-03**: Telemetry events include `run.started`, `step.completed`, `llm.call`, and `run.completed` with structured fields.
- **OBS-04**: Terminal output uses consistent symbols and concise status lines (Partially addressed in Phase 2, needs final validation).

## Current State & Recent Changes (Phase 2)
- **Consolidated Endpoints**: Telemetry and logs now point to the Bifrost Gateway (`localhost:8080`).
- **Standardized Labels**: Loki job name standardized to `sow-to-jira` with `event` labels to distinguish logs from telemetry.
- **Structured Events**: `TelemetryEmitter` refactored to support unified Loki/Bifrost pattern.
- **Trace Instrumentation**: Basic trace spans and `run_id` propagation implemented in `orchestrator.py` and `llm_client.py`.
- **Cancellation**: `stop_event` propagation ensuring clean state transitions.

## Blockers & Concerns
- **Service Stability**: Local observability stack (Tempo/Loki) previously reported as unstable or empty.
- **Networking**: Docker Compose networking between the app and observability services needs validation.
- **Configuration**: Tempo config compatibility with the selected image version needs verification (Requirement **DEP-03**).

## Success Criteria
1. Application logs appear in Loki and are queryable from Grafana.
2. Tempo starts with valid config and traces for API/pipeline requests are visible.
3. Structured telemetry events are correctly formatted and reachable in Grafana.
