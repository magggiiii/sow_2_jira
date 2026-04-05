# Codex Handoff: v1.1.7 Runtime Stability Fixes

**Release:** `v1.1.7`  
**Commit:** `52fca09`  
**Date:** 2026-04-05  
**Scope:** User-stack startup reliability and health stabilization for `s2j`.

---

## Incident Summary

During user-stack startup on `v1.1.6`, `s2j-user-app` entered a restart loop with:

- `SyntaxError: invalid syntax (page_index.py, line 362)`
- Gunicorn worker boot failures (`Worker failed to boot`)

In the same session:

- `s2j-user-argus-collector` repeatedly restarted
- `s2j-user-bifrost` remained `unhealthy`

---

## Root Causes

1. **App image regression (critical)**
- Published image `ghcr.io/magggiiii/sow_2_jira:v1.1.6` contained malformed Python in `/app/pageindex/page_index.py`.

2. **Bifrost healthcheck mismatch**
- Healthcheck used `wget --spider` (HEAD request), but Bifrost `/health` behavior returned method-not-allowed for HEAD in this environment, causing false `unhealthy`.

3. **Argus collector config incompatibilities**
- `ARGUS_HQ_URL` format used URL scheme where exporter expected `host:port`
- `file_storage` directory did not auto-create
- `attributes` processor action `replace` was unsupported in current collector runtime

---

## Permanent Fixes Shipped

All items below were shipped in commit `52fca09` and published as image `v1.1.7`.

### 1) App boot crash fix
- Source corrected in `pageindex/page_index.py` and published in new image.

### 2) Bifrost health fix
- Updated user compose healthcheck from HEAD-style probe to GET probe:
- File: `infra/user/docker-compose.user.yml`
- Change: `wget --quiet --tries=1 -O /dev/null http://127.0.0.1:8080/health || exit 1`

### 3) Argus collector compatibility fix
- File: `config/user/argus-collector-edge.yaml`
- Added `file_storage.create_directory: true`
- Removed unsupported `attributes/redaction` `replace` processor usage
- Kept OTLP exporter endpoint aligned with `host:port` expectation

### 4) Installer upgrade-path hardening
- File: `scripts/install/install.sh`
- Normalizes `ARGUS_HQ_URL` to `host:port`
- Ensures `~/.sow_to_jira/data/argus_storage` exists
- Upserts critical env vars on reinstall/update:
  - `S2J_VERSION`
  - `DOCKER_HOST_INTERNAL`
  - `ARGUS_HQ_URL`

### 5) Release metadata
- `VERSION` bumped to `v1.1.7`
- `CHANGELOG.md` updated with incident fixes

---

## Verification Evidence

Post-release local validation after switching to `v1.1.7`:

- `s2j-user-app`: `healthy`
- `s2j-user-bifrost`: `healthy`
- `s2j-user-argus-collector`: `Up` (stable)

Image publish confirmation:

- `ghcr.io/magggiiii/sow_2_jira:v1.1.7`
- digest: `sha256:3b57a3af122542f6740b74c6681d60a6ef13b31b7b3d9e26a3935b5b03fb2f77`

---

## Upgrade Runbook

1. Re-run installer (recommended):
- `bash scripts/install/install.sh`

2. Or manually update existing user stack:
- set `S2J_VERSION=v1.1.7` in `~/.sow_to_jira/.env`
- `cd ~/.sow_to_jira`
- `docker compose -f docker-compose.user.yml pull app`
- `docker compose -f docker-compose.user.yml up -d --force-recreate app`

3. Confirm health:
- `docker ps -a --format 'table {{.Names}}\t{{.Status}}'`

---

## Rollback

If emergency rollback is needed:

1. Set `S2J_VERSION` back to previous known-good tag in `~/.sow_to_jira/.env`
2. Recreate stack:
- `docker compose -f ~/.sow_to_jira/docker-compose.user.yml up -d --force-recreate`

Note: rolling back to `v1.1.6` reintroduces the app startup crash.

---

## Notes for Next Patch

- Avoid floating `otel/opentelemetry-collector-contrib:latest` in production paths; pin tested tag.
- Add container smoke test in release gate:
  - app import/startup
  - bifrost healthcheck command validation
  - argus collector config lint/start check
