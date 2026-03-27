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
curl -fsSL https://calib.dev/mageswaran/sow_2_jira/-/raw/main/install.sh | bash
```

### Windows (PowerShell)
```powershell
irm https://calib.dev/mageswaran/sow_2_jira/-/raw/main/install.ps1 | iex
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

The tool uses a `.env` file for core configuration. On first run, the installer will create a template for you.

| Variable | Description |
| :--- | :--- |
| `LITELLM_MODEL` | The model string (e.g., `gpt-4o`, `anthropic/claude-3-5-sonnet`) |
| `LITELLM_API_KEY` | Your LLM provider API key |
| `JIRA_SERVER` | Your Atlassian domain (e.g., `https://my-company.atlassian.net`) |
| `JIRA_API_TOKEN` | Your Atlassian API Token |

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

MIT © 2026 [Mageswaran](https://calib.dev/mageswaran)
