# Codex Handoff: Red Team Hardening Spec

**Objective:** Patch critical security and reliability vulnerabilities in the Sprint Delta implementation.
**Priority:** Mandatory (Must fix before or during Feature Implementation)
**Target Model:** OpenAI Codex / GPT-4o

---

## 🔒 Security Fix 1: Protect API Keys in Logs (Fix A-1)

**Issue**: Passing `api_key` in the URL query string writes secrets to server access logs.
**Action**:
- Change `GET /api/providers/{id}/models?api_key=...` to `POST /api/providers/{id}/models`.
- Move `api_key` and `base_url` into the JSON request body.
- Update `ui/app.js` to use `POST` with `JSON.stringify(body)` and `headers: {'Content-Type': 'application/json'}` for model discovery.

---

## 🔑 Security Fix 2: Persistent Encryption Key (Fix A-2 & C-1)

**Issue**: MAC-based encryption keys are guessable and break on hardware/Docker changes.
**Action**:
- On backend startup (`ui/server.py`), check if `data/.keyfile` exists.
- If not: Generate 32 random bytes via `secrets.token_bytes(32)`. Write to `data/.keyfile` with `os.chmod(f, 0o600)`.
- If yes: Read the existing key.
- Use this file-based key for all `cryptography.fernet` operations.
- **Docker**: In the README, add a critical note: *"Backup `/app/data/.keyfile`. If lost, your encrypted settings cannot be recovered."*

---

## 🕵️ Security Fix 3: Telemetry Privacy (Fix B-1 & B-2)

**Issue**: Section titles leak confidential business data (PII/NDA risk). The backbone token is exposed in the image.
**Action**:
- **Scrubbing**: In `pipeline/telemetry.py`, remove `section_title` from any payload. Use `section_index` (integer) instead.
- **Append-Only**: Document that the `BACKBONE_TOKEN` used in `pipeline/observability.py` must have its RBAC restricted on the Maxim Bifrost side to "Write-Only (Append)".
- **Obfuscation**: For the hardcoded token, use a simple base64 + XOR obfuscation to stop casual `grep` or `cat` inspection in the Docker image.

---

## 🚀 Reliability Fix 4: Production-Grade Telemetry (Fix B-3)

**Issue**: Per-event `threading.Thread` spawning causes resource exhaustion and app stalls.
**Action**:
- Implement a **Singleton Queue Worker** in `pipeline/telemetry.py`.
- Use a `queue.Queue(maxsize=1000)` and a single persistent daemon thread to process outgoing telemetry requests.
- Ensure the main thread uses `put_nowait()` to avoid blocking the pipeline if the telemetry server is slow.

---

## 📦 Reliability Fix 5: Docker Runtime Stability (Fix C-2 & C-3)

**Issue**: Multi-stage Docker builds break native C-extension linking (`PyMuPDF`). Sync Jira pushes block the UI.
**Action**:
- **Dockerfile**: In the `runtime` stage, ensure `apt-get install -y libmupdf-dev libfreetype6-dev` is called **before** copying the `site-packages` from the builder. Verify `.so` links match.
- **Async Push**: Move the logic for pushing tasks to Jira into a background thread (same pattern as `run_pipeline_task`). Return a `run_id` immediately so the UI can poll for push progress without freezing.

---

## 🏗️ Execution Guidance for Codex
1. Apply the **Encryption Key Fix (Fix 2)** first — it is the foundation for saving settings.
2. Apply the **POST body fix (Fix 1)** before testing the Settings UI.
3. Integrate the **Queue Worker (Fix 4)** before enabling the Telemetry Overhaul.
