# 🚀 SOW-to-Jira: The Portable Extraction Engine

Automate complex B2B project decomposition with high-fidelity LLM orchestration and enterprise-grade observability. Transform dense Statement of Work (SOW) PDFs into actionable, hierarchical Jira tickets in seconds.

![SOW to Jira Banner](https://raw.githubusercontent.com/username/sow_to_jira/main/static/banner.png)

## ✨ Milestone v1.0 Features

- **Hierarchical Extraction**: Level-aware decomposition supporting **Epics → Stories → Sub-tasks**.
- **Universal LLM Routing**: Seamlessly switch between OpenAI, Anthropic, Gemini, and local Ollama models via LiteLLM integration.
- **Magi-Optics Observability**: Full-stack tracing and logging. Monitor extraction performance and LLM latency using **Grafana, Loki, and Tempo**.
- **High-Fidelity Terminal UX**: Real-time animated progress bars and formatted run summaries using the `Rich` library.
- **Enterprise Security**: 
  - **Non-Root Execution**: Containerized app runs as a restricted `sow` user.
  - **Fernet Encryption**: Stored API keys are encrypted at rest.
  - **PII Scrubbing**: Telemetry is scrubbed of sensitive data before sync.
- **Zero-Dependency Installer**: One-click setup for macOS, Linux, and Windows.

---

## 🛠 Quick Start (One-Command Setup)

Install all dependencies (including Docker) and configure the `sjt` alias with a single command. Supports macOS and Ubuntu/Debian.

```bash
curl -fsSL "https://calib.dev/mageswaran/sow-to-jira/-/raw/main/install.sh" | bash
```

---

## 🏃 Daily Workflow

Once installed, use the **SOW-to-Jira (SJT)** command to launch the engine.

### 1. Launch the Stack
```bash
sjt
```
This boots the **Milestone v1.0 Production Stack**:
- **Main App**: [http://localhost:8000](http://localhost:8000)
- **Observability (Grafana)**: [http://localhost:3000](http://localhost:3000)
- **LLM Gateway (Bifrost)**: [http://localhost:8080](http://localhost:8080)

### 2. Run the Extraction
You can run the extraction via the Web UI or the interactive CLI wizard:
```bash
python3 main.py
```

### 3. Review & Push
Open the dashboard at `http://localhost:8000`, review the extracted hierarchy, edit acceptance criteria, and push approved items directly to Jira Cloud.

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

## 🤝 Contributing

We use **Caliber** for AI-agent alignment. If you are developing with an AI assistant, initialize the workspace first:
```bash
npx caliber init
```

## 📄 License

Proprietary and Confidential. © 2026 Mageswaran.
