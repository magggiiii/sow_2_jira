# 🚀 SOW-to-Jira: The Portable Extraction Engine

Transform complex Statement of Work (SOW) PDFs into testable, hierarchical Jira tickets in seconds. Built for high-stakes B2B project management.

![SOW to Jira Banner](https://raw.githubusercontent.com/username/sow_to_jira/main/static/banner.png) <!-- Replace with real banner later -->

## ✨ Features

- **Hierarchical Extraction**: Level-aware decomposition (Epics → Stories → Sub-tasks).
- **Universal LLM Routing**: One-click switching between OpenAI, Anthropic, Gemini, and local Ollama.
- **Magi-Optics Observability**: Full-stack tracing and logging via Bifrost, Loki, and Tempo.
- **High-Fidelity Terminal UI**: Real-time extraction progress bars and formatted run summaries using `rich`.
- **Zero-Dependency Portable Installer**: A single command checks your system, installs Docker, and launches the app.
- **Privacy First**: All SOW data stays on your machine.
- **Session Management**: Work on multiple SOWs simultaneously without state overlap.

---

### macOS (Intel & Apple Silicon)
```bash
curl -fsSL "https://calib.dev/mageswaran/sow_2_jira/-/raw/main/install_mac.sh?v=$(date +%s)" | bash
```

### Ubuntu / Debian
```bash
curl -fsSL "https://calib.dev/mageswaran/sow_2_jira/-/raw/main/install_ubuntu.sh?v=$(date +%s)" | bash
```

### Windows (CMD & PowerShell)
```cmd
powershell -ExecutionPolicy Bypass -Command "irm 'https://calib.dev/mageswaran/sow_2_jira/-/raw/main/install_windows.ps1?v=$(Get-Date -UFormat %s)' | iex"
```

---

## 🏃 Daily Use

Once installed, simply type:
```bash
sjt
```
This command launches the **Milestone v1.0 Stack**:
1. **App**: http://localhost:8000
2. **Observability (Grafana)**: http://localhost:3000
3. **LLM Gateway (Bifrost)**: http://localhost:8080

---

## 📡 Observability & Tracing

SOW-to-Jira includes a built-in observability stack to monitor extraction performance and LLM latency.

- **Logs**: Standardized application logs are shipped to **Loki**.
- **Traces**: End-to-end trace spans for every node extraction are visible in **Tempo**.
- **Dashboards**: Access **Grafana** at `http://localhost:3000` to explore logs and traces.

To point a remote extraction run back to your central deck, set the following in your `.env`:
```env
BIFROST_GATEWAY_URL=http://your-central-deck:8080
BIFROST_BACKBONE_TOKEN=your-token
```

---

## ⚙️ Configuration

Configure SOW-to-Jira through the **Settings (gear icon)** in the web interface.

Secrets are persisted at `~/.sow_to_jira/data/settings.json` and encrypted using the key in `~/.sow_to_jira/data/.keyfile`.

| Feature | How to Configure |
| :--- | :--- |
| **LLM Routing** | Set `Universal API Key`, `Model`, and `Base URL` in Settings. |
| **Jira Integration** | Set your Jira Server URL and API Token in Settings. |
| **Telemetry** | Set `BIFROST_GATEWAY_URL` to your central instance. |

---

## 🛡️ Production Readiness

Audit the deployment integrity using our built-in check script:
```bash
bash scripts/prod-check.sh
```
This script verifies:
- **Non-Root Execution**: Container runs as user `sow`.
- **Healthchecks**: All 5 core services are responding.
- **Isolation**: Internal networking is secure.

---

## 🤝 Contributing

We use **Caliber** for AI-agent alignment. If you are using an AI coding assistant, run:
```bash
npx caliber init
```

---

## 📄 License

Proprietary and Confidential. © 2026 Mageswaran.
