# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.14] - 2026-04-10

### Added
- **Evals Architecture**: Context and setup for Langfuse dataset seed scripts (`scripts/seed_langfuse_dataset.py`, `scripts/seed_full_langfuse_dataset.py`) and admin evaluator container planning.

### Fixed
- **PageIndex JSON Parsing**: Resolved `AttributeError: 'dict' object has no attribute 'extend'` in `pageindex/page_index.py` during LLM parsing fallbacks when processing the table of contents.

## [1.1.7] - 2026-04-05

### Fixed
- **App boot crash**: Corrected packaged PageIndex source so startup no longer fails with `SyntaxError` in `page_index.py`.
- **Bifrost healthcheck**: Switched user-stack health probe from `wget --spider` (HEAD) to a GET request so `/health` no longer reports false-unhealthy.
- **Argus Collector startup**:
  - enabled `file_storage.create_directory` so spool path is created automatically
  - removed unsupported `attributes` processor `replace` action from default config
  - standardized `ARGUS_HQ_URL` format to `host:port` for OTLP gRPC exporter compatibility
- **Installer upgrades**: `install.sh` now upserts `S2J_VERSION`, `DOCKER_HOST_INTERNAL`, and `ARGUS_HQ_URL` in existing `~/.sow_to_jira/.env` so reruns pick up new releases correctly.

## [1.1.0] - 2026-04-01

### Added
- **Unified One-Command Installer**: Consolidated `install.sh` for macOS and Linux with automated OS detection and a new interactive credential wizard.
- **Branding & UX**: Replaced the `sjt` shortcut with `s2j` (SOW-to-Jira) and added a new banner and ASCII logo to the installer.
- **Magi-Optics Observability**: Integrated full-stack tracing and logging via Grafana, Loki, and Tempo; pre-wired all distributed instances for central telemetry sync.
- **Production UI Overhaul**: Redesigned the entire web dashboard for a professional, high-quality production experience using modern design system components.

### Changed
- **Provider Consistency**: Centralized all settings logic into a unified `SettingsManager` with immutable per-run LLM configuration.
- **Docker Distribution**: Switched to pre-built images from `calib.dev` for faster, more reliable deployments.
- **Credential Security**: Hardened Fernet encryption for at-rest storage of API keys and moved all sensitive data to isolated volumes.

### Fixed
- **Runtime Logging**: Standardized logs across the PageIndex and LLM pipeline for clean, actionable output.
- **Docker Networking**: Corrected internal BIFROST gateway routing for containerized execution.
- **Async Testing**: Fixed `test_jira_mcp.py` collection by correctly identifying it as an asynchronous test.
- **Volume Masking**: Resolved a critical issue where host volumes were masking application configuration code.

## [1.0.0] - 2026-03-27

### Added
- **Guided Portable Installer**: Interactive `install.sh` (Linux/macOS) and `install.ps1` (Windows) for zero-dependency setup.
- **Universal LLM Routing**: Integration with `litellm` to support any provider (OpenAI, Anthropic, Gemini, Ollama, etc.) with a unified Settings UI.
- **PII-Safe Telemetry**: In-place scrubbing of sensitive data before syncing traces to the Bifrost proxy.
- **Session Management**: Isolated data directories for concurrent SOW processing.
- **Global Persistence**: Moved secrets and configuration to `~/.sow_to_jira/.env` for persistence across updates.
- **PageIndex Integration**: Upgraded the core extraction engine to the high-fidelity PageIndex parser.

### Fixed
- **Jira Next-Gen Compatibility**: Fixed Epic linking using the modern `parent` field to support Team-managed projects.
- **Installer Resilience**: Added auto-detection and installation of Git and Docker services across Windows/macOS/Linux.
- **macOS Compatibility**: Resolved `sed` syntax differences in the installer script.
- **Shell UX**: Automated terminal reload (`exec $SHELL`) after installation for immediate `s2j` command availability.
- **Cache-Busting**: Implemented timestamped URLs in documentation to ensure the latest installer is always fetched.
