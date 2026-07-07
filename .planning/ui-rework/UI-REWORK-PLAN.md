# SOW-to-Jira UI Rework Plan — React + Vite migration + full UX revamp

Status: DRAFT for review. Read-only planning artifact. No code changed.
Author: lead architect + designer synthesis, grounded in the six inventory files under `.planning/ui-rework/inventory/`.
Backend is out of scope and untouched: the frozen `/api/*` contract in `.planning/ui-rework/inventory/backend-contract.md` is the migration boundary.

---

## 1. Executive summary, goals, non-goals

### 1.1 What this is

We are migrating the SOW-to-Jira review UI from a hand-written vanilla-JS + Vite + nanostores app to React 19 + Vite, served by the *same* FastAPI `/static` mount, keeping `api.js` and the nanostores atoms intact, and layering Tailwind v4 + shadcn/Radix primitives + beUI motion + Framer Motion. Alongside the framework move we do a full UI/UX revamp so the app reads like a serious document-review work tool rather than the current generic dark dev-console.

The current build is, by its own inventory's admission, the textbook AI-slop reflex: `oklch(15% 0 0)` near-black canvas + vibrant orange accent + Space Grotesk (`styles.css:26-39,5`). We reject that reflex deliberately.

### 1.2 Recommended direction (see §5 for the full spec and alternatives)

Ship **Drafting Table** light-first: a warm-paper document-review surface, one committed clay-amber marker accent, rows-on-a-sheet instead of nested cards, dial-able density (Comfortable / Cozy / Compact), and a non-blocking inline run dock. We graft in the best moves from the two runner-up directions: proof-reader edge-mark color discipline and ink-density confidence from The Reading Room; ledger virtualization, a Cmd-K command palette, the `--signal-*` accent-lint rule, and the exact `error_class` recovery rendering from PLATEN. The two dark modes are deferred v2 inversions, not defaults.

### 1.3 Goals

1. Replace the imperative DOM/`innerHTML`/template-clone render layer with a declarative React component tree, deleting the fragile plumbing (`patchTaskCard`, `_triage`/`_recoveryAction`/`_recoveryBound` node-stashing, manual `renderTasks()` re-render calls, hand-rolled modal focus-trap).
2. Keep the data layer verbatim: 3 nanostores atoms (`state.js`), the whole of `api.js` (CSRF, `apiFetch`, all endpoint adapters, pure view-model helpers), and the pure poll helpers (`ERROR_CLASS`, `errorRecovery`).
3. Land a distinctive, defensible visual identity that serves the work (density control and reading flow over decoration) and passes the AI-slop test at both altitudes.
4. Make long-running work non-blocking: the extraction/push overlay becomes an inline, dismissible run dock so users can keep reviewing while a job streams in.
5. Make the design system a Tailwind v4 `@theme` token set so shadcn / Radix / beUI / Framer Motion are genuinely drop-in.
6. Preserve every accessibility and security behavior the vanilla codebase already got right (WCAG 1.4.1 text-not-color status labels, `javascript:`-scheme href guards, `escapeHtml` at trust boundaries, CSRF double-submit, 401 handling). Treat the existing vitest suites as the migration contract.

### 1.4 Non-goals (this rework)

- No backend/API changes. `ui/server.py` and every `/api/*` route stay byte-identical. Two data-model gaps are flagged for a *future* backend ticket, not this rework: (a) per-task push-failure detail is not persisted (only an aggregate rides `status.message`), and (b) `current_step` is a bare integer with no phase name. We design around both; we do not fix them here.
- No dark mode in v1. Both "lamp-off" and "dim-desk" dark variants are explicitly deferred.
- No auth/multi-user work. The app runs auth-off single-user by default; we keep the CSRF/401 seams live so auth-on keeps working, but we do not build login UI.
- No new pipeline features. In-place list editors, cross-task search, bulk multi-select, phase-named progress, and elapsed timers are called out as opportunities and phased, but only the ones listed in §4 are committed.

---

## 2. Current-state map (from the inventory)

### 2.1 Component inventory (what exists, coupling, migration hazard)

Source: `inventory/view-layer.md`. There is **no component tree today** — `main.js` is a single `DOMContentLoaded` IIFE with all handlers plus module-local mutable state; rendering is imperative `document.createElement` trees and `<template>` clones. Most view state lives in the DOM or in `main.js` closure vars, not in the store. That is the single biggest migration hazard.

| Region (inferred component) | Source | Coupling | React target |
|---|---|---|---|
| AppShell / bootstrap orchestration | `main.js:793-819` | HIGH (god module) | `<App>` root; bootstrap/resume/visibility as `useEffect`s; `isOverlayOpen()` DOM-scan -> modal-stack state |
| Sidebar container | `index.html:15-139` | LOW | `<Sidebar>` JSX wrapper |
| SessionSwitcher | `main.js:268-329,376-387` | HIGH — `<select>` is the source of truth, hand-synced with `$activeSessionId` in ~5 places | `sessions` array in state + one controlled `value={activeSessionId}` |
| ExtractionConfig + UploadZone | `main.js:158-232,428-491` | HIGH — 4 uncontrolled inputs, imperative `#btnClearFile` createElement, `.drag-over`/`.has-file` class toggles | controlled `config` object + `{selectedFile && <ClearButton/>}` + `isDragging` state |
| MetricsPanel / Overview | `render.js:268-275` | LOW-MED (imperative but trivial); 5 explicit `updateStats()` call sites | derive `computeStats(tasks)` from `useStore($taskData)` |
| Filters + Sort | `render.js:279-311`, `main.js:90-99` | HIGH — canonical filter state lives entirely in DOM checkboxes | `filters` object + `sortDir` state; `visibleTasks = useMemo(...)` |
| GlobalActions (Push/ApproveAll/CollapseAll/Settings) | `main.js:137-149,561-612` | MED-HIGH — `updatePushPreflight` sets `.disabled`/`.title` from ~5 sites; button label = state ("Pushing…") | `pushPreflight` `useMemo` + `isPushing` state |
| TaskList (grouped) | `render.js:298-420` | VERY HIGH — `innerHTML=''` full wipe, template-literal group header, per-group collapse via `style.transform` | `<TaskList>` -> `<TaskGroup>` -> `<TaskCard>`; collapse in state |
| TaskCard (editable) | `index.html:244-303`, `render.js:521-696` | VERY HIGH — template clone + 15 `querySelector`s, 6 uncontrolled fields, `card._triage` stash, imperative classList/style | `<TaskCard task onSave>` with `expanded` + form state; keys replace DOM-id lookup |
| PushChip | `render.js:452-516` | MED — createElement; `jiraBrowseUrl` http/https scheme guard | JSX sub-component; keep scheme guard as util |
| ProgressOverlay | `index.html:144-163`, `polling.js:122-233` | VERY HIGH — full imperative DOM mutation, incremental log-append diff, loader mount, recovery-action stash on DOM node | `<RunDock>` driven by a poll atom; `logs.map(...)` + scroll effect |
| SettingsModal | `index.html:166-228`, `modals.js:423-554` | VERY HIGH — imperative provider option rebuild, `updateProviderUI` show/hide, secret-mask sentinel `'***'` | routed `<SettingsPage>` (see graft); conditional render + controlled inputs |
| Collapse-All (cross-cutting) | `main.js:389-403` | HIGH — reaches into every group/card node's classes | lift to `collapsedGroups` Set + per-card `expanded` |
| ConfirmDialog | `modals.js:212-268` | MED-HIGH — Promise-per-invocation `createElement` overlay | `useConfirm()` hook -> Radix `AlertDialog` portal |
| Toasts | `render.js:120-142` | MED — ~20 call sites; errors persist until click | `sonner`; keep a thin `pushToast()` wrapper so call sites barely change |
| PushResultsPanel | `render.js:181-264` | MED-HIGH — idempotent-by-removal, `insertBefore(taskList)` | conditional `<PushResultsPanel>` above `<TaskList>` |
| beUI loaders (scramble/helix/newton) | `loaders.js`, `styles.css:959-1049` | ports cleanly — already `motion`-driven with reduced-motion fallback | real beUI / `motion/react` components (net code deletion) |

### 2.2 State + data flow

Source: `inventory/state-api.md`. Canonical client state is three nanostores atoms; **most view state is NOT in them** — it is in the DOM or `main.js` locals.

| Atom (`state.js`) | Meaning | Persistence |
|---|---|---|
| `$activeSessionId` | run currently displayed/polled; `null` == new-extraction screen | mirrored to `sessionStorage` via `setActiveSession` |
| `$taskData` | full `/api/tasks` payload `{tasks, config, env_defaults, run_id}` | in-memory |
| `$pollStatus` | poll lifecycle `{isRunning, pollErrorCount}` | in-memory |

State-shaped things that live *outside* the store today and must be promoted (React port): `selectedFile`, `lastOp` (`'extraction'|'push'`), `lastPushRequest`, `jiraConfigured`, `lastPushFailure` (`main.js` locals); `localStorage['lastViewedSessionId']`; and a **side-channel** `sessionStorage.removeItem('activeSessionId')` written directly in `polling.js:190` that the atom never sees (must be folded through `clearActiveSession()`).

End-to-end flow (verified in `state-api.md §4`): upload PDF -> `startExtraction` (`uploadFile` -> `startProcess` -> `{run_id}` -> `setActiveSession` -> overlay + `startStatusPolling(loadData, pollRecovery)`) -> poll ticks mutate overlay DOM until `!is_running` -> `onComplete=loadData` (`fetchTasks` -> `setTaskData` -> render). Session switch probes `GET /api/status` and branches `decideSessionSwitch` -> `'resume'` (reopen overlay + poll) or `'load'`. Push mirrors extraction with `kind:"jira_push"`.

### 2.3 API contract (the frozen boundary)

Source: `inventory/backend-contract.md`. Single FastAPI app, no router/version prefix, all routes under `/api/*`. Static mount: `app.mount("/static", StaticFiles(directory=FRONTEND_DIR))` where `FRONTEND_DIR = ui/dist` if `ui/dist/index.html` exists else `ui/` (`server.py:187-189`). `GET /` returns `ui/dist/index.html` (`server.py:404-406`) and does **not** catch-all other paths — any client-side routing lives entirely in the SPA.

Dual-mode auth (`auth_enabled()`, `server.py:272-284`): auth-OFF default -> fixed `_DefaultUser`, no 401, CSRF not enforced (dev against this); auth-ON -> real per-user, 401 on bad `sow_session` cookie, data routes 404 on cross-user, CSRF enforced. CSRF is a double-submit: `X-CSRF-Token` header == `sow_csrf` cookie (not HttpOnly) on POST/PUT/DELETE/PATCH. `api.js` `apiFetch` already implements all of this and degrades gracefully — reuse verbatim.

Full route surface (verified vs `server.py`): `GET /api/csrf`, `GET /api/sessions`, `GET /api/tasks`, `GET /api/status` (the 1s poll), `POST /api/cancel/{run_id}`, `DELETE /api/sessions/{run_id}`, `POST /api/upload` (multipart, PDF magic-byte, <=50MB), `GET /api/providers`, `POST /api/providers/{id}/models`, `GET|POST /api/settings` (secrets masked `"***"`), `POST /api/process` -> `{run_id}`, `POST /api/tasks`, `POST /api/tasks/add` (not yet wrapped in `api.js` — add a `postAddTask` adapter), `POST /api/tasks/approve_all`, `POST /api/push` (409 if a run is active), `POST /api/jira/test` (never throws; classified JSON). Poll payload `ProcessingStatus` = `{is_running, current_step, message, progress(0..1), error, error_class, run_id, owner_id, kind, logs[<=50]}`.

### 2.4 Styles / design system (as-is)

Source: `inventory/styles-system.md`. `ui/styles.css` (1050 lines), Space Grotesk, single dark theme, orange accent. Token duality is the biggest inconsistency: neutrals + accent are OKLCH but status colors are raw Tailwind hex (`#10b981`/`#ef4444`/`#f59e0b`/`#3b82f6`). Worth keeping as domain IP: the semantic status aliases (`--status-*`), error-severity tokens (`--sev-transient/fixable/terminal`) that map to `ErrorClass`, confidence bands (`--conf-low/mid/high`), the glowing status dots, the 3-tier button hierarchy, the WCAG-1.4.1 discipline, the fluid `clamp()` spacing, the first-run de-emphasis, and the beUI loaders. Worth dropping/revising: heavy black shadows (0.5-0.7 alpha smear), hand-rolled modal overlay, custom toast, overloaded `.metric-label`, ad-hoc utility classes (`.btn-mt-8` etc.), spacing token names that collide with Tailwind numbering, and the single 300ms transition applied even to hovers.

### 2.5 Test coverage (as-is)

Source: `inventory/tests.md`. Runner today: vitest 4.1.9 + jsdom 29 (root `package.json`). **111 tests**: api 30, render 32, loaders 15, modals 14, polling 13, state 7. Split: **58 pure** (survive verbatim — all of `state`+`api`, plus pure helpers in render/polling/loaders) and **53 DOM-render** (need RTL rewrite; several modal focus-trap tests may be *deleted* if shadcn's Dialog owns the behavior).

### 2.6 UX flows + top pain points

Source: `inventory/flows-painpoints.md`. Ranked systemic debt driving the redesign:

1. **Blocking overlays are the #1 debt.** Extraction AND push hijack the whole app with a fixed full-screen modal for minutes; you cannot browse sessions or read prior results. Fix: non-blocking per-session dismissible run surface.
2. **State lives in DOM + closure vars, not a store.** `lastOp`, `lastPushRequest`, `jiraConfigured`, `selectedFile`, expand/collapse, dirty edits, recovery-action — all imperative. Model as explicit state; `@nanostores/react` makes it declarative.
3. **Sidebar is overloaded** — nav + create-form + dashboard + action bar in one 300px column; first-run just *dims* (not hides) Filters + Global Actions.
4. **Review ergonomics thin** — list fields (AC/deliverables/constraints) are newline-joined text blobs, no dirty-state tracking, a toast per save (50 tasks = 50 toasts), full list re-render on any filter change (loses scroll + expand), no cross-task search, no bulk multi-select.
5. **Progress under-informative** — `current_step` is a bare int ("Step 3" not "Deduplicating"), raw text log dump, "Reconnecting…" forever with no give-up state, no elapsed/ETA.
6. **Error copy + recovery** — raw exception strings in a modal, USER_FIXABLE can only "Open Settings" (can't deep-link the bad field), no offline/terminal boundary.
7. **Settings crams LLM + Jira into one modal**, no LLM test-connection, no "Configured ✓" chip, save does not reactively refresh the Push preflight.

Loading/feedback matrix gaps: upload has no progress/spinner; session-switch has two uncoordinated loading signals; settings-load has no loading state.

---

## 3. Target architecture

### 3.1 Stack

- **React 19** + **Vite** (keep the existing `vite.config.js`: `root: 'ui'`, `base: '/static/'`, `outDir: 'dist'`) + `@vitejs/plugin-react`.
- **nanostores kept**, consumed via `@nanostores/react`'s `useStore`. The 3 atoms in `state.js` are the port-invariant layer.
- **`api.js` kept verbatim** — CSRF/`apiFetch`/adapters/pure helpers. Add one adapter: `postAddTask` (for `POST /api/tasks/add`, which is not wrapped today).
- **Tailwind v4** with the design system expressed as `@theme` OKLCH tokens (the design system IS the token set).
- **shadcn/ui on Radix** for the accessible unstyled primitives the app hand-rolled: Dialog/AlertDialog, Collapsible (task/group expand), Select (session/provider/hierarchy), Checkbox (filters), Tabs, Tooltip (push preflight reason), Toast via `sonner`, Command (Cmd-K palette).
- **beUI motion accents + Framer Motion** (`motion` is already a dependency, `motion/react` in React) for the high-impact moments and the retinted loaders.
- **TanStack Virtual** for the task sheet so the Compact density stop stays 60fps at 140+ rows (graft from PLATEN).
- **React Router** for the routed Settings destination (graft from The Reading Room) — the only new routing dep. Backend serves `index.html` only at `/`, so use a `HashRouter` or configure the SPA to render Settings as an in-app route without needing a server catch-all (see §3.5).

### 3.2 Proposed component tree

```
<App>                                  # bootstrap effects, keyboard triage hook, modal stack, router
├─ <CommandPalette/>                   # Cmd-K: session switch / push / approve-all / open-settings  (graft: PLATEN)
├─ <Toaster/>                          # sonner portal; pushToast() wrapper keeps ~20 call sites
├─ Route "/"  <ReviewWorkspace>
│   ├─ <TopMatter>                     # document-header bar: session switch, running tally (typeset, NOT metric-card grid), density dial, Push
│   │   ├─ <SessionSwitcher/>          # Select; controlled value={activeSessionId}
│   │   ├─ <RunningTally/>             # "142 tasks · 88 approved · 12 to push" — derived useMemo(computeStats)
│   │   ├─ <DensityDial/>              # Comfortable | Cozy | Compact  (persisted atom)
│   │   └─ <PushAction/>               # single accent CTA; disabled+Tooltip from pushPreflight
│   ├─ <NewExtractionPanel/>           # transient: upload zone + config; shown only when starting a run
│   ├─ <RunDock/>                      # inline, non-blocking, dismissible  (replaces full-screen overlay)
│   │   ├─ <RunLoader/>                # beUI scramble/helix/newton, retinted; motion/react
│   │   ├─ <RunProgress/>              # step name + baseline % rule + elapsed
│   │   ├─ <LogConsole/>               # logs.map(); scroll-to-bottom effect; aria-live
│   │   └─ <FailureRecovery/>          # error_class -> severity chip + Retry/OpenSettings/Dismiss  (graft: PLATEN)
│   ├─ <FilterToolbar/>                # controlled checkboxes + sort Select; filters+sortDir state
│   ├─ <PushResultsPanel/>             # conditional, above the sheet
│   └─ <TaskSheet>                     # virtualized (TanStack Virtual)
│       └─ <TaskGroup>                 # SOW section = document part; Fraunces running-head + hairline rule (graft: Reading Room)
│           └─ <TaskRow>               # row-on-a-sheet, not a card; status = edge mark; Conf = ink-density bar + numeric
│               └─ <TaskRowEditor/>    # inline expand (Collapsible); 6 fields; Save/Approve/Reject
├─ Route "/settings"  <SettingsPage>   # routed destination, NOT a modal  (graft: Reading Room)
│   ├─ <LlmSettingsSection/>           # provider Select, Fetch Models, Test LLM, Azure conditionals, OpenRouter helper
│   └─ <JiraSettingsSection/>          # server/token, Test Connection, "Configured ✓" chip
└─ <ConfirmDialog/> (Radix AlertDialog)# only justified modal: destructive session delete + push confirm
```

Notes:
- `patchTaskCard`, `card._triage`, `_recoveryAction`/`_recoveryBound`, `isOverlayOpen()` DOM-scans, and all `innerHTML=''` wipes disappear — React reconciliation + keys make them unnecessary.
- The hand-rolled `openModal`/`closeModal`/`trapFocus`/`modalStack` machinery in `modals.js:32-203` is deleted; Radix Dialog owns focus-trap/Escape/scroll-lock.

### 3.3 State strategy

- **Keep the 3 atoms** (`$activeSessionId`, `$taskData`, `$pollStatus`) and read via `useStore`. Subscribing components re-render automatically — this deletes every scattered explicit `renderTasks(saveTask)`/`updateStats()` call in `main.js`.
- **Promote to atoms (or a persistent-atom):** `$lastViewedSessionId` (persist to `localStorage`), and a run/UI context for `selectedFile`, `lastOp`, `lastPushRequest`, `jiraConfigured`, `lastPushFailure`, `filters`, `sortDir`, `density`, `collapsedGroups`.
- **Model the session lifecycle as an explicit machine** (`idle -> uploading -> running -> review -> pushing -> done|error`) instead of the current closure-var op-recovery dance spread across `switchToSession`/`autoResumeSession`/`visibilitychange`.
- **Fold the `sessionStorage` side-channel** (`polling.js:190`) back through `clearActiveSession()` so the store stays canonical.
- **Polling becomes `useStatusPolling(sessionId)`** — a hook writing to `$pollStatus` (and a `$pollProgress`-style atom for step/message/logs/progress). The DI seams (`onComplete`, `recovery`) that existed to dodge an import cycle become effects reacting to poll state; the cycle-avoidance reason disappears. Keep the stale-response guard (`$activeSessionId.get() !== polledSession`) as a captured-ref check. Consider visibility-aware pause + backoff/jitter and a hard give-up state (fixes the "Reconnecting… forever" pain).

### 3.4 Build + where `ui/dist` and `/static` fit

- Vite input stays `ui/index.html`, which will now load `ui/src/main.jsx` (React entry). Output stays `ui/dist` with `base: '/static/'` so hashed asset URLs are `/static/assets/...` exactly as FastAPI expects (verified: current `ui/dist/index.html` references `/static/assets/index-*.js`).
- `make ui-build` (`npm run build`) still emits `ui/dist`; `make ui` builds then runs uvicorn; FastAPI's `FRONTEND_DIR` auto-detection (`server.py:188`) requires no change.
- **Fix the dev proxy** (`vite.config.js` currently proxies only `/api`): add `/static`, `/healthz`, and `/` to the proxy per `backend-contract.md §2`, so the `sow_csrf` cookie + same-site origin flow works in `make ui-dev`. This is the one build-config change the migration requires.
- Self-host the chosen fonts (drop the Google Fonts `<link>` at `index.html:8-10`) — variable subsets, `font-display: swap`. Removes a render-blocking third-party request and matches the new type system.

### 3.5 Routing note

Backend `GET /` serves `index.html` and does not catch-all. Two options: (a) `HashRouter` (`/#/settings`) — zero backend change, safest; (b) `BrowserRouter` with a small FastAPI catch-all added later. Recommend **(a) HashRouter for v1** to honor the "backend untouched" non-goal; revisit if deep-linkable clean URLs become a requirement.

---

## 4. Migration strategy + phasing

### 4.1 Islands (strangler) vs clean rebuild — recommendation

**Recommend a clean rebuild of the view layer behind a preserved data core, executed as vertically-shippable phases** — not React-islands.

Why not islands: the current app is a single `DOMContentLoaded` IIFE whose "components" communicate through shared DOM (checkbox `.checked`, `<select>.value`, `card.classList`, `card._triage`) and module-scope closure vars. There is no clean seam to mount a React island against without React and vanilla both fighting over the same DOM nodes and the same non-store state. The strangler pattern shines when modules already have boundaries; here they do not (`view-layer.md` calls `main.js` "the god-module").

Why it is still low-risk: the *data core* (`state.js` + `api.js` + pure poll helpers) is already cleanly separated and ports unchanged, and 52% of the test suite is pure and stays green. So we get the strangler's safety (a stable, tested core) without its cost (dual-DOM coordination). The rebuild is of the *render layer only*.

Backend/API is untouched throughout every phase. `api.js` is the contract; the vitest api/state suites prove it.

### 4.2 Phased sequence

Each phase is independently shippable. The visual identity (warm-paper, row-not-card, inline run dock) lands early; the higher-effort grafts (in-place list editors, virtualization, routed Settings) come later.

**Phase 0 — Tooling + core-port proof (de-risk).**
- Add `@vitejs/plugin-react`, React 19, Tailwind v4, `@testing-library/react` + `user-event` + `jest-dom`; wire the `@vitejs/plugin-react` plugin and a vitest `environment: jsdom`/`globals`/setup-file block. Fix the dev proxy (§3.4).
- Extract the ~21 pure helpers (`escapeHtml`, `resolveTriageAction`, `isTypingTarget`, `sortTasksByConfidence`, `errorRecovery`, loader factory contract) into a `lib/`/viewmodel module, keeping their tests verbatim.
- Run `state.test.js` + `api.test.js` (37 tests) green against the ported core on day one.
- **Ships:** nothing user-visible; a green core + build.
- **Risk:** LOW. If the pure suites are not green, stop — the core is not port-invariant.

**Phase 1 — Shell + identity + review sheet (the visual payoff).**
- Tailwind `@theme` token set (§5.2). `<App>`, `<Sidebar>`/`<TopMatter>`, `<SessionSwitcher>`, `<FilterToolbar>`, `<RunningTally>`, `<TaskSheet>` -> `<TaskGroup>` -> `<TaskRow>` with the row-on-a-sheet layout, edge-mark status, ink-density confidence. Keep labeled form inputs for now (Reading Room's own tradeoff #3: defer contenteditable). Keyboard triage hook (`j/k/a/r/e`) + a visible legend strip.
- Reuse `computeStats`, `shouldShowTask`, `sortTasksByConfidence`, `statusDisplay`, hierarchy labels from `api.js`.
- **Ships:** the warm-paper review identity is live for the load/review/edit/approve loop. This is the "wow" moment.
- **Risk:** MED. The `<TaskRow>` and its accordion editor are the migration centerpiece; port the render/RTL tests for TaskCard/PushChip/PushResults as the contract.

**Phase 2 — Non-blocking run dock + polling hook (kill the #1 pain).**
- `useStatusPolling` hook + `$pollProgress` atom; `<RunDock>` inline dock with `<RunLoader>` (retinted beUI), `<RunProgress>`, `<LogConsole>`, and `<FailureRecovery>` using the exact `errorRecovery` mapping (graft: PLATEN) — severity chip is a TEXT label (Retryable / Check credentials / Failed) + class-specific action (Retry / Open Settings deep-link / Dismiss), never color-only.
- Because the dock is inline and the atoms fan out, already-extracted tasks render live beneath it while later ones stream in.
- Fold the `sessionStorage` side-channel through `clearActiveSession()`; add hard give-up + visibility-pause.
- **Ships:** users can review while a job runs; declarative error recovery.
- **Risk:** MED. Streaming-in tasks is a store update, not new plumbing, but the run/push/resume lifecycle needs the explicit state machine to be correct. Port the 7 polling DOM tests as RTL `<FailureRecovery>` tests.

**Phase 3 — Dialogs, toasts, push results, command palette.**
- Radix `AlertDialog` for destructive delete + push confirm (`useConfirm()` hook). `sonner` Toaster + `pushToast()` wrapper. `<PushResultsPanel>` conditional above the sheet. Cmd-K `<CommandPalette>` (graft: PLATEN) for session switch / push / approve-all / open-settings.
- **Ships:** the confirm/toast/push-results/palette layer; delete the hand-rolled modal a11y machinery.
- **Risk:** LOW-MED. Decide per-test: rewrite as RTL vs delete-and-trust-shadcn for the 14 modal focus-trap tests.

**Phase 4 — Routed Settings (graft: Reading Room) + LLM test-connection.**
- HashRouter; `<SettingsPage>` at `/settings` with `<LlmSettingsSection>` + `<JiraSettingsSection>`, provider Select, debounced Fetch Models, Test Connection (Jira) + a new Test LLM affordance, Azure conditionals, OpenRouter helper, "Configured ✓" chips, secret-mask sentinel preserved. Save reactively refreshes `jiraConfigured` -> Push preflight (fixes the lag pain).
- **Ships:** Settings as a real destination; the LLM-key-validation loop closes.
- **Risk:** MED. New router dep; careful with the `'***'` sentinel (never render it as a real value).

**Phase 5 — Density dial + virtualization + review ergonomics (graft: PLATEN + Reading Room).**
- `<DensityDial>` (Comfortable/Cozy/Compact, persisted atom) driving row padding + default-collapse. TanStack Virtual on `<TaskSheet>` so Compact stays 60fps at 140+ rows. Then the deferred high-effort items: in-place list editors for AC/deliverables/constraints, dirty-state tracking, and the ink-underline approve motion.
- **Ships:** the dial-able-density promise made real; scan-fast Compact; richer editing.
- **Risk:** MED-HIGH. Virtualization interacts with expand/collapse and keyboard focus navigation — test the focused-row scroll behavior explicitly.

**Deferred to v2 (not this rework):** both dark modes; cross-task search; bulk multi-select + batch approve/reject; phase-named progress + elapsed/ETA (needs backend `current_step` naming); per-task push-failure detail (needs backend persistence).

### 4.3 Risk-per-phase summary

| Phase | Ships | Risk | Primary hazard |
|---|---|---|---|
| 0 | green core + build | LOW | pure suites must be green first |
| 1 | review identity + sheet | MED | `<TaskRow>` accordion is the centerpiece |
| 2 | non-blocking run dock | MED | run/push/resume lifecycle machine |
| 3 | dialogs/toasts/palette | LOW-MED | modal-test rewrite vs delete decision |
| 4 | routed Settings | MED | new router; secret-mask sentinel |
| 5 | density + virtualization | MED-HIGH | virtual + expand/collapse + focus |

---

## 5. UI/UX design direction

### 5.1 Recommended: Drafting Table (with grafts)

**Register:** PRODUCT — a serious internal document-review tool for PM / delivery / dev users doing hours of repeated triage. Design serves the task: density control and reading flow beat decoration, but the surface is distinctive, not generic-admin-panel.

**Scene sentence (forces the light choice):** "Late morning in a quiet architecture studio: a wide oak drafting table by a north-facing window, soft diffuse daylight on warm paper, a single amber-tipped marker resting on the sheet, no glare and no hum — the kind of light you can read contracts under for three hours without your eyes tiring." The product is hours of reading and correcting LLM-extracted prose; it must read like reviewing a document under daylight, not staring into a console. That is exactly the reflex the current dark build embodies and we reject.

**One-liner:** A warm-paper reviewing surface where SOW clauses become Jira drafts you mark up like a document, not a dashboard you monitor.

#### 5.2 Color — OKLCH, RESTRAINED strategy (one committed accent)

Every neutral is tinted toward the warm brand hue so nothing is pure `#000`/`#fff` (the current build literally uses `#ffffff`/`#a3a3a3`, banned here). Status colors are the only saturated pigments and behave like proof marks on edges/dots/underlines, never as fills behind prose (graft: Reading Room's proof-reader discipline).

```
/* Paper substrate (warm, never blue-gray) */
--bg-canvas:  oklch(0.982 0.006 75);   /* app background */
--bg-raised:  oklch(0.965 0.008 75);   /* the review sheet */
--bg-sunk:    oklch(0.945 0.010 72);   /* wells / inputs */

/* Ink (brown-tinted so text sits ON the paper, never punched through) */
--ink-900:    oklch(0.28 0.02 65);     /* body + headings */
--ink-600:    oklch(0.50 0.02 65);     /* secondary */
--ink-400:    oklch(0.66 0.015 65);    /* meta / source refs */
--rule:       oklch(0.90 0.010 72);    /* hairline rules */

/* THE committed accent = a single amber/ochre marker (clay, not neon SaaS orange) */
--signal:        oklch(0.68 0.15 62);  /* focus ring, active-row rail, run progress fill */
--signal-strong: oklch(0.60 0.16 60);  /* the ONE primary button (Push) */

/* Status — semantic, muted into the paper world; WCAG-labeled, never color-only */
--status-approved: oklch(0.58 0.11 145);  /* olive-green ink */
--status-rejected: oklch(0.58 0.15 28);   /* clay-red */
--status-pending:  var(--signal);          /* pending IS "needs your marker" */
--status-pushed:   oklch(0.55 0.08 250);   /* calm ink-blue; shipped recedes */

/* Error severity (maps ErrorClass) — TEXT labels carry meaning, color reinforces */
--sev-transient: var(--status-pending);
--sev-fixable:   var(--signal-strong);
--sev-terminal:  var(--status-rejected);
```

Confidence renders as a marginal ink-density bar whose opacity tracks the value, alongside the existing numeric `Conf 62%` label (graft: Reading Room) — not a colored pill grid. Confidence bands still map: low=clay, mid=amber, high=olive.

**Accent-lint rule (graft: PLATEN):** accent tokens are named `--signal-*`, reserved for (a) the one CTA per surface, (b) live run state (progress fill), and (c) the focused-row rail. Never `--brand-*`. This prevents amber creeping in as decoration as features grow.

#### 5.3 Typography

Replace Space Grotesk (a geometric-grotesk UI reflex). Self-hosted variable fonts.

| Role | Family | Notes |
|---|---|---|
| Display / section running-heads / run-complete moment | **Fraunces** (variable, optical-size + soft italic) | high-contrast old-style serif; document masthead + studio nameplate; italic eyebrow for "Epic/Story/Section" labels (graft: Reading Room keeps Fraunces on the `source_refs[0].section_title` running-heads) |
| Body / editable task prose / data | **Inter** (text optical size) | legibility at 14-15px across the 6 `ManagedTask` fields; body capped at 68ch |
| Mono | **IBM Plex Mono** | run log console, Jira issue keys, page-range source refs, confidence % (`tabular-nums`) |

Scale ratio **1.333** (perfect fourth, >1.25): 13 / 14 / 16 (body) / 18 / 24 / 32 / 43px. Body 16px / 1.6 line-height, 68ch measure. Hierarchy comes from serif-vs-sans + size + weight (400 body vs ~600 headings), not from color or boxes.

#### 5.4 Layout + density

Kill the app-shell-with-sidebar-of-cards pattern. The screen is a document review surface: a slim quiet RAIL (session switcher, running tally as a typeset strip not a metric-card grid, density dial, Settings link, Push); a center **review SHEET** — one continuous warm-paper column (~68-88ch) where SOW sections are document parts with Fraunces running-heads separated by a single hairline rule and generous 32-64px vertical space (graft: Reading Room). Tasks are ROWS on the sheet, not nested cards-in-cards; the focused row carries the amber marker rail on its left edge (a *functional* focus indicator that moves with `j/k`, NOT the banned decorative side-stripe on every card). Expanding a row reveals the editor inline on the same paper plane (no floating card, no shadow-md lift). Config for a new extraction is a transient panel shown only when starting a run, not permanent chrome. **Density is dial-able** (Comfortable/Cozy/Compact) — the promise a serious work tool lives on: Compact scans 140 rows fast, Comfortable edits clauses carefully.

Explicitly avoids the bans: identical card grids, nested cards, side-stripe accent borders, one big wrapping container, glassmorphism, gradient text, the hero-metric block, and modal-as-first-thought.

#### 5.5 Motion

Ease-out only (quart/quint/expo; `cubic-bezier(0.22,1,0.36,1)`), 180-260ms, no bounce/elastic, animate transform + opacity only (never width/height/top/left). Spend the budget on high-impact moments, not scattered micro-interactions:

1. **The marker stroke (signature):** approve/reject draws a single amber (approve->olive) marker underline left-to-right across the row title in ~220ms ease-out via Framer `useAnimate`, then the row settles into its status color. This is THE interaction — triage is the core repeated action; keep it <=220ms and respect `prefers-reduced-motion` (fall back to instant color set, reusing the existing reduced-motion discipline).
2. **Run/indexing:** a hairline scan line eases down the sheet with the step name typeset large; the beUI scramble loader suits the "scanning text" indexing metaphor (retinted to amber-on-paper).
3. **Push success:** pushed entries get their Jira key stamped in as an of-record margin mark; the run dock does one calm settle to 100%.

Everything else (hover/focus/filter) is a quiet ~120ms opacity/color shift. Row expand/collapse uses the grid-template-rows `0fr->1fr` technique (already proven, not a height animation), retuned to ~260ms. beUI loaders are net code deletion — swap the vanilla `motion.animate()` port for `motion/react`.

#### 5.6 The three key screens, reworked

**Review Workspace (core).** Rail: session switcher, running tally strip, density dial, Settings link, single amber Push. Sheet: SOW sections as Fraunces running-heads with hairline rules; each task a ruled row — muted status dot + text status word (WCAG discipline kept) + Inter title + tabular `Conf 62%` with an ink-density bar + flag pills; focused row carries the amber marker rail and responds to `a/r/e/j/k`; expanding opens the 6 fields inline (title, short_description, use_case, acceptance_criteria, considerations_constraints, deliverables) as a ruled sub-form with Save/Approve/Reject. Approve fires the marker stroke. Push gated by `pushPreflight` with the reason surfaced as Tooltip helper text (not a dead disabled button).

**Run / Progress (inline, non-blocking).** The moment `/api/process` starts, a run dock docks at the top of the sheet (NOT the current blocking overlay). It shows the retinted beUI loader, the current step + streaming message, an amber progress fill, an expandable IBM-Plex-Mono log console (`status.logs`, `aria-live`), and — because it is inline — already-extracted tasks render live beneath while later ones stream in. Failure uses the `error_class` mapping: a text severity chip (Retryable / Check credentials / Failed) + the class-specific primary action (Retry / Open Settings deep-link / Dismiss). Ghost Cancel. This is the one screen the motion budget is spent.

**Settings (routed page, not a modal).** `<SettingsPage>` at `/settings`: a single ~68ch column on paper with generous spacing, Fraunces section headings (Language Model, Jira Connection); provider/model Selects with inline Fetch Models + Test Connection + a new Test LLM, password fields, Azure conditional fields, OpenRouter helper. The only justified modal is a small centered Radix AlertDialog for destructive session delete (and push confirm).

#### 5.7 AI-slop test (passes both altitudes)

- **Altitude 1 (category reflex):** "dev/PM tool" reflexively yields dark + blue/charcoal + a grotesque — literally today's build. We commit to the opposite: light warm-paper, editorial serif, one clay-amber marker. Theme + palette are NOT guessable from the category.
- **Altitude 2 (anti-reflex trap):** the trendy escape from SaaS-dark is terminal-green-on-black or brutalist all-mono. We reject both (no mono-as-primary, no green terminal). The commitment is a drafting-studio/manuscript metaphor *derived from the data model* — the `ManagedTask` prose fields are genuinely meant to be read and marked up. Defensible from the domain, not from a trend.

### 5.8 Alternative directions (for the user to choose)

The three directions share the same courageous thesis (reject dark-dev-tool reflex; light warm-paper; one warm accent; kill the card grid; non-blocking run surface). The pick is about register and shippability. Adjudication scores (fit / craft / feasibility / slop-resistance):

| Direction | Scores | One-line register | Pick if… |
|---|---|---|---|
| **Drafting Table** (recommended) | 9 / 8 / 8 / 8 | calm warm-paper focus tool; dial-able density; inline run dock | you want the best-balanced, most-shippable strong identity for hours of dual-mode (triage + edit) work |
| **The Reading Room** (alt A) | 8 / 9 / 6 / 9 | manuscript margin-annotation; 68ch serif reading column; proof-reader marks in a right margin | you prize the most distinctive craft and will accept contenteditable + right-margin-collapse + router cost up front |
| **PLATEN** (alt B) | 8 / 8 / 6 / 9 | light instrument-panel operator console; mono readouts; ruled ledger; sodium-amber = live signal only | your users are throughput-first keyboard operators triaging hundreds of rows and want the densest ledger |

**Why Drafting Table wins (adjudication rationale):** best-balanced expression of the shared thesis for this audience; the dial-able density directly serves both fast triage and careful clause-editing (the single feature most aligned with "density control beats decoration"); the inline non-blocking run dock leans on the existing nanostores fan-out (more correct UX, less new plumbing); and it did the most rigorous migration-contract thinking (port CSRF + `escapeHtml` + WCAG labels unchanged, treat vitest suites as the contract). It sits just below Reading Room on craft and slop-resistance but is meaningfully more feasible (no contenteditable-first, no ledger-virtualization-as-prerequisite). We grafted Reading Room's best moves (edge-mark color discipline, ink-density confidence, Fraunces running-heads, routed Settings) and PLATEN's highest-leverage lower-risk ideas (virtualization for Compact, Cmd-K palette + keyboard legend, `--signal-*` lint rule, exact `error_class` recovery rendering) onto the recommendation to raise its ceiling without taking on the others' up-front risk.

If the user prefers **Reading Room**, the main deltas are: adopt the right-margin annotation zone + in-place contenteditable prose (defer per its own tradeoff #3 if scope tightens), and a 68ch serif *reading* column optimized for careful single-entry reading over throughput scanning. If **PLATEN**, the main deltas are: mono display/headings (Departure/Commit Mono), a three-zone rail/ledger/right-inspector shell with Settings as a right Sheet, TanStack Virtual as a phase-1 prerequisite (not a phase-5 add), and cool slate-blue reserved for PUSHED so shipped items recede.

---

## 6. Component library + where beUI / loaders slot in

- **Base:** Tailwind v4 `@theme` OKLCH tokens (§5.2) are the design system. shadcn/Radix + beUI + Framer Motion are all drop-in on this token base — the stated migration target.
- **shadcn/Radix maps 1:1 onto the accessibility scaffolding the team hand-rolled:** Dialog/AlertDialog (replaces `modals.js` focus-trap + `showConfirm`), Collapsible (replaces the `role=button`+`aria-expanded` task/group disclosure), Select (session/provider/hierarchy), Checkbox (filters), Tabs, Tooltip (the `pushPreflight` reason), Command (Cmd-K), and `sonner` for toasts (stacking/queue/dismiss for free). Restyle hard toward the editorial warm-paper look so they never read as default shadcn. The 3-tier button semantics map to shadcn `Button` variants (`btn-primary->default`, `secondary->secondary`, `ghost->ghost`, `danger->destructive`, `outline->outline`, `btn-icon->size="icon"`); keep the 44px min touch target.
- **beUI + loaders — this dissolves the current loader friction.** The three beUI loaders (scramble/helix/newton) exist today as vanilla ports driven by the already-installed `motion` package with a `prefers-reduced-motion` fallback. Once React lands, `shadcn add @beui/loader` (and use `motion/react`) replaces the hand-port — a **net code deletion**, not a rebuild. beUI supplies the motion accents (retinted amber-on-paper); Framer Motion drives the two high-impact moments (marker stroke on approve, run-complete settle) and the `useAnimate` ink-underline. The scramble loader is kept for the indexing metaphor.
- **TanStack Virtual** for the `<TaskSheet>` so Compact density stays 60fps at 140+ rows (phase 5).

---

## 7. Test strategy

Source: `inventory/tests.md`. Keep the runner (vitest + jsdom); add `@testing-library/react` + `@testing-library/user-event` + `@testing-library/jest-dom` and a vitest setup file (`environment: jsdom`, `globals: true`, `afterEach(cleanup)`).

| File | Tests | Survive as-is | RTL rewrite | Notes |
|---|---:|---:|---:|---|
| state | 7 | 7 | 0 | store is the port-invariant layer |
| api | 30 | 30 | 0 | pure view-model + fetch/CSRF/401 wrapper — kept |
| polling | 13 | 6 | 7 | 6 pure `errorRecovery`; 7 -> `<FailureRecovery>` RTL |
| modals | 14 | 0 | 14 | several focus-trap/`aria-modal`/backdrop tests may be **deleted** (trust Radix) |
| render | 32 | 12 | 20 | heaviest; `escapeHtml`/`resolveTriageAction`/`isTypingTarget`/`sortTasksByConfidence` stay pure; 20 -> `<TaskCard>`/`<PushChip>`/`<PushResultsPanel>` RTL |
| loaders | 15 | 3 | 12 | wrap-don't-rewrite short-term: mount the imperative handle in a `useEffect`, keep `vi.mock('motion')` + fake-timer tests; rewrite only if loaders become true Framer components |
| **Total** | **111** | **58** | **53** | 52% survive verbatim |

Sequencing: (1) green-for-free 37 (state+api) on day one; (2) extract-and-keep ~21 pure helpers before any JSX; so ~79 of 111 are green before a component exists. (3) Rewrite the ~34 real component tests (TaskCard/PushChip/PushResults/FailureRecovery) with `render()` + `user-event` — replace raw `dispatchEvent(new KeyboardEvent)` with `user-event` which fires the full sequence React expects; a11y assertions (role/aria-expanded/aria-modal) map 1:1 onto `getByRole`. (4) For the 14 modal tests, consciously decide per-test "test our Dialog" (rewrite) vs "trust Radix" (delete). **Preserve the security tests exactly** — XSS-inert section titles, `javascript:`-scheme href guards, `escapeHtml` at trust boundaries are behavior contracts; port unchanged even though React auto-escapes, to lock the href-scheme allowlist.

---

## 8. Effort, risk, and open decisions

### 8.1 Effort (honest, relative — not calendar estimates)

| Area | Effort | Why |
|---|---|---|
| Core port (`state.js`/`api.js`/pure helpers) | XS | ports unchanged; 37+ tests green immediately |
| Tooling (React/Tailwind/RTL/proxy fix) | S | standard React-on-vitest stack; one proxy line + `@theme` tokens |
| Shell + review sheet (Phase 1) | L | `<TaskRow>` accordion + edge-mark/ink-density is the centerpiece; ~20 render tests rewritten |
| Run dock + polling hook (Phase 2) | M-L | `useStatusPolling` + explicit lifecycle machine; 7 polling tests rewritten |
| Dialogs/toasts/palette (Phase 3) | M | mostly library swaps; delete hand-rolled a11y machinery |
| Routed Settings (Phase 4) | M | new router; `updateProviderUI` conditional-render port; secret-mask care |
| Density + virtualization + editors (Phase 5) | M-H | virtual x expand/collapse x focus; in-place list editors are new UX |
| Motion (marker stroke + run scan + push stamp) | M | 3 high-impact moments; reduced-motion fallbacks |
| **Net test cost** | M | ~34 genuine rewrites; 58 survive; ~14 modal tests may be deleted |

Overall: a substantial but well-bounded rework. The data core and 52% of tests are free; the cost concentrates in the `<TaskRow>` accordion (Phase 1) and the run lifecycle machine (Phase 2).

### 8.2 Top risks + mitigations

1. **Light theme surprises dark-conditioned users.** Mitigation: warmth genuinely reduces fatigue over multi-hour review (the actual usage); the deferred dark inversion is a v2 follow-on. Ship light-first — committing is the point.
2. **`<TaskRow>` accordion + non-store state promotion** is the biggest behavioral surface (filters, expand/collapse, dirty edits, triage focus). Mitigation: model the session lifecycle as an explicit machine; port the render tests as the contract; promote DOM/closure state to atoms before building UI.
3. **Regressing hard-won a11y/security.** Mitigation: port `api.js` CSRF + `escapeHtml` + WCAG status labels + href-scheme guards UNCHANGED; keep the security tests verbatim.
4. **Marker-stroke motion taxes the core repeated action if slow.** Mitigation: <=220ms ease-out; instant-set under `prefers-reduced-motion`.
5. **Virtualization x expand/collapse x keyboard focus** (Phase 5). Mitigation: test focused-row scroll explicitly; it is a phase-5 add, not a phase-1 prerequisite (unlike PLATEN).
6. **Self-hosted variable fonts add load cost** vs the single Google-Fonts link. Mitigation: subset + variable; Fraunces only on headings/labels so FOUT risk on the Inter body is low; net removes a render-blocking third-party request.

### 8.3 Open decisions for the user

1. **Islands vs clean rebuild** — recommend **clean rebuild of the view layer behind the preserved core** (§4.1). Confirm.
2. **Design direction** — recommend **Drafting Table (light-first, with grafts)**; alternatives Reading Room and PLATEN in §5.8. Confirm which.
3. **Tailwind adoption** — recommend **Tailwind v4 `@theme` OKLCH tokens** as the design system. Confirm (vs keeping bespoke CSS variables without Tailwind).
4. **Fonts** — recommend **Fraunces (display/running-heads) + Inter (body) + IBM Plex Mono (data)**, self-hosted, dropping Space Grotesk + the Google Fonts link. Confirm (Reading Room would use Source Serif 4/Newsreader for body; PLATEN would use a warm humanist mono for headings).
5. **Routing** — recommend **HashRouter for v1** (no backend catch-all needed). Confirm vs BrowserRouter + a future FastAPI catch-all.
6. **Loaders** — recommend **wrap-don't-rewrite short-term** (keep the imperative handle + its 12 tests via a `useEffect` mount) then move to `motion/react` beUI components. Confirm.
7. **Modal tests** — decide the **rewrite-vs-delete** policy for the 14 modal focus-trap tests (trust Radix vs test our wrapper).
8. **Dark mode timing** — confirm **defer both dark modes to v2** (not v1 default).
9. **Backend gaps** — acknowledge that phase-named progress + per-task push-failure detail need a *separate backend ticket*; confirm they stay out of this view-only rework.
