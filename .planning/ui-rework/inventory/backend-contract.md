# Backend API Contract — SOW-to-Jira (`ui/server.py`)

This is the **frozen contract** the React frontend must preserve. The migration is view-layer only:
React hits the exact same endpoints, same query params, same request/response JSON, same cookies/headers.
Nothing below changes. `ui/src/api.js` already implements every call and can be reused verbatim.

App object: `app = FastAPI(title="SOW to Jira Pipeline")` (`ui/server.py:73`). Everything is defined on this single
app — there is no `APIRouter`, no versioned prefix. All API routes live under `/api/*` (plus `/`, `/healthz`, `/static`).

---

## 1. Static mount + SPA shell

- **`app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")`** — `ui/server.py:189`.
  - `FRONTEND_DIR = ui/dist` if `ui/dist/index.html` exists (prod / after `npm run build`), else raw `ui/` (`ui/server.py:187-188`). The React build must therefore emit its `index.html` + hashed assets into **`ui/dist/`**, and asset URLs must be under **`/static/`** (Vite `base: '/static/'`).
- **`GET /`** → `FileResponse(FRONTEND_DIR / "index.html")` (`ui/server.py:404-406`). This is the SPA entry. No server-side routing/params. Any client-side routing must be handled inside the SPA (server only serves `index.html` at `/`; it does NOT catch-all other paths).
- **Uploads dir**: `data/uploads/` (`ui/server.py:180-181`). Sessions persist under `data/sessions/<run_id>/{metadata.json,pipeline_output.json}`.

## 2. CORS / credentials

`CORSMiddleware` (`ui/server.py:156-162`):
- `allow_origins` = `BETTER_AUTH_TRUSTED_ORIGINS` env, default `http://localhost:8000,http://127.0.0.1:8000`.
- `allow_credentials=True`; methods `GET, POST, PUT, DELETE, OPTIONS`; headers `*`.
- Implication for React: same-origin in prod (served from FastAPI). In the Vite dev server (`make ui-dev`, different port) you must **proxy `/api`, `/static`, `/healthz`, `/` to :8000** so the cookie + origin stay same-site — do NOT rely on CORS for the dev cookie flow.

## 3. Auth / CSRF / session mechanics — DUAL MODE (critical)

The whole app runs in one of two modes, decided at runtime by `auth_enabled()` (`ui/server.py:272-284`):

- **Auth OFF (default, single-user/local — what you'll dev against):** `get_current_user` returns a fixed `_DefaultUser` (`local-default-user`, `ui/server.py:248-304`). No `sow_session` cookie needed → **no 401s**. Ownership checks are no-ops. **CSRF is NOT enforced** (`enforce_csrf` early-returns, `ui/server.py:318-319`). Every route works with zero auth headers.
- **Auth ON (when a `SessionStore` is wired via `app.state.session_store` or `get_session_store` dependency override):** real per-user auth. Missing/forged/expired `sow_session` cookie → **401**. Data routes 404 on cross-user access (never 403 — existence not leaked, `_assert_owned_or_404`, `ui/server.py:389-402`). **CSRF enforced** on state-changing routes.

**Cookies:**
- `sow_session` — opaque session token, HttpOnly server-set (auth backend, not in this file). `SESSION_COOKIE_NAME` in `auth/deps.py:19`.
- `sow_csrf` — CSRF double-submit token. **NOT HttpOnly** (JS must read it). `samesite="strict"`, `httponly=False`, set by `GET /api/csrf` (`ui/server.py:264, 419-421`).

**CSRF double-submit pattern (`enforce_csrf`, `ui/server.py:307-323`):** On state-changing requests in hardened mode, send header **`X-CSRF-Token`** equal to the `sow_csrf` cookie value (compared with `secrets.compare_digest`). Missing/mismatch → **403**. React must send this on every POST/PUT/DELETE. **`ui/src/api.js` already does this** (`apiFetch`, lines 206-226): safe methods skip it; unsafe methods call `ensureCsrfToken()` (in-memory cache → `sow_csrf` cookie → mint via `GET /api/csrf`), attach `X-CSRF-Token`, and always send `credentials: 'same-origin'`. It **degrades gracefully** — if no cookie and `/api/csrf` unavailable, the request still goes through without the header (server only enforces when auth is on). It also fires an injectable `onUnauthorized` handler on any 401 (`setOnUnauthorized`). **Reuse this file as-is in React** — wrap in a hook if you like, but do not reimplement.

**Routes requiring CSRF (all state-changing, all take `_csrf=Depends(enforce_csrf)`):** `POST /api/cancel/{run_id}`, `DELETE /api/sessions/{run_id}`, `POST /api/upload`, `POST /api/providers/{provider_id}/models`, `POST /api/settings`, `POST /api/process`, `POST /api/tasks`, `POST /api/tasks/add`, `POST /api/tasks/approve_all`, `POST /api/push`, `POST /api/jira/test`.
**Routes with user resolution but NO CSRF (GET/read):** `GET /api/csrf`, `GET /api/sessions`, `GET /api/tasks`, `GET /api/status`.
**Routes with NO auth dep at all (public):** `GET /`, `GET /healthz`, `GET /api/providers`, `GET /api/settings`.

## 4. Health probe

- **`GET /healthz`** → `{"status": "ok"}` (`ui/server.py:425-432`). No DB/settings/network. Used by container/Render liveness. Filtered out of access logs alongside `/api/status` (`ui/server.py:110-115`).

---

## 5. Full `/api/*` route table

Legend: **CSRF** = requires `X-CSRF-Token` when auth on. **Owner** = runs `_assert_owned_or_404(session_id, user)`.

### `GET /api/csrf` — `ui/server.py:408-422`
- Req: none. Resp: `{ "csrf_token": "<url-safe-32>" }` + sets `sow_csrf` cookie.

### `GET /api/sessions` — `ui/server.py:453-482`
- Req: none. Resp: **array** of session metadata dicts, sorted `created_at` desc:
  `[{ "run_id", "filename", "llm_mode", "owner_id"?, "created_at" }]`. Auth-on filters to caller's `owner_id`.

### `GET /api/tasks?session_id=<id>` — `ui/server.py:484-493` — **Owner**
- Req: optional `session_id` query. Resp: the session's `pipeline_output.json` plus injected `env_defaults`:
  `{ "tasks": [ManagedTask...], "config": {...}, "health"?: {...}, "env_defaults": { "jira_project_key", "jira_server" } }`.
  When no/blank session → `{ "tasks": [], "config": {}, "env_defaults": {...} }`. Never 404s on missing file (returns empty).

### `GET /api/status?session_id=<id>` — `ui/server.py:495-500` — **Owner**
- Req: optional `session_id`. Resp: `ProcessingStatus` dump (see §6). No `session_id` → default idle status.
- **This is the polling endpoint** (kept out of access logs). Frontend polls this during runs & pushes.

### `POST /api/cancel/{run_id}` — `ui/server.py:579-591` — **CSRF + Owner**
- Path: `run_id`. Req body: none. Resp: `{ "message": "Cancellation signal sent" }`. 404 if no active run.

### `DELETE /api/sessions/{run_id}` — `ui/server.py:593-610` — **CSRF + Owner**
- Path: `run_id`. Resp: `{ "message": "Session <id> deleted" }`. 404 if dir missing. Stops run if live, rmtree's session dir.

### `POST /api/upload` — `ui/server.py:629-660` — **CSRF**
- Req: **multipart/form-data**, field name **`file`** (a PDF). Resp: `{ "filename": "<safe-basename>" }`.
- Validation: must end `.pdf` (400), `<= SOW_MAX_UPLOAD_MB` (env, default 50MB → 413), must start with `%PDF-` magic (400), filename sanitized to basename (traversal → 400). **Do NOT set `Content-Type` manually — let the browser set the multipart boundary** (`api.js:307-312` uses bare FormData).

### `GET /api/providers` — `ui/server.py:662-664` — public
- Resp: `{ "providers": PROVIDER_REGISTRY }` where each key maps to `{ "base_url": str|null, "show_base_url": bool }`.
  Providers: `openai, anthropic, google, ollama, openrouter, groq, mistral, together, cohere, azure, zai` (`config/settings.py:10-21`).

### `POST /api/providers/{provider_id}/models` — `ui/server.py:691-775` — **CSRF**
- Path: `provider_id`. Req body `ModelDiscoveryRequest`: `{ api_key?, base_url?, azure_deployment_name?, azure_api_version? }` (all optional; `api_key: "***"` is treated as "use stored key").
- Resp: `{ "success": true, "models": ["<id>", ...] }` (sorted, deduped). Errors: 404 unknown provider, 400 missing base_url / azure api-version / unsupported, upstream status passed through, 500 on discovery failure — all as `{ "detail": "..." }`. **Has server-side retry** (tenacity, 3 attempts on connect/timeout) and a 5-min in-memory cache.

### `GET /api/settings` — `ui/server.py:777-800` — public
- Resp: `{ "provider", "providers": { <id>: { "model", "api_key": "***"|"" , "base_url", "azure_deployment_name", "azure_api_version" } }, "jira_server_url", "jira_api_token": "***"|"" }`. **Secrets are masked as `"***"` — never returned in plaintext.**

### `POST /api/settings` — `ui/server.py:802-875` — **CSRF**
- Req body `SettingsConfig`: `{ provider?, model?, api_key?, base_url?, azure_deployment_name?, azure_api_version?, jira_server_url?, jira_api_token? }`. Send `api_key`/`jira_api_token` = `"***"` (or omit) to keep the stored secret unchanged; a real value re-encrypts it.
- Resp: `{ "message": "Settings saved successfully" }`. 500 → `{ "detail": ... }` on keyfile/runtime error. Clears the model cache.

### `POST /api/process` — `ui/server.py:877-893` — **CSRF**
- Req body `ProcessRequest`: `{ "pdf_filename": str, "llm_mode": "api"|"local"|"custom", "jira_hierarchy": "flat"|"epic_task"|"story_subtask", "jira_project_key": str, "skip_indexing": bool=false, "max_nodes": int=200 }`.
- Resp: `{ "message": "Processing started", "run_id": "<uuid>" }`. Fire-and-forget (FastAPI BackgroundTasks). **Frontend then polls `GET /api/status?session_id=<run_id>`.** `run_id` is a full UUID (`make_run_id`).

### `POST /api/tasks?session_id=<id>` — `ui/server.py:906-928` — **CSRF + Owner**
- Req body `TaskUpdate`: `{ "id": str, "title": str, "short_description"?, "use_case"?, "acceptance_criteria"?: [str], "considerations_constraints"?: [str], "deliverables"?: [str], "mockup_prototype"?, "status": str }`.
- Resp: `{ "message": "Task updated successfully", "task": <echo> }`. 404 `{ "detail": "Task not found" }` if id absent. Merges via `model_dump(exclude_unset=True)`.

### `POST /api/tasks/add?session_id=<id>` — `ui/server.py:934-960` — **CSRF + Owner**
- Req body `AddTaskRequest`: `{ "title": str, "short_description": str }`.
- Resp: `{ "message": "Task added successfully", "task": <new task dict> }`. New task defaults `status:"APPROVED"`, `confidence:1.0`, fresh uuid, empty `flags`/`source_refs`.

### `POST /api/tasks/approve_all?session_id=<id>` — `ui/server.py:962-978` — **CSRF + Owner**
- Req: no body. Flips all `CLOSED` tasks → `APPROVED`. Resp: `{ "message": "Approved N tasks successfully", "count": N }`.

### `POST /api/push?session_id=<id>` — `ui/server.py:1140-1161` — **CSRF + Owner**
- Req body `PushRequest` (optional): `{ "jira_hierarchy"?, "jira_project_key"?, "override": bool=false }`. `override` force-pushes flagged/DEGRADED-run tasks past the PushGate.
- Resp (sync, then async work): `{ "success": true, "started": true, "run_id": str, "message": "Push started" }`.
  - If no approved tasks: `{ "success": false, "message": "No approved tasks to push", "results": [] }` (200, not error).
  - If a run is already active for the session: **409** `{ "detail": "A task is already running for this session" }`.
- Runs on a background thread. **Frontend polls `GET /api/status?session_id=<run_id>`** (status `kind:"jira_push"`). Per-task `JiraPushResult` is persisted onto each task's `push_result` field and success flips status → `PUSHED` (visible on next `GET /api/tasks`).

### `POST /api/jira/test` — `ui/server.py:1163-1204` — **CSRF**
- Req: no body. Read-only whoami (`JIRA.myself()`), no writes. **Never raises** — returns classified JSON:
  - Success: `{ "success": true, "user": <displayName|email|null>, "server": <url> }`.
  - Failure: `{ "success": false, "error": str, "error_class": "transient"|"user_fixable"|"terminal" }`.
  - Missing creds: `{ "success": false, "error": "...", "error_class": "user_fixable" }`.

---

## 6. `ProcessingStatus` (the poll payload) — `ui/server.py:192-206`

Returned by `GET /api/status` and drives the progress overlay:
```
{
  "is_running": bool,
  "current_step": int,
  "message": str,               // e.g. "Idle", "Pipeline Complete", "Error: ..."
  "progress": float,            // 0.0..1.0
  "error": str | null,
  "error_class": "transient" | "user_fixable" | "terminal" | null,  // core/errors.py:44-46
  "run_id": str | null,
  "owner_id": str | null,
  "kind": "pipeline" | "jira_push",
  "logs": [str]                 // rolling, capped at 50, each "[HH:MM:SS] msg"
}
```

## 7. Key task response shapes (from `models/schemas.py`, serialized in `pipeline_output.json`)

**`ManagedTask`** (`schemas.py:350-372`) — items in `GET /api/tasks .tasks[]`:
`id (UUID str)`, `title`, `short_description`, `acceptance_criteria?` (list of `{condition,type,verified_by,...}` objects), `use_case?`, `considerations_constraints?[]`, `deliverables?[]`, `mockup_prototype?`, `confidence (0..1)`, `flags[]` (`TaskFlag` enum values), `continues_to_next`, `status` (`TaskStatus`), `jira_issue_key?`, `source_refs[]`, `merged_from[]`, `dependencies[]`, `created_at`, `updated_at`. After a push: extra `push_result` (a `JiraPushResult` dict) may be present.

**`TaskStatus`** (mirrored in `api.js:14-21`): `OPEN, CLOSED, MERGED, REJECTED, APPROVED, PUSHED`.

**`JiraPushResult`** (`schemas.py:470-480`, persisted as `task.push_result`): `{ task_id, success, jira_issue_key?, jira_issue_url?, error?, error_class?, warning? }`.

## 8. Existing frontend adapter — reuse verbatim

`ui/src/api.js` (337 lines) is the single source of truth for all calls and can move into React unchanged:
- Fetch wrappers: `fetchProviders, fetchSettings, saveSettings, fetchModels, testJiraConnection, fetchSessions, fetchTasks, fetchStatus, postTask, approveAll, uploadFile, startProcess, pushToJira, cancelRun, deleteSession`.
- `apiFetch` (credentials + CSRF), `setOnUnauthorized`, `sessionQuery`.
- Pure view-model helpers (framework-agnostic, keep them): `TASK_STATUS`, `STATUS_BUCKET`, `bucketOf`, `computeStats`, `shouldShowTask`, `statusDisplay`, `pushPreflight`, `decideSessionSwitch`, `isApproved`, `isRejected`, `containerLabelFor`, `childLabelFor`.
