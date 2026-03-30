# Phase 2 Plan: Magi-Optics Telemetry & Logging Completion

## Objective
Fix broken observability integrations (Loki/Bifrost) and complete the missing high-fidelity terminal UI components as per the "Magi-Optics Telemetry Overhaul" spec, without duplicating already implemented work.

## Key Files & Context
- `pipeline/telemetry.py`: `TelemetryEmitter` refactored to use unified endpoints.
- `pipeline/observability.py`: Centralized config, standardized Loki labels, added `run_logger` context manager.
- `pipeline/orchestrator.py`: Implemented `_print_run_summary` and high-fidelity extraction progress bar.
- `pipeline/llm_client.py`: Integrated `stop_event` for graceful cancellation.
- `pageindex/page_index.py`: Propagated `stop_event` through all tree building/parsing functions.
- `ui/server.py`: Refactored to use `run_logger` for reliable cleanup.

## Implementation Steps

### Step 2.1: Fix Broken Observability (Empty Dashboards)
- [x] **Task 2.1.1: Align Telemetry Endpoints**
  - Refactored `pipeline/telemetry.py` to use `SYSTEM_LOKI_URL` and `SYSTEM_BIFROST_TOKEN` from `observability.py`.
- [x] **Task 2.1.2: Standardize Event Payloads**
  - Standardized Loki job name to `sow-to-jira` and added `event` labels to distinguish standard logs from telemetry events.

### Step 2.2: Complete High-Fidelity Terminal UI
- [x] **Task 2.2.1: Implement Extraction Progress Bar**
  - Integrated `rich.progress.Progress` into `PipelineOrchestrator.run()` for the task extraction loop.
- [x] **Task 2.2.2: Consolidated Run Summary (PIPE-01)**
  - Implemented `PipelineOrchestrator._print_run_summary()` using `rich.panel`.
  - Cleaned up redundant `logger.info` calls in `main.py`.
- [x] **Task 2.2.3: Animated Spinners for PageIndex**
  - Integrated `console.status()` for animated spinners during PageIndex tree building.

### Step 2.3: Reliable Cancellation & Logger Safety
- [x] **Task 2.3.1: Global stop_event Integration (PIPE-02)**
  - Hard-wired `stop_event` check in `LLMClient` and all `PageIndex` processing loops.
- [x] **Task 2.3.2: PageIndex Logger & Stop Safety (PIPE-03)**
  - Verified and patched `pageindex/` modules for safe logger usage and stop event propagation.
- [x] **Task 2.3.3: Automated Handler Cleanup**
  - Implemented `run_logger` context manager in `observability.py` and refactored `ui/server.py` to use it.

## Verification & Testing
- **Observability:** Verify logs/events in Grafana (requires running Bifrost/Loki).
- **Terminal UI:** Visually confirmed syntax and structure.
- **Cancellation:** Verified `stop_event` propagation through codebase.
- **Safety:** Syntax check passed for all modified files.
