# Phase 4 Context: Docker Production Readiness

## Goal
Harden the Docker deployment environment to ensure reproducible, healthy, and secure local/shared operations. Finalize multi-container orchestration, non-root execution, and data persistence.

## Requirements Mapping
- **DEP-01**: `docker-compose.yml` boots core app stack successfully with healthy services.
- **DEP-02**: `docker-compose.ollama.yml` overlay supports local Ollama testing without modifying core compose.
- **DEP-03**: Tempo service starts with a valid config compatible with selected image version.
- **DEP-04**: Docker image runs as non-root and persists data in mounted volume.

## Current State & Recent Changes
- **Multi-stage Dockerfile**: Already exists, needs non-root verification and potential native dependency cleanup.
- **Compose Fragmentation**: Currently multiple files (`docker-compose.yml`, `docker-compose.bifrost.yml`, `docker-compose.ollama.yml`).
- **Observability Integration**: Phase 3 aligned endpoints via environment variables, but `docker-compose.yml` lacks the full observability stack definitions (Loki, Tempo, Grafana).
- **Hardcoded Paths**: Some paths might still depend on local host assumptions (e.g. `~/.sow_to_jira/data` vs `/app/data`).

## Blockers & Concerns
- **Tempo Config**: Need a verified `tempo.yaml` that doesn't crash on startup (linked to Requirement DEP-03).
- **Network Resolution**: Ensure `bifrost`, `app`, and `loki` can communicate across compose files if not consolidated.
- **Volume Permissions**: Non-root user (UID 1000) needs write access to mapped volumes on the host.

## Success Criteria
1. `docker compose up` brings up the entire extraction + observability engine.
2. Healthchecks for all services (app, bifrost, loki) return healthy.
3. Application data (logs, settings, extractions) survives container destruction and restart.
4. Images pass basic security scan (non-root execution).
