# Phase 5 Context: Docker Distribution & One-Command Installer

## Goal
Enable non-technical users to install and run SOW-to-Jira locally with a single command (`curl | bash`), using pre-built images from `calib.dev`. Ensure all telemetry is automatically routed to the central observability deck.

## Requirements Mapping (New for v1.1 Distribution)
- **DIST-01**: Automated installer (`install.sh`) handles Docker dependency and OS detection.
- **DIST-02**: Distribution-ready `docker-compose.yml` uses pre-built images from `calib.dev`.
- **DIST-03**: Automated `.env` generation with hardcoded observability backbone credentials.
- **DIST-04**: Interactive wizard for user-specific Jira and LLM credentials.
- **DIST-05**: Persistent shell alias (`sjt`) for easy stack management.

## Implementation Decisions (from Design Spec)
- **Image**: `calib.dev/mageswaran/sow-to-jira:v1.0`
- **Workspace**: `~/.sow-to-jira/`
- **Backbone**: `BIFROST_GATEWAY_URL` and `BIFROST_BACKBONE_TOKEN` are pre-set in the installer.
- **Artifacts**: Script will download `docker-compose.yml` and `config/*.yaml` from the GitLab raw URL.

## Current State
- **Milestone v1.0 complete**: Logic is stable and verified.
- **Multi-stage Dockerfile**: Exists and runs as non-root.
- **Compose**: Hardened but currently set to `build: .`.

## Blockers & Concerns
- **Registry Auth**: Ensure the `calib.dev` registry is accessible or the installer handles login if private.
- **Native PDF Libs**: Verify `libmupdf` and `libfreetype` are correctly bundled in the pushed image.
- **OS Variations**: MacOS `zsh` vs. Linux `bash` profile syntax for alias creation.

## Success Criteria
1. `curl | bash` installer completes without error on macOS and Ubuntu.
2. `sjt` command starts the entire 5-service stack.
3. User instances automatically show up in central Grafana logs.
