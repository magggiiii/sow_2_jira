# 🚀 SOW-to-Jira: The Portable Extraction Engine

Automate complex B2B project decomposition with high-fidelity LLM orchestration and enterprise-grade observability. Transform dense Statement of Work (SOW) PDFs into actionable, hierarchical Jira tickets in seconds.

![SOW to Jira Banner](ascii-art-text.png)

## ✨ Milestone v1.0 Features

- **Hierarchical Extraction**: Level-aware decomposition supporting **Epics → Stories → Sub-tasks**.
- **Universal LLM Routing**: Seamlessly switch between OpenAI, Anthropic, Gemini, and local Ollama models via LiteLLM integration.
- **Observability**: Full-stack tracing and logging. Monitor extraction performance and LLM latency using **Grafana, Loki, and Tempo**.
- **High-Fidelity Terminal UX**: Real-time animated progress bars and formatted run summaries using the `Rich` library.
- **Enterprise Security**: 
  - **Non-Root Execution**: Containerized app runs as a restricted `sow` user.
  - **Fernet Encryption**: Stored API keys are encrypted at rest.
  - **PII Scrubbing**: Telemetry is scrubbed of sensitive data before sync.
- **Zero-Dependency Installer**: One-click setup for macOS, Linux, and Windows.

---

## 🛠 Quick Start (One-Command Setup)

Install all dependencies (including Docker) and configure the `sjt` alias with a single command. Supports macOS and Ubuntu/Debian.

### 1. Before You Begin: Generate your Jira API Token
To push tasks to Jira, you need a Jira API token:
1.  Visit [Atlassian API Tokens](https://id.atlassian.com/manage-profile/security/api-tokens).
2.  Click **Create API token**.
3.  Enter a **Label** (e.g., `sow-to-jira`) and click **Create**.
4.  **Copy the token** immediately and save it securely. You will need it during the installation wizard.

### 2. Run the Unified Installer
Copy and paste the following command into your terminal:
```bash
curl -fsSL "https://calib.dev/mageswaran/sow-to-jira/-/raw/main/install.sh" | bash
```

### 3. Complete the Interactive Wizard
The installer will prompt you for:
- **LiteLLM Model:** (e.g., `gpt-4o`, `anthropic/claude-3-5-sonnet`)
- **LiteLLM API Key:** Your OpenAI/Anthropic/etc. key.
- **Jira Server:** Your Atlassian domain (e.g., `https://your-company.atlassian.net`).
- **Jira Email:** The email associated with your Atlassian account.
- **Jira API Token:** The token you generated in Step 1.

---

## 🏃 Getting Started & Daily Workflow

Once installed, follow these steps to start extracting SOWs:

### 1. Launch the Stack
Type the shortcut command to boot the entire engine:
```bash
sjt
```
*Tip: If the command isn't found, run `source ~/.zshrc` (macOS) or `source ~/.bashrc` (Linux) first.*

This boots the **Milestone v1.0 Production Stack**:
- **Main App Dashboard**: [http://localhost:8000](http://localhost:8000)
- **Observability (Grafana)**: [http://localhost:3000](http://localhost:3000)

### 2. Run an Extraction
Choose your preferred interface:
- **Web UI:** Open [http://localhost:8000](http://localhost:8000) and upload your SOW PDF.
- **CLI Wizard:** Run `python3 main.py` for a guided terminal experience.

### 3. Review & Push to Jira
1.  Navigate to the **Dashboard** at `http://localhost:8000`.
2.  Review the extracted **Epic → Story → Sub-task** hierarchy.
3.  Edit any descriptions or acceptance criteria as needed.
4.  Click **Push to Jira** to sync approved items directly to your project.

---

## 📡 Observability & Tracing

SOW-to-Jira provides deep visibility into the extraction lifecycle.

- **Explore Logs**: View standardized application logs in **Grafana Loki**.
- **Analyze Traces**: Inspect end-to-end trace spans for every PageIndex and LLM call in **Grafana Tempo**.
- **Metrics**: Track token usage, latency, and success rates across different LLM providers.

**Remote Sync:**
To point a remote extraction run back to your central observability deck, configure your `.env`:
```env
BIFROST_GATEWAY_URL=http://your-central-deck:8080
BIFROST_BACKBONE_TOKEN=your-secure-token
```

---

## 🛡️ Production Integrity

Audit your deployment health using the built-in production check script:
```bash
bash scripts/prod-check.sh
```
This script verifies:
- ✅ **Non-Root User**: App is running as user `sow` (UID 1000).
- ✅ **Healthchecks**: All 5 core services are responding.
- ✅ **Infrastructure**: Proper networking and volume isolation.
- ✅ **Configuration**: Stable Tempo and Loki configuration files.

---

## 📄 License

Proprietary and Confidential. © 2026 Mageswaran.
