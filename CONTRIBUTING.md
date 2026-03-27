# Contributing to SOW-to-Jira

We welcome contributions! Whether you're fixing a bug, improving documentation, or proposing new features, your help is appreciated.

## How to Contribute

1. **Fork the Repository**: Create your own copy of the project on GitLab.
2. **Create a Branch**: Use a descriptive name for your feature or fix (e.g., `feat/new-llm-provider` or `fix/epic-linking`).
3. **Make Your Changes**: Ensure your code follows the existing style and is well-documented.
4. **Run Tests**: If applicable, verify your changes locally.
5. **Submit a Merge Request**: Provide a clear description of what your changes do and why they're needed.

## Development Setup

1. Clone the repository locally.
2. Ensure you have **Docker** and **Docker Compose** installed.
3. Run the application using the `sjt` command:
   ```bash
   bash install.sh
   sjt
   ```

## Code of Conduct

Please be respectful and professional in all interactions. We aim to foster a collaborative and inclusive environment for everyone.

---

### AI-Agent Guidelines
If you are using an AI coding assistant (like Antigravity or GitHub Copilot), please ensure:
- **No PII**: Never commit real SOW data or private API keys.
- **Verification**: All AI-generated code must be verified using the local test suite.
- **Standards**: Follow the `pipeline/observability.py` patterns for all new modules.
