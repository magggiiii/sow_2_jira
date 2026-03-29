# Codex Handoff: Feature Implementation Spec

**Objective:** Implement three major feature sets for the SOW-to-Jira pipeline.
**Target Model:** OpenAI Codex / GPT-4o
**Environment:** Python 3.11, FastAPI, Vanilla JS, Docker

---

## 🏗️ Feature A: Universal LLM Provider Selector

Replace the current basic API/Model/BaseURL fields with a sophisticated provider registry.

### 1. Provider Registry Configuration
Define a `PROVIDER_REGISTRY` mapping on the backend:
- `openai`: `base_url="https://api.openai.com/v1"`, `show_base_url=False`
- `anthropic`: `base_url="https://api.anthropic.com"`, `show_base_url=False`
- `google`: `base_url=None`, `show_base_url=False`
- `ollama`: `base_url="http://localhost:11434"`, `show_base_url=True`
- `openrouter`: `base_url="https://openrouter.ai/api/v1"`, `show_base_url=False`
- `groq`: `base_url="https://api.groq.com/openai/v1"`, `show_base_url=False`
- `mistral`: `base_url="https://api.mistral.ai/v1"`, `show_base_url=False`
- `together`: `base_url="https://api.together.xyz/v1"`, `show_base_url=False`
- `cohere`: `base_url=None`, `show_base_url=False`
- `azure`: `base_url=None`, `show_base_url=True`
- `zai`: `base_url="https://api.z.ai/v1"`, `show_base_url=True`

### 2. UI Components (`ui/index.html` & `ui/app.js`)
- **Dropdown**: "Select Provider".
- **Dynamic Fields**: Show/Hide API Key and Base URL inputs based on `show_base_url` flag.
- **Model Discovery**: Triggers a list fetch when provider or credentials change.
- **Save State**: Store configuration in `data/settings.json`.

---

## 📡 Feature B: Magi-Optics Telemetry Overhaul

Rebuild the system observability stack using a unified Loki/OTLP pattern.

### 1. Structured Event Schema
The system must emit the following events via a `TelemetryEmitter` class:
- `run.started`: Metadata about the document and config.
- `run.completed`: Execution stats and task counts.
- `step.completed`: Timing for PageIndex, Extraction, etc.
- `llm.call`: Token usage, latency, and success/fail per request.

### 2. High-Fidelity Terminal Output
Refactor `observability.py` and `pipeline/` logs to use `loguru` + `rich`:
- **Docker-style Progress**: Use `rich.progress` for the node extraction loop.
- **Animated Spinners**: Replace messy print statements with `console.status()` during LLM waits.
- **Clean Symbols**: Use `✓`, `●`, `›`, `✗` for statuses.

---

## 📦 Feature C: Docker Packaging

Wrap the app for one-click deployment.

### 1. Multi-Stage Dockerfile
- **Builder**: Install build-essential and compile native dependencies.
- **Runtime**: Slim image with `libmupdf` and the pre-installed site-packages.
- **Non-Root**: Execute as user `sow` (UID 1000).

### 2. Docker Compose
- Map persistent volume `sow_data` to `/app/data`.
- Default to `127.0.0.1:8000` port mapping.
- Add an optional `docker-compose.ollama.yml` for local-only sidecar model hosting.

---

## 🔗 Implementation Constraints
- **LiteLLM**: Use the universal `completion` interface.
- **JSON Security**: Use `extract_json` helpers to prevent malformed response crashes.
- **Concurrency**: Do not block the FastAPI event loop with long-running extraction threads.
