---
status: testing
phase: 05-docker-distribution
source:
  - .planning/phases/05-docker-distribution/05-01-SUMMARY.md
started: 2026-04-05T17:02:52Z
updated: 2026-04-05T17:05:48Z
---

## Current Test
<!-- OVERWRITE each test - shows where we are -->

number: 2
name: Installer Bootstrap
expected: |
  Running the documented one-command installer completes without shell errors and creates `~/.sow-to-jira/` workspace with compose/config artifacts.
awaiting: user response

## Tests

### 1. Cold Start Smoke Test
expected: Kill any running services, start from clean workspace state, then run startup command from scratch. Expected: services boot without fatal errors, and `http://localhost:8000` (or health endpoint) responds.
result: pass

### 2. Installer Bootstrap
expected: Running the documented one-command installer completes without shell errors and creates `~/.sow-to-jira/` workspace with compose/config artifacts.
result: pending

### 3. Interactive Credential Wizard
expected: Installer prompts for Jira/LLM credentials and writes a populated `.env` in `~/.sow-to-jira/` with required runtime keys.
result: pending

### 4. `s2j` Command Lifecycle
expected: `s2j up` starts services, `s2j down` stops them, and shell alias remains available in new shell sessions.
result: pending

### 5. Distribution Image/Compose Behavior
expected: Deployment path uses pre-built image flow (not local build) and app container can read env values via compose.
result: pending

## Summary

total: 5
passed: 1
issues: 0
pending: 4
skipped: 0
blocked: 0

## Gaps
