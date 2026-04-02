# Phase 3 Plan: Flexible Observability Bring-Up

## Objective
Configure the observability stack to support both local development and shared deployment by externalizing all endpoints and tokens. Ensure the system can point back to a central observability deck regardless of where it is running.

## Key Files & Context
- `pipeline/observability.py`: Endpoint resolution prioritized via env vars.
- `pipeline/telemetry.py`: Aligned with dynamic endpoint resolution and token removal.
- `.env.example`: Updated with comprehensive observability variables.
- `docker-compose.yml`: Environment propagation for `BIFROST_GATEWAY_URL`.

## Implementation Steps

### Step 3.1: Externalize Endpoint Configuration
- [x] **Task 3.1.1: Refactor `observability.py` Endpoint Resolution**
  - Replaced hardcoded defaults with `BIFROST_GATEWAY_URL` and `BIFROST_BACKBONE_TOKEN`.
  - Implemented `resolve_observability_endpoint()` helper with Docker hostname mapping.
- [x] **Task 3.1.2: Align `TelemetryEmitter`**
  - Refactored `TelemetryEmitter` to use central resolution and removed redundant obfuscation logic.

### Step 3.2: Robust Telemetry Sync & Persistence
- [x] **Task 3.2.1: Hardened `OfflineBufferSpanExporter`**
  - Ensured local log queuing works reliably when the remote gateway is unreachable.
- [x] **Task 3.2.2: Automated `sync_telemetry` trigger**
  - Integrated `sync_telemetry()` calls at the start and end of `PipelineOrchestrator.run()`.

### Step 3.3: Trace Instrumentation Completion
- [x] **Task 3.3.1: PageIndex Instrumentation**
  - Added trace spans and `run_id` propagation to main PageIndex entry points.

### Step 3.4: Configuration & Validation
- [x] **Task 3.4.1: Update `.env.example`**
  - Added documentation for new variables.
- [x] **Task 3.4.2: Docker Environment Pass-through**
  - Verified and updated `docker-compose.yml` environment section.
- [x] **Task 3.4.3: Smoke Test for Remote Deck**
  - Created `scripts/verify-telemetry.py` for configuration verification.

## Verification & Testing
- **Syntax Check**: All modified files passed `py_compile`.
- **Logic Check**: Verified hostname translation for Docker environments.
- **Buffer Check**: Verified `sync_telemetry` is integrated into the main orchestration flow.
