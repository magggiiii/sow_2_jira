# 🚀 SOW-to-Jira: The Portable Extraction Engine

Automate complex B2B project decomposition with high-fidelity LLM orchestration and enterprise-grade observability. Transform dense Statement of Work (SOW) PDFs into actionable, hierarchical Jira tickets in seconds.

![SOW to Jira Banner](ascii-art-text.png)

## ✨ Milestone v1.0 Features

- **Hierarchical Extraction**: Level-aware decomposition supporting **Epics → Stories → Sub-tasks**.
- **Universal LLM Routing**: Seamlessly switch between OpenAI, Anthropic, Gemini, and local Ollama models via LiteLLM integration.
- **Cloud-Native Observability**: Structured Loguru logs captured by the host platform, **Langfuse Cloud** for LLM traces and cost analytics, and **Bifrost** for LLM-gateway logging.
- **High-Fidelity Terminal UX**: Real-time animated progress bars and formatted run summaries using the `Rich` library.
- **Enterprise Security**: 
  - **Non-Root Execution**: Containerized app runs as a restricted `sow` user.
  - **Fernet Encryption**: Stored API keys are encrypted at rest.
  - **PII Scrubbing**: Telemetry is scrubbed of sensitive data before sync.
- **Zero-Dependency Installer**: One-click setup for macOS, Linux, and Windows.

---

## 🛠 Quick Start (One-Command Setup)

Install all dependencies (including Docker) and configure the `s2j` alias with a single command. Supports macOS and Ubuntu/Debian.

### 1. Before You Begin: Generate your Jira API Token
To push tasks to Jira, you need a Jira API token:
1.  Visit [Atlassian API Tokens](https://id.atlassian.com/manage-profile/security/api-tokens).
2.  Click **Create API token**.
3.  Enter a **Label** (e.g., `sow-to-jira`) and click **Create**.
4.  **Copy the token** immediately and save it securely. You will need it during the installation wizard.

### 2. Run the Unified Installer
Copy and paste the following command into your terminal:

```bash
curl -fsSL "https://raw.githubusercontent.com/magggiiii/sow_2_jira/main/scripts/install/install.sh" | bash
```

### 3. Launch and Configure
1.  Launch the stack by typing `s2j`.
2.  Open **http://localhost:8000** in your browser.
3.  Go to the **Settings** section to enter your AI Model, API Keys, and Jira credentials.
    - *Ollama Users:* Use `http://host.docker.internal:11434` as the API Base.
    - **Crucial:** You must configure Ollama to listen on all interfaces so Docker can reach it. On macOS, run `launchctl setenv OLLAMA_HOST "0.0.0.0"` in your terminal, then completely quit and restart the Ollama app.

#### Using OpenRouter
[OpenRouter](https://openrouter.ai) gives you access to 300+ models (OpenAI, Anthropic, Google, Meta, Mistral, and more) behind a single API key — handy when you want to compare providers without juggling credentials.

1.  In **Settings**, select **`openrouter`** as the provider.
2.  Paste your key (`sk-or-...`) into the **API Key** field. Settings are Fernet-encrypted at rest.
3.  Click **Fetch Models** — the dropdown will populate with every model your key has access to.
4.  Pick a model. **Recommended for SOW extraction:** `google/gemini-2.5-flash` — best quality-per-dollar on this pipeline (~$0.49 per SOW run as of May 2026: $0.30 / $2.50 per million tokens in/out, 1M context window, strong JSON-mode adherence). The Settings dialog has a one-click "Use it" button. Cheaper alternative: `deepseek/deepseek-chat-v3.1` (~$0.29/run). Premium-but-still-cheap: `anthropic/claude-haiku-4.5` (~$1.45/run). Full catalogue at [openrouter.ai/models](https://openrouter.ai/models).
5.  Save. Your runs will appear on the OpenRouter dashboard under the app name `SOW-to-Jira` (override via `OPENROUTER_APP_NAME` / `OPENROUTER_REFERER` env vars).

---

## 🏗 For Developers: Manual Distribution (Test Users)

If you want to give the software to someone personally without relying on the public registry:

### 1. Build and Tag Locally
```bash
docker build -t sow-to-jira:test .
```

### 2. Export the Image
```bash
docker save sow-to-jira:test | gzip > sow-to-jira-v1.tar.gz
```

### 3. Test User Installation
1.  Send them the `sow-to-jira-v1.tar.gz` and the `install.sh`.
2.  They run: `docker load < sow-to-jira-v1.tar.gz`.
3.  They run: `bash install.sh`.
4.  They run the loaded image directly, e.g. `docker run -p 8000:8000 sow-to-jira:test`.

---

## 🏃 Getting Started & Daily Workflow

Once installed, follow these steps to start extracting SOWs:

### 1. Launch the Stack
Type the shortcut command to boot the entire engine:
```bash
s2j
```
*Tip: If the command isn't found, run `source ~/.zshrc` (macOS) or `source ~/.bashrc` (Linux) first.*

This opens the **Main App Dashboard**: [http://localhost:8000](http://localhost:8000)

Observability is cloud-native: application logs stream to your host platform's
log viewer, and LLM traces/cost land in **Langfuse Cloud** (see *Observability & Tracing* below).

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

Observability is cloud-native — there is no self-hosted metrics fleet to run:

- **Application Logs**: Structured **Loguru** logs are emitted to stdout and captured by the host platform's log viewer (Render / Vercel).
- **LLM Traces & Cost**: End-to-end spans for every PageIndex and LLM call, plus prompt comparisons and cost tracking, land in **Langfuse Cloud** via the direct SDK.
- **LLM-Gateway Logs**: When routing through **Bifrost**, gateway-side request/response logs are available on the Bifrost deck.

A local append-only audit trail is always written to `data/audit.db` for manual inspection.

### Bifrost LLM-Gateway Admin Deck (optional)

If you route LLM traffic through Bifrost, an optional admin deck is available for developers. Launch it with the admin shortcut after running the installer locally:
```bash
s2j-admin
```
*(Or manually: `docker compose -f infra/admin/docker-compose.admin.yml up -d`)*

- **Traffic Control (Bifrost)**: [http://localhost:8081](http://localhost:8081)

---

## 🛡️ Production Integrity

Audit your deployment health using the built-in production check script:
```bash
bash scripts/prod-check.sh
```
This script verifies:
- ✅ **Non-Root User**: App is running as user `sow` (UID 1000).
- ✅ **Healthchecks**: Core services are responding.
- ✅ **Infrastructure**: Proper networking and volume isolation.
- ✅ **Configuration**: Stable runtime configuration files.

---

## 🗑 Uninstallation

To completely remove SOW-to-Jira, its data, and the `s2j` shortcut from your system, run:

```bash
s2j uninstall
```
*(Note: This will prompt for confirmation before deleting ALL data, logs, and API configurations).*

---

## 🤝 Contributing

We use **Caliber** for AI-agent alignment. If you are developing with an AI assistant, initialize the workspace first:
```bash
npx caliber init
```

---

## 📄 License

Proprietary and Confidential. © 2026 Mageswaran.
