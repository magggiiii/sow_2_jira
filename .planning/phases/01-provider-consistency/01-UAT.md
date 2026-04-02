---
status: diagnosed
phase: 01-provider-consistency
source: [".planning/phases/01-provider-consistency/01-01-SUMMARY.md", ".planning/phases/01-provider-consistency/01-02-SUMMARY.md", ".planning/phases/01-provider-consistency/01-03-SUMMARY.md"]
started: 2026-03-30T00:00:00Z
updated: 2026-03-30T00:00:00Z
---

## Current Test

[testing complete]

## Tests

### 1. Cold Start Smoke Test
...
result: pass

### 2. Centralized Settings and Persistence
expected: |
  Save provider credentials (e.g., OpenAI API key) in the Settings UI. Refresh the page or restart the server. The credentials should be preserved and pre-filled in the UI (likely masked as '***').
result: issue
reported: "ok the API is preserved but it doesnt show *** it says, \"Stored (re-enter to change)\" which is fine, but the slected model is not saved and i have the choose the right model again from the dropdown, can we maek that persistant as well ?"
severity: major

### 3. Async Model Discovery
...
result: pass

### 4. Error Propagation for Corrupted Settings
...
result: pass

### 5. Concurrent Extraction Isolation
expected: |
  (Note: This is hard to test manually without multiple browser tabs/users, but check observable behavior)
  Initiate an extraction run. While it is running, change the settings or start another run with a different provider. The first run should continue to completion using its original credentials and provider, without being affected by the changes.
result: issue
reported: "when i start a extraction and open another tab to start another extraction then the \"Step 1/6 (Run: 20260330-025033-raw-essential-sow)\" box loads and shows me the current extraction process, i am not able to start a new one"
severity: major

## Summary

total: 5
passed: 3
issues: 2
pending: 0
skipped: 0

## Gaps

- truth: "Save provider credentials (e.g., OpenAI API key) in the Settings UI. Refresh the page or restart the server. The credentials should be preserved and pre-filled in the UI (likely masked as '***')."
  status: failed
  reason: "User reported: ok the API is preserved but it doesnt show *** it says, \"Stored (re-enter to change)\" which is fine, but the slected model is not saved and i have the choose the right model again from the dropdown, can we maek that persistant as well ?"
  severity: major
  test: 2
  root_cause: "In `ui/app.js` -> `updateProviderUI()`, `#providerModelSelect` is cleared before `fetchModels()` completes. `fetchModels()` attempts to restore the value from the already-cleared DOM element instead of the settings cache."
  artifacts:
    - path: "ui/app.js"
      issue: "Model selection cleared before async fetch completes"
  missing:
    - "Update `fetchModels` to restore value from `providerSettingsCache` instead of DOM state."

- truth: "Multiple concurrent runs should be supported by the backend and accessible via the UI."
  status: failed
  reason: "User reported: when i start a extraction and open another tab to start another extraction then the \"Step 1/6 (Run: 20260330-025033-raw-essential-sow)\" box loads and shows me the current extraction process, i am not able to start a new one"
  severity: major
  test: 5
  root_cause: "Frontend uses `localStorage` for `activeSessionId` which is shared across tabs. Backend uses a global constant `TREE_CACHE_PATH` in `orchestrator.py` causing resource contention. `ui/server.py` leaks Loguru handlers."
  artifacts:
    - path: "ui/app.js"
      issue: "Uses localStorage instead of tab-specific sessionStorage for active runs"
    - path: "pipeline/orchestrator.py"
      issue: "Global TREE_CACHE_PATH causes concurrent write collisions"
    - path: "ui/server.py"
      issue: "Fails to remove Loguru handlers after pipeline run completion"
  missing:
    - "Switch `ui/app.js` to `sessionStorage` for active session tracking."
    - "Refactor `pipeline/orchestrator.py` to use run-specific tree cache paths."
    - "Implement explicit Loguru handler cleanup in `ui/server.py`."
