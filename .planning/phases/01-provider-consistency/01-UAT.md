---
status: complete
phase: 01-provider-consistency
source: [".planning/phases/01-provider-consistency/01-01-SUMMARY.md", ".planning/phases/01-provider-consistency/01-02-SUMMARY.md", ".planning/phases/01-provider-consistency/01-03-SUMMARY.md"]
started: 2026-03-30T00:00:00Z
updated: 2026-03-30T00:00:00Z
---

## Current Test

[testing complete]

## Tests

### 1. Cold Start Smoke Test
expected: |
  Kill any running server/service. Clear ephemeral state (temp DBs, caches, lock files). Start the application from scratch using `python3 ui/server.py` or equivalent. Server boots without errors and the UI is accessible at the expected port.
result: pass

### 2. Centralized Settings and Persistence
expected: |
  Save provider credentials (e.g., OpenAI API key) in the Settings UI. Refresh the page or restart the server. The credentials should be preserved and pre-filled in the UI (likely masked as '***').
result: issue
reported: "ok the API is preserved but it doesnt show *** it says, \"Stored (re-enter to change)\" which is fine, but the slected model is not saved and i have the choose the right model again from the dropdown, can we maek that persistant as well ?"
severity: major

### 3. Async Model Discovery
expected: |
  In the Settings UI, select a provider (e.g., OpenAI, Anthropic). The model list should populate without the browser or server freezing. Subsequent switches to the same provider should be near-instant (due to caching).
result: pass

### 4. Error Propagation for Corrupted Settings
expected: |
  Manually corrupt the `data/settings.json` file (e.g., by changing a byte in the encrypted string or making the JSON invalid). Attempt to load the settings page in the UI. A clear 500 error message should be displayed, rather than the system silently failing or resetting to defaults.
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
  artifacts: []  # Filled by diagnosis
  missing: []    # Filled by diagnosis

- truth: "Multiple concurrent runs should be supported by the backend and accessible via the UI."
  status: failed
  reason: "User reported: when i start a extraction and open another tab to start another extraction then the \"Step 1/6 (Run: 20260330-025033-raw-essential-sow)\" box loads and shows me the current extraction process, i am not able to start a new one"
  severity: major
  test: 5
  artifacts: []
  missing: []
