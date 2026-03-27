# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
- **Shell UX**: Automated terminal reload (`exec $SHELL`) after installation for immediate `sjt` command availability.
- **Cache-Busting**: Implemented timestamped URLs in documentation to ensure the latest installer is always fetched.
