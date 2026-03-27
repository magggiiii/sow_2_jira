# 🚀 SOW-to-Jira: The Portable Extraction Engine

Transform complex Statement of Work (SOW) PDFs into testable, hierarchical Jira tickets in seconds. Built for high-stakes B2B project management.

![SOW to Jira Banner](https://raw.githubusercontent.com/username/sow_to_jira/main/static/banner.png) <!-- Replace with real banner later -->

## ✨ Features

- **Hierarchical Extraction**: Level-aware decomposition (Epics → Stories → Sub-tasks).
- **Universal LLM Routing**: One-click switching between OpenAI, Anthropic, Gemini, and local Ollama (via LiteLLM).
- **Zero-Dependency Portable Installer**: A single command checks your system, installs Docker, and launches the app.
- **Privacy First**: All SOW data stays on your machine. Anonymous execution telemetry is scrubbed of PII before sync.
- **Session Management**: Work on multiple SOWs simultaneously without state overlap.

---

## 🛠️ Quick Start (Guided Install)

Run the following command in your terminal to automatically set up the environment, install dependencies, and launch the application:

### macOS / Linux
```bash
curl -fsSL "https://calib.dev/mageswaran/sow_2_jira/-/raw/main/install.sh?v=$(date +%s)" | bash
```

### Windows (CMD & PowerShell)
```cmd
powershell -ExecutionPolicy Bypass -Command "irm 'https://calib.dev/mageswaran/sow_2_jira/-/raw/main/install.ps1?v=$(Get-Date -UFormat %s)' | iex"
```

---

## 🏃 Daily Use

Once installed, simply type:
```bash
sjt
```
This command will:
1. Boot the required Docker containers.
2. Link your global data folder (`~/.sow_to_jira`).
3. Open your browser to `http://localhost:8000`.

---

## ⚙️ Configuration

The easiest way to configure SOW-to-Jira is through the **Settings (gear icon)** in the web interface. 

Your secrets are persisted globally at `~/.sow_to_jira/.env`, ensuring they are preserved across tool updates.

| Feature | How to Configure |
| :--- | :--- |
| **LLM Routing** | Set `Universal API Key`, `Model`, and `Base URL` in the Settings Modal. |
| **Jira Integration** | Set your Jira Server URL and API Token in the Settings Modal. |
| **Telemetry** | Telemetry is enabled by default. To opt-out, edit `~/.sow_to_jira/.env` and set `BIFROST_TELEMETRY_URL=""`. |

---

## 🛡️ Security & Privacy

- **Local Storage**: All session data and PDFs are stored in `~/.sow_to_jira/data`.
- **PII Scrubbing**: Our observability layer uses a "Scrub-First" policy. Raw prompts and responses are NEVER sent to our central telemetry server.
- **Offline Mode**: If you are offline, telemetry is buffered locally and synced only when you are back online.

---

## 🤝 Contributing

We use **Caliber** for AI-agent alignment. If you are using an AI coding assistant, run:
```bash
npx caliber init
```

---

## 📄 License

Proprietary and Confidential. © 2026 Mageswaran.
