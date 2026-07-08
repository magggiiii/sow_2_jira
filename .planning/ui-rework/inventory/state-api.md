# Inventory: state-api (state.js / api.js / polling.js)

Scope: the reactive store, the network/view-model adapter, and the status-polling
loop that together form the data layer under the current vanilla-JS UI. Consumers
are `ui/src/main.js` (entry, owns handlers) and `ui/src/render.js` (DOM). Backend
is FastAPI at `ui/server.py`.

Verdict up front: **state.js and api.js port to React essentially unchanged.**
**polling.js is the only file that needs a real rewrite** — it is 50% `setInterval`
lifecycle (→ a hook) and 50% direct DOM mutation of the progress overlay (→ JSX +
store). The pure helpers inside all three files (`bucketOf`, `computeStats`,
`shouldShowTask`, `pushPreflight`, `decideSessionSwitch`, `errorRecovery`, the enums)
are framework-agnostic and stay as-is.

---

## 1. nanostores atoms (`ui/src/state.js`, 53 lines)

Three atoms + a handful of setter actions. This is the entire canonical client state.

| Atom | Initial value | Meaning | Persistence |
|------|---------------|---------|-------------|
| `$activeSessionId` (`state.js:19`) | `sessionStorage['activeSessionId']` or `null` | The run currently displayed / being polled. `null` == "new extraction" screen. | Mirrored to `sessionStorage` via `setActiveSession` (`state.js:31`). |
| `$taskData` (`state.js:24`) | `{ tasks: [], config: {} }` | The full `/api/tasks` payload: `{ tasks:[...], config:{...}, env_defaults:{...}, run_id }`. | In-memory only. |
| `$pollStatus` (`state.js:27`) | `{ isRunning: false, pollErrorCount: 0 }` | Poll lifecycle — replaces the old module-level `statusInterval` presence + `pollErrorCount`. | In-memory only. |

Actions (all trivial `.set()` wrappers):
- `setActiveSession(id)` / `clearActiveSession()` — set atom AND sync `sessionStorage` (`state.js:31-41`).
- `setTaskData(payload)` / `resetTaskData()` — set/reset `$taskData` (`state.js:43-49`).
- `setPollStatus(patch)` — shallow-merge patch into `$pollStatus` (`state.js:51-53`).

Notes / gotchas:
- There is a **second, sneaky piece of state** not in an atom: `sessionStorage['activeSessionId']` is written directly (not via `setActiveSession`) inside `polling.js:190` (`sessionStorage.removeItem('activeSessionId')` when a run stops). This is a side-channel the atom doesn't see. On the React port this MUST be folded back through `clearActiveSession()` so the store stays canonical (see migration note M4).
- `localStorage['lastViewedSessionId']` (LAST_SESSION_KEY) lives entirely in `main.js:49,729-749`, NOT in state.js. It survives refresh independent of the running flag. It should become a fourth persisted atom (or a persistent-atom) in the React version rather than staying as `main.js` locals.
- Also state-shaped but living as `main.js` locals today: `selectedFile`, `lastOp` (`'extraction'|'push'`), `lastPushRequest`, `jiraConfigured`, `lastPushFailure`. These are genuine app state that React components will need — candidates to promote into atoms or a run-context (see M5).

## 2. api.js (`ui/src/api.js`, 337 lines)

Two halves:

### 2a. Pure view-model (no DOM, no framework) — PORTS UNCHANGED
- `TASK_STATUS` enum (`api.js:14`) — mirror of `models/schemas.py::TaskStatus` (OPEN/CLOSED/MERGED/REJECTED/APPROVED/PUSHED).
- `STATUS_BUCKET` (`api.js:34`) — single source of truth mapping every status → one of `pending|approved|rejected|pushed`. `bucketOf()` (`api.js:45`) falls back to `pending`.
- `computeStats(tasks)` (`api.js:53`) → `{ total, pending, approved, rejected, pushed }`.
- `shouldShowTask(t, filters)` (`api.js:66`) — filter predicate; `filters = {pending,approved,rejected,pushed,flagged}`.
- `statusDisplay(status)` (`api.js:76`) — inline label+colour token (returns `null` for OPEN/CLOSED/MERGED). Note: returns emoji strings + `var(--...)` colour tokens — fine to keep, but in React these become component props.
- `pushPreflight({activeSessionId, approvedCount, jiraConfigured})` (`api.js:96`) → `{enabled, reason}`. Drives both button-disabled AND tooltip.
- `decideSessionSwitch(status)` (`api.js:113`) → `'resume' | 'load'`.
- `isApproved/isRejected(t)` (`api.js:118/121`), `containerLabelFor/childLabelFor(hierarchy)` (`api.js:126/131`).

All of these are already unit-tested in `api.test.js` (357 lines) and have zero
framework coupling. They can be imported directly by React components.

### 2b. Fetch layer — PORTS UNCHANGED (this is the whole point of keeping api.js)
- **CSRF machinery** (`api.js:150-226`): double-submit token. `apiFetch()` (`api.js:206`) is a `fetch` superset — sends `credentials:'same-origin'`, attaches `X-CSRF-Token` on unsafe methods (POST/PUT/DELETE/PATCH), mints the token via `GET /api/csrf` (cached in module-level `_csrfToken`), and fires an injectable `_onUnauthorized` handler on 401. `setOnUnauthorized(fn)` (`api.js:161`) is the injection seam — the React shell wires it (e.g. redirect-to-login) instead of `main.js`. `__resetCsrfCache()` is a test seam.
- Degrades gracefully: no cookie + `/api/csrf` unavailable → request still goes through without the header (server only enforces CSRF when auth is on). GET flows are byte-identical to plain fetch.
- These are async functions returning Promises — they map cleanly onto React Query / SWR / plain `useEffect`+`await`. No change required.

### Endpoint adapters (the full `/api/*` surface this layer touches)
`sessionQuery(id)` (`api.js:233`) → `?session_id=<id>` or `''`.

| api.js fn | Method + path | Request body | Response shape (verified vs server.py) | Notes |
|-----------|---------------|--------------|----------------------------------------|-------|
| `fetchProviders` (`237`) | GET `/api/providers` | — | `{ providers: {...} }` → returns `.providers` | plain `fetch`, no CSRF |
| `fetchSettings` (`243`) | GET `/api/settings` | — | `SettingsConfig`-ish JSON; token returned as `"***"` when set, `""` when unset (`main.js:129` gates `jiraConfigured` on `jira_server_url && jira_api_token`) | plain fetch |
| `saveSettings` (`248`) | POST `/api/settings` | `SettingsConfig` JSON | `Response` (caller checks `.ok`) | via `apiFetch` (CSRF) |
| `fetchModels` (`256`) | POST `/api/providers/{provider}/models` | `ModelDiscoveryRequest` `{api_key?,base_url?,azure_deployment_name?,azure_api_version?}` | `{ models:[...] }`; throws `Error(data.detail)` on `!res.ok` | via `apiFetch` |
| `testJiraConnection` (`271`) | POST `/api/jira/test` | — | `{success:true,user,server}` or `{success:false,error,error_class}` — never throws server-side | via `apiFetch` |
| `fetchSessions` (`279`) | GET `/api/sessions` | — | **array** of metadata `{run_id, filename, llm_mode, owner_id, created_at}` (server.py:453-482) | plain fetch |
| `fetchTasks` (`284`) | GET `/api/tasks?session_id=` | — | `{ tasks:[ManagedTask...], config:{...}, env_defaults:{jira_project_key, jira_server}, run_id }` (server.py:484-493) | plain fetch → `setTaskData` |
| `fetchStatus` (`289`) | GET `/api/status?session_id=` | — | `ProcessingStatus`: `{is_running, current_step, message, progress, error, error_class, run_id, owner_id, kind, logs:[]}` (server.py:192-206) | plain fetch — the poll payload |
| `postTask` (`294`) | POST `/api/tasks?session_id=` | `TaskUpdate` `{id,title,short_description?,use_case?,acceptance_criteria?[],considerations_constraints?[],deliverables?[],mockup_prototype?,status}` | `Response` (caller checks `.ok`); body `{message, task}` | via `apiFetch` |
| `approveAll` (`302`) | POST `/api/tasks/approve_all?session_id=` | — | `{ message, ... }` (`.json()`) | via `apiFetch` |
| `uploadFile` (`307`) | POST `/api/upload` | `FormData{file}` | `{ filename, ... }` (`.json()`) | via `apiFetch`, multipart |
| `startProcess` (`314`) | POST `/api/process` | `ProcessRequest` `{pdf_filename, llm_mode, jira_hierarchy, jira_project_key, skip_indexing, max_nodes}` | `Response` (checks `.ok`); body `{message, run_id}` (server.py:893) | via `apiFetch` |
| `pushToJira` (`322`) | POST `/api/push?session_id=` | `PushRequest` `{jira_hierarchy, jira_project_key}` | `{success:true, started:true, run_id, message}` (async path, server.py:1161) OR `{success:false, message, results:[]}` (no approved tasks, server.py:1156). **Note: 409 raised if a run is already active** (server.py:1149) | via `apiFetch` |
| `cancelRun` (`331`) | POST `/api/cancel/{run_id}` | — (accepts `AbortSignal`) | `Response` | via `apiFetch`; called best-effort with 3s abort timeout |
| `deleteSession` (`335`) | DELETE `/api/sessions/{run_id}` | — | `Response` (caller checks `.ok`) | via `apiFetch` |

Additional endpoints in the app but NOT wrapped in api.js (called elsewhere, noted for completeness): `GET /api/csrf` (called internally by `apiFetch`), `GET /` (index html), `GET /healthz`, `POST /api/tasks/add` (server.py:934 — the "add task" form; check `modals.js`/`render.js` for its caller, it is not in api.js today). The React port should add a `postAddTask` adapter here to keep the "one home for fetch" invariant.

## 3. polling.js (`ui/src/polling.js`, 234 lines) — THE FILE THAT NEEDS REWRITING

Two concerns entangled:

### 3a. Pure + testable — PORTS UNCHANGED
- `ERROR_CLASS` enum (`polling.js:20`) — mirror of `core/errors.py::ErrorClass` (TRANSIENT/USER_FIXABLE/TERMINAL).
- `errorRecovery(errorClass)` (`polling.js:29`) → `{chipLabel, chipClass, action, actionLabel}`; case-insensitive; unknown→Dismiss. Covered by `polling.test.js`. Keep as-is (a component will consume its return).

### 3b. Imperative DOM + interval — REWRITE AS A HOOK + JSX
- `renderFailureRecovery` (`polling.js:62`), `clearFailureRecovery` (`polling.js:107`), `showProgressOverlay` (`polling.js:122`), `stopPolling` (`polling.js:131`), `startStatusPolling` (`polling.js:141`).
- `startStatusPolling(onComplete, recovery)` is the core loop:
  - `setInterval(..., 1000)` module-level `statusInterval` (`polling.js:11,153`).
  - Each tick: reads `$activeSessionId.get()`, `await fetchStatus(session)`, **stale-response guard** (`polling.js:158`) drops the response if the active session changed mid-request, resets `pollErrorCount`.
  - While running or `progress>=1.0`: mutates DOM directly — `progressStepTitle`, `progressMessage`, `progressBarFill.style.width`, `progressPercentage`, and **incrementally appends log `<p>` nodes** to `logConsole` (only new lines beyond `existingCount`, `polling.js:174-186`) then auto-scrolls.
  - On `!is_running`: `sessionStorage.removeItem('activeSessionId')` (side-channel, see M4), then branches:
    - `status.error` → `stopPolling()`, red message, hide cancel, `renderFailureRecovery(status.error_class, ...)`.
    - `progress>=1.0` → `stopPolling()`, "Complete!", `showToast(...)`, `clearFailureRecovery`, show dismiss, `await onComplete()`.
    - else (idle) → `stopPolling()`.
  - `catch`: increments `pollErrorCount`; at `>=3` shows "Reconnecting…" (`polling.js:224-232`).
- **Dependency-injection seams** (to avoid an import cycle with main.js): `onComplete` (the data-load flow, `main.js` passes `loadData` or `onPushComplete`) and `recovery = {onRetry, onOpenSettings, onDismiss}` (main.js `pollRecovery`, `main.js:107-117`). In React these become hook arguments / callbacks — the cycle-avoidance reason disappears once state flows through the store.

## 4. Full data flow (end-to-end)

**Upload → extract → poll → review:**
1. User drops PDF → `main.js` `acceptFile` (local `selectedFile`).
2. Start → `startExtraction` (`main.js:428`): `uploadFile` → `{filename}` → build `ProcessRequest` from DOM inputs → `startProcess` → `{run_id}` → `setActiveSession(run_id)` + `setLastViewed` → `showProgressOverlay` → `loadSessions` → `startStatusPolling(loadData, pollRecovery)`.
3. Poll ticks (`polling.js`) mutate the overlay DOM until `!is_running`.
4. On complete → `onComplete = loadData` (`main.js:468`): `fetchTasks(activeSessionId)` → `setTaskData(data)` → hydrate config inputs from `data.config`/`data.env_defaults` → `updateStats` + `renderTasks` + `updatePushPreflight` + `maybeRenderPushResults`.

**Review/edit → save:** card edit → `saveTask` (`main.js:546`) → `postTask` → on ok `patchTaskCard` (optimistic in-place) + `updatePushPreflight`.

**Approve all:** confirm → `approveAll` → `loadData` (full reload).

**Push:** `pushPreflight` gate → confirm → `doPush` (`main.js:617`): `pushToJira` → `{started, run_id}` → `setActiveSession` → overlay → `startStatusPolling(onPushComplete, pollRecovery)`. `onPushComplete` (`main.js:680`) re-fetches status, parses `"N failed"` from `status.message` into `lastPushFailure`, then `loadData` → `maybeRenderPushResults`.

**Session switch:** `switchToSession(id)` (`main.js:346`) → `setActiveSession` + `setLastViewed` → `fetchStatus` → `decideSessionSwitch`: `'resume'` re-opens overlay + polls; `'load'` → `loadData`.

**Cancel:** immediate UI teardown (`stopPolling`, hide overlay, `clearActiveSession`, `resetToNewSession`) FIRST, then best-effort `cancelRun` with a 3s abort (`main.js:242-266`) — never blocks on the network.

**Resume:** `visibilitychange` (`main.js:752`) and `autoResumeSession` (`main.js:770`, from `sessionStorage`) + last-viewed fallback (`main.js:800`) restart polling on refresh/focus, recovering `lastOp` from `status.kind`.

## 5. Which subscribers read the atoms
- `main.js` reads via `.get()` (imperative) — `$activeSessionId.get()` everywhere, `$taskData.get().tasks` via `currentTasks()`.
- `polling.js` reads `$activeSessionId.get()` + `$pollStatus.get()`.
- `render.js` (not in scope but confirmed importer) consumes `$taskData` for rendering.
- **No component currently `.subscribe()`s** — everything is manual `.get()` + explicit re-render calls (`renderTasks`, `updateStats`). This is exactly the fan-out that `@nanostores/react`'s `useStore` replaces: subscribing components re-render automatically, deleting the manual `renderTasks(saveTask)` calls scattered through main.js.

---

## Migration map (port-unchanged vs adapt)

- **PORTS UNCHANGED:** `state.js` (all 3 atoms + actions — just add `@nanostores/react`'s `useStore`), `api.js` in full (pure helpers + `apiFetch` + all adapters + CSRF + `setOnUnauthorized` injection seam), and the pure parts of `polling.js` (`ERROR_CLASS`, `errorRecovery`).
- **NEEDS ADAPTING:** everything DOM/interval in `polling.js` → a `useStatusPolling` hook + a `<ProgressOverlay>` component driven by a `$pollProgress`-style atom.
