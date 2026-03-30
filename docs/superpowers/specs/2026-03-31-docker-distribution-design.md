# Design Spec: Docker Distribution & One-Command Installer

**Date:** 2026-03-31
**Topic:** SOW-to-Jira Portability & Sharing
**Status:** Draft

## 1. Objective
Enable non-technical users to install and run the complete SOW-to-Jira software locally with a single command, while ensuring all telemetry and logs are automatically routed to a central developer-managed observability deck.

## 2. Architecture

### 2.1 Component Distribution
- **Application Image**: Hosted on `calib.dev/mageswaran/sow-to-jira:v1.0`. Contains all source code, dependencies, and the FastAPI runtime.
- **Infrastructure Services**: Standard images for Bifrost, Loki, Tempo, and Grafana (pulled from official registries).
- **Configuration Bundle**: `docker-compose.yml` and `config/*.yaml` files hosted as raw artifacts on the project's GitLab repository.

### 2.2 The One-Command Installer (`install.sh`)
The installer performs the following sequence:
1. **OS Detection**: Identifies macOS vs. Ubuntu/Debian.
2. **Dependency Management**: 
   - Checks for Docker. If missing on macOS, installs Homebrew and then Docker Desktop/OrbStack.
   - On Linux, executes the official Docker convenience script.
3. **Workspace Provisioning**: Creates `~/.sow-to-jira/` directory for persistence.
4. **Environment Generation**:
   - **Automated Injection**: Writes `BIFROST_GATEWAY_URL` and `BIFROST_BACKBONE_TOKEN` (developer's central deck) to `.env`.
   - **User Prompting**: Asks for `JIRA_SERVER`, `JIRA_EMAIL`, and `JIRA_API_TOKEN`.
5. **Artifact Pull**: Downloads the latest `docker-compose.yml` and observability configurations into the workspace.
6. **Alias Creation**: Adds `alias sjt='docker compose -f ~/.sow-to-jira/docker-compose.yml up -d'` to the user's shell profile.
7. **Cold Boot**: Runs `docker compose up -d` to launch the full stack.

## 3. Implementation Details

### 3.1 Docker Compose Alignment
The distribution-ready `docker-compose.yml` will replace `build: .` with `image: calib.dev/mageswaran/sow-to-jira:v1.0`.

### 3.2 Security & Isolation
- The `BIFROST_BACKBONE_TOKEN` used in the installer must be a "Write-Only" (Append) token to prevent users from querying logs from other instances.
- User-specific data (PDFs, local settings) is mapped to local volumes to ensure persistence across container updates.

## 4. Success Criteria
- [ ] Users can run `curl | bash` and reach the dashboard at `http://localhost:8000` without manual config.
- [ ] Logs from remote user instances appear in the developer's central Grafana instance.
- [ ] The `sjt` command works as a persistent shortcut for daily use.
