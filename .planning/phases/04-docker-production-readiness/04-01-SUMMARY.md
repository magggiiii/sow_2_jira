# Phase 4 Plan: Docker Production Readiness

## Objective
Harden the Docker deployment environment to ensure a secure, reproducible, and fully observable production-ready stack.

## Key Files & Context
- `docker-compose.yml`: Consolidated all 5 core services (app, bifrost, loki, tempo, grafana) with healthchecks and dependencies.
- `Dockerfile`: Optimized multi-stage build with non-root user and API healthcheck.
- `config/tempo.yaml`: Fulfills DEP-03 with a stable OTLP-compatible configuration.
- `scripts/prod-check.sh`: New validation script passing all 5 production criteria.

## Implementation Steps

### Step 4.1: Consolidated & Hardened Compose Stack
- [x] **Task 4.1.1: Consolidate Services**
  - Merged `bifrost` into `docker-compose.yml`.
  - Added `loki`, `tempo`, and `grafana` services to the main stack.
- [x] **Task 4.1.2: Implement Healthchecks & Dependencies**
  - Added robust healthchecks to all core infrastructure services.
  - Used `condition: service_healthy` to ensure proper startup sequencing.
- [x] **Task 4.1.3: Network Isolation**
  - Implemented `sow-internal` network for service isolation.

### Step 4.2: Dockerfile & Security Hardening (DEP-04)
- [x] **Task 4.2.1: Optimize Multi-stage Build**
  - Verified slim runtime stage with compiled native dependencies.
- [x] **Task 4.2.2: Non-Root Verification**
  - Confirmed `USER sow` execution and proper data directory ownership.
- [x] **Task 4.2.3: API Healthcheck**
  - Integrated `HEALTHCHECK` into the runtime Docker image.

### Step 4.3: Observability Configuration (DEP-03)
- [x] **Task 4.3.1: Create Tempo Config**
  - Created `config/tempo.yaml` with verified receivers and local storage.
- [x] **Task 4.3.2: Create Loki Config**
  - Verified `loki` service uses the correct local config path.
- [x] **Task 4.3.3: Grafana Pre-configuration**
  - Set up anonymous admin role for easy initial local access.

### Step 4.4: Deployment Validation
- [x] **Task 4.4.1: Production Check Script**
  - Developed and successfully ran `scripts/prod-check.sh`.
- [x] **Task 4.4.2: Finalize `docker-compose.ollama.yml` (DEP-02)**
  - Updated Ollama overlay to connect to the external `sow-to-jira-network`.

## Verification & Testing
- **Audit Script**: `bash scripts/prod-check.sh` returned **PASSED** for all criteria.
- **Security Check**: Verified non-root user `sow` is active in the Dockerfile.
- **Infrastructure Check**: Verified 5/5 services defined in the primary compose file.
