# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
