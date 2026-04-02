# 🚀 SOW-to-Jira: The Portable Extraction Engine

Automate complex B2B project decomposition with high-fidelity LLM orchestration and enterprise-grade observability. Transform dense Statement of Work (SOW) PDFs into actionable, hierarchical Jira tickets in seconds.

![SOW to Jira Banner](ascii-art-text.png)

## ✨ Milestone v1.0 Features

- **Hierarchical Extraction**: Level-aware decomposition supporting **Epics → Stories → Sub-tasks**.
- **Universal LLM Routing**: Seamlessly switch between OpenAI, Anthropic, Gemini, and local Ollama models via LiteLLM integration.
- **Argus Global Observability**: Full-stack tracing, logging, and fleet metrics. Monitor remote instances via **Grafana, Loki, Tempo, and Langfuse**.
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
curl -fsSL "https://calib.dev/mageswaran/sow_2_jira/-/raw/milestone-v1-stabilization/install.sh" | bash
```

### 3. Complete the Interactive Wizard
The installer will prompt you for:
- **AI Model:** (e.g., `gpt-4o`, `anthropic/claude-3-5-sonnet`)
  - *Ollama Users:* Use the `ollama/` prefix (e.g., `ollama/llama3`).
- **Model API Key:** Your OpenAI/Anthropic key. 
  - *Ollama Users:* Leave this blank.
- **AI API Base (Optional):** Usually blank.
  - *Ollama Users:* Use `http://host.docker.internal:11434`.
- **Jira Server:** Your Atlassian domain (e.g., `https://your-company.atlassian.net`).
- **Jira Email:** The email associated with your Atlassian account.
- **Jira API Token:** The token you generated in Step 1.

---

## 🏃 Getting Started & Daily Workflow

Once installed, follow these steps to start extracting SOWs:

### 1. Launch the Stack
Type the shortcut command to boot the entire engine:
```bash
s2j
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

## 👁️ Argus: Global Observability (Admin)

If you are the developer/admin managing the fleet, use the **Argus HQ** stack on your laptop to monitor all remote instances.

### 1. Launch the Argus HQ Deck
```bash
docker compose -f docker-compose.hq.yml up -d
```

### 2. Monitoring Dashboards
- **Bird's Eye View (Grafana)**: [http://localhost:3001](http://localhost:3001)
  - *Import the dashboard from `config/argus-dashboard.json` on first run.*
- **AI Deep-Dive (Langfuse)**: [http://localhost:3002](http://localhost:3002)
- **Traffic Control (Bifrost)**: [http://localhost:8081](http://localhost:8081)

### 3. Exposing the HQ via Tunnel
To receive data from anywhere, run your tunnel provider (e.g., LocalXpose) and point it to your laptop's ports:
- **OTLP (gRPC)**: 4317
- **OTLP (HTTP)**: 4318

---

## 📡 Observability & Tracing

SOW-to-Jira provides deep visibility into the extraction lifecycle via Argus.

- **Explore Logs**: View standardized application logs in **Grafana Loki**.
- **Analyze Traces**: Inspect end-to-end trace spans for every PageIndex and LLM call in **Grafana Tempo**.
- **AI Analytics**: Side-by-side prompt comparisons and cost tracking in **Langfuse**.
- **Fleet Metrics**: Track token usage, latency, and success rates across all users.

**Remote Sync:**
Remote synchronization is **disabled by default**. To enable it, or to point a remote extraction run back to your central Argus HQ deck, configure your `.env`:
```env
ARGUS_SYNC_ENABLED=true
ARGUS_HQ_URL=https://hz8nuthhmt.loclx.io
ARGUS_BACKBONE_TOKEN=your-secure-token
```

Regardless of this setting, all logs are saved locally in `~/.sow_to_jira/data/audit.jsonl` for manual collection.

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

## 🗑 Uninstallation

To completely remove SOW-to-Jira, its data, and the `s2j` shortcut from your system, run:

```bash
# WARNING: This will delete ALL data, logs, and API configurations.
# 1. Stop and remove containers
cd ~/.sow_to_jira && docker compose -f docker-compose.user.yml down -v 2>/dev/null
# 2. Remove data and shortcut
rm -rf ~/.sow_to_jira && sed -i.bak '/# SOW-to-Jira/,+1d' ~/.zshrc ~/.bashrc 2>/dev/null
```

---

## 🤝 Contributing

We use **Caliber** for AI-agent alignment. If you are developing with an AI assistant, initialize the workspace first:
```bash
npx caliber init
```

---

## 📄 License

Proprietary and Confidential. © 2026 Mageswaran.
