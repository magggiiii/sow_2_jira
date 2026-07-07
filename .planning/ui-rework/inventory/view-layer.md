# View-Layer Component Inventory (React migration)

Scope: `ui/src/main.js` (entry/handlers), `ui/src/render.js` (DOM writes), `ui/src/modals.js` (settings + confirm dialog + a11y layer), `ui/src/polling.js` (status poll + overlay chrome). Supporting: `ui/src/state.js` (nanostores atoms), `ui/src/api.js` (fetch + pure view-model helpers), `ui/src/loaders.js` (motion loaders), `ui/index.html` (static markup + `<template id="taskCardTemplate">`).

Register: PRODUCT. Everything below is a work tool — the migration target is React+Vite with nanostores kept via `@nanostores/react`, `api.js` kept as-is, FastAPI unchanged.

## Architecture as-is (the shape we're porting)

There is **no component tree today** — `main.js` is a single `DOMContentLoaded` IIFE holding all handlers + module-level mutable locals (`selectedFile`, `lastOp`, `lastPushRequest`, `jiraConfigured`, `lastPushFailure`). Rendering is entirely imperative: `render.js` clears `#taskList.innerHTML` and rebuilds `document.createElement` trees, or clones `<template id="taskCardTemplate">`. State lives in 3 nanostores atoms (`$activeSessionId`, `$taskData`, `$pollStatus`) in `state.js`, but **most view state is NOT in the store** — it lives in the DOM (checkbox `.checked`, `<select>.value`, `card.classList`, `card._triage`) or in `main.js` locals. This is the single biggest migration hazard: React needs those promoted to state/props.

The natural component boundaries below are inferred from the static HTML regions (`ui/index.html`) plus the JS that mutates each region.

---

## COMPONENTS

### 1. AppShell / Layout
- **Responsibility:** Two-pane `.app-layout` = `<aside class="sidebar">` + `<main class="main-content">`. Owns the `DOMContentLoaded` bootstrap sequence and the top-level orchestration (`loadSessions().then(autoResumeSession...)`, `main.js:793-806`). Owns cross-cutting document-level listeners: keyboard triage (`main.js:408-425`), `visibilitychange` resume (`main.js:752-767`), and the `is-first-run` body class (`render.js:applyFirstRunState`, `main.js` calls it via `renderTasks`).
- **State/props read:** `$activeSessionId`, `sessionStorage['activeSessionId']`, `localStorage['lastViewedSessionId']` (`LAST_SESSION_KEY`, `main.js:49`).
- **Handlers:** bootstrap init, `autoResumeSession` (`main.js:770-790`), `document.keydown` triage dispatch, `document.visibilitychange`.
- **DOM-complexity/coupling:** HIGH. This is the god-module. In React it becomes the root `<App>` holding the router-less orchestration in effects (`useEffect` for bootstrap/resume/visibility) and a top-level context or store subscription. `is-first-run` becomes a derived class on the layout root. `isOverlayOpen()` (`main.js:815-819`) scans the DOM for visible `.progress-overlay` — replace with modal-stack state.

### 2. Sidebar
- **Responsibility:** Static container (`ui/index.html:15-139`) grouping SessionSwitcher, ExtractionConfig, MetricsPanel, Filters, GlobalActions, separated by `.divider`s. Header is a plain `<h2>SOW to Jira</h2>`.
- **State/props read:** none itself; a pure layout wrapper.
- **DOM-complexity/coupling:** LOW. Straight JSX container `<Sidebar>` composing the five child components.

### 3. SessionSwitcher (`#sessionListContainer`)
- **Responsibility:** "Start New Extraction" button, "Recent SOWs" `<select id="sessionSwitcher">`, and a per-row delete button. Lists sessions, switches active session, creates/deletes sessions.
- **State/props read:** `fetchSessions()` result (`main.js:298-329`), `$activeSessionId` (mirrored into `sessionSwitcher.value`, `main.js:322-323`), loading flag via `aria-busy`/`is-loading` class (`setSessionsLoading`, `main.js:722-726`).
- **Handlers:** `btnNewSession` click → `resetToNewSession` (`main.js:382-387`); `sessionSwitcher` change → `switchToSession(e.target.value)` (`main.js:376-380`); `btnDeleteSession` click → `showConfirm` then `deleteSession` (`main.js:268-295`).
- **DOM-complexity/coupling:** HIGH (imperative). `loadSessions` manually strips options with a `while (sessionSwitcher.options.length > 1) sessionSwitcher.remove(1)` loop then `createElement('option')` per session (`main.js:303-319`). The `<select>` is used as **the source of truth** for the currently-highlighted session (`sessionExists` reads `sessionSwitcher.options`, `main.js:808-811`; delete reads `sessionSwitcher.value`, `main.js:270`) — this must become a `sessions` array in state with a controlled `value={$activeSessionId}`. Date formatting (`toLocaleString`) and the `filename (date)` label build inline. **Note the store/DOM dual-write**: `$activeSessionId` and `sessionSwitcher.value` are hand-synced in ~5 places — a classic React win (one controlled component).

### 4. ExtractionConfig + UploadZone (`#newExtractionContainer`)
- **Responsibility:** The "Extraction Config" block: hidden `<input type=file>` + drag/drop `.upload-zone`, the `#processConfig` form (LLM Provider select `#llmMode` [mostly hidden], Jira Hierarchy `#jiraHierarchy`, Project Key `#projectKey`, Max Nodes `#maxNodes`), and the `Start Extraction` button. Validates PDF, shows filename, gates Start, kicks off `startExtraction`.
- **State/props read:** `selectedFile` (module local, `main.js:54`); config field values read on demand at submit via `getEl('llmMode').value` etc. (`main.js:435-442`); config values back-filled from `data.config`/`data.env_defaults` after a load (`main.js:475-491`).
- **Handlers:** `uploadZone` click/dragover/dragleave/drop (`main.js:209-232`), `pdfUpload` change (`main.js:215-217`), `btnStartProcess` click → `startExtraction` (`main.js:234, 428-465`). Shared validation `acceptFile`/`isPdf`/`clearSelectedFile` (`main.js:158-207`).
- **DOM-complexity/coupling:** HIGH. **Imperative-DOM flag:** `showClearFileAffordance` lazily `createElement`s a `#btnClearFile` × button and appends it to the zone, then toggles `display` (`main.js:191-207`) — pure createElement-on-demand → becomes conditional JSX `{selectedFile && <ClearButton/>}`. The `.drag-over` class toggle (`main.js:221-230`) → `isDragging` state. `uploadLabel.textContent`/`.has-file` class mutation (`main.js:171-189, 487-490`) → derived from `selectedFile`/session. The four config `<select>/<input>` are **uncontrolled** (read via `.value` at submit; written via `getEl(...).value = ...` on load) — must become controlled inputs bound to a `config` state object. `#processConfig` `style.display` toggled between `'flex'`/`'none'` in ~4 spots (`main.js:176, 336, 486`) → conditional render.

### 5. MetricsPanel / Overview (`.metrics-grid` under "Overview")
- **Responsibility:** Five metric cards (Total / Approved / Rejected / Pending / Pushed) showing counts.
- **State/props read:** `computeStats($taskData.get().tasks)` (`render.js:updateStats`, 268-275).
- **Handlers:** none (read-only display).
- **DOM-complexity/coupling:** LOW-MED (imperative but trivial). `updateStats` does 5 `getEl('statsTotal').textContent = ...` writes and is called explicitly from ~6 sites (loadData, resetToNewSession, saveTask via patchTaskCard, patchTaskCard branches). **React win:** derive `computeStats(tasks)` from `useStore($taskData)` — the panel recomputes automatically, and every explicit `updateStats()` call site disappears.

### 6. Filters (`#filtersSection`)
- **Responsibility:** Five status checkboxes (Pending/Approved/Rejected/Pushed/Flagged-only) + a Sort `<select id="sortConfidence">` (Section order / Confidence low-first [default] / high-first). Drives which task cards render + their order.
- **State/props read:** checkbox `.checked` + select `.value` read live in `currentFilters()` (`render.js:279-294`) and `sortDir` in `renderTasks` (`render.js:306-307`).
- **Handlers:** each of the 5 checkboxes + sort select `.addEventListener('change', () => renderTasks(saveTask))` (`main.js:90-99`).
- **DOM-complexity/coupling:** HIGH (imperative). **These controls are the canonical filter state and live entirely in the DOM.** `currentFilters()` reads `getEl('filterPending').checked` etc. with hardcoded defaults if absent (pending:true, approved:true, rejected:false, pushed:true, flagged:false). On change the handler blows away and rebuilds the entire task list. **React migration:** promote to a `filters` state object + `sortDir` state; the checkboxes/select become controlled; `renderTasks` becomes a derived `visibleTasks = useMemo(() => sortTasksByConfidence(tasks.filter(shouldShowTask), sortDir))`. Also `applyFirstRunState` (`render.js:149-152`) dims this whole block on first-run via a body class.

### 7. GlobalActions (`#globalActionsSection`)
- **Responsibility:** 3-tier button stack: Push to Jira (primary), Approve All Pending (secondary), Collapse All (ghost), Settings (ghost).
- **State/props read:** Push disabled/tooltip from `pushPreflight({activeSessionId, approvedCount, jiraConfigured})` (`main.js:updatePushPreflight`, 137-149); `approvedCount` from `computeStats(currentTasks())`.
- **Handlers:** `btnPushJira` click → preflight guard + `showConfirm` + `doPush` (`main.js:584-612`); `btnApproveAll` click → `showConfirm` + `approveAll` + `loadData` (`main.js:561-582`); `btnCollapseAll` click (see #12); `btnSettings` click → opens settings modal (handler lives in `modals.js:439`).
- **DOM-complexity/coupling:** MED-HIGH. `updatePushPreflight` imperatively sets `.disabled`, `.title`, `aria-disabled` and is called from ~5 sites (refreshJiraConfigured, resetToNewSession, loadData, saveTask, doPush finally). During a push, `doPush` mutates `btn.textContent = 'Pushing…'` and back to `'Push to Jira'` (`main.js:622-624, 646-648`) — button-label-as-state. **React win:** `disabled`/`title` derive from a `pushPreflight` `useMemo`; `Pushing…` becomes an `isPushing` state. `jiraConfigured` (`main.js:62`) is a module local set by `refreshJiraConfigured` (`main.js:125-134`) — becomes state, refreshed after settings save.

### 8. TaskList (`#taskList` in `.main-content`)
- **Responsibility:** The review surface. Renders grouped task cards, the empty/onboarding states, the "Showing N of M" count, and (conditionally) mounts the PushResultsPanel above itself. Groups visible tasks by SOW section (`source_refs[0].section_title`, default 'General').
- **State/props read:** `$taskData` (tasks + config + env_defaults), `currentFilters()`, `sortConfidence.value`, `jiraHierarchy.value` (for container/child labels), `hasLoadedTasks` module flag (`render.js:58`, suppresses first-load flash).
- **Handlers:** none directly; delegates card save/approve/reject to injected `onSave`.
- **DOM-complexity/coupling:** VERY HIGH — the migration centerpiece. `renderTasks(onSave)` (`render.js:298-420`):
  - `taskListEl.innerHTML = ''` full wipe every render.
  - Builds empty-state via `createElement` with two branches (onboarding pane with inline SVG icon string + `Upload SOW` CTA that `scrollIntoView`s and `.click()`s the upload zone, `render.js:318-356`; vs. filter-scoped message).
  - Groups into an object literal, then per group `createElement('.task-group')`, a `.task-group-header` built with a **template-literal `innerHTML` string** (`render.js:385-398`) mixing `escapeHtml(containerLabel)`/`escapeHtml(childLabel)` and an inline chevron SVG, then `groupHeader.querySelector('.task-group-section').textContent = section` (XSS-safe post-write, ui-13).
  - Group header click toggles `.collapsed` + rotates `.group-chevron` via inline `style.transform` (`render.js:404-408`).
  - Per task calls `buildTaskCard`.
  - **React migration:** `<TaskList>` maps `visibleTasks` → grouped `<TaskGroup>` → `<TaskCard>`. Empty/onboarding states become conditional components. The `innerHTML` group header string → JSX (the escapeHtml calls vanish — React escapes by default). Collapse state per group → `collapsed` state (or lifted, see #12). The `<h1>SOW Task Review</h1>` page header + `#showingCount` (`ui/index.html:230-234`) belong here too.

### 9. TaskCard (`<template id="taskCardTemplate">`, `ui/index.html:244-303`)
- **Responsibility:** One editable task: collapsed header (status dot + label + title + badges + push chip + chevron) and an expandable body (source-ref line, editable Title/Short Description/Use Case/Acceptance Criteria/Considerations & Constraints/Deliverables, plus Approve/Reject/Save Edits buttons + inline status text). Owns its own expand/collapse, edit-collection, and triage verbs.
- **State/props read:** the task `t` (id, title, status, confidence, flags, source_refs, short_description, use_case, acceptance_criteria, considerations_constraints, deliverables, push_result, jira_issue_key), plus `jiraServerFromStore()` for the push chip (`render.js:443-446`).
- **Handlers:** header click/keydown toggle (`render.js:595-605`); Save click → `onSave(getUpdatedData())` (`render.js:648-651`); Approve/Reject click → `onSave({...,status})` (`render.js:656-683`); exposes `card._triage = {approve, reject, expand}` for the document-level keyboard handler (`render.js:689-693`).
- **DOM-complexity/coupling:** VERY HIGH. `buildTaskCard(t, onSave)` (`render.js:521-696`):
  - **Template cloning:** `template.content.cloneNode(true)` then `querySelector` for ~15 sub-elements — the canonical imperative pattern that becomes a JSX component.
  - Sets `card.id = task-card-<encoded id>` and `card.dataset.taskId` for later DOM lookup (`render.js:42-43, 529-530`) — needed only because patching is done by DOM id; React keys make this obsolete.
  - **Manual createElement:** status-label span inserted `insertAdjacentElement('afterend')` after the dot (`render.js:539-544`); confidence badge with computed band class `conf-low/mid/high` (`render.js:549-558`); flag badges loop (`render.js:559-564`); push chip appended (`render.js:568-569`).
  - **Direct classList/style toggles:** `ind.classList.add(status.toLowerCase())`, `card.classList.toggle('expanded')`, chevron `svg.style.transform = 'rotate(180deg)'`, `btnApprove.style.display='none'` when already approved (`render.js:667-683`).
  - **Uncontrolled form fields:** six `.value = ...` writes on load, then `getUpdatedData()` re-reads all six `.value`s and `.split('\n').filter(...)` for the array fields (`render.js:614-646`) — becomes controlled inputs + a per-card form state.
  - **`.task-status-text` sets `style.color` from `statusDisplay(t.status).color`** (`render.js:621-626`).
  - a11y: header `role=button` + `tabindex=0` + `aria-expanded`/`aria-controls` set imperatively (`render.js:575-578`); `isControl(el)` guard so clicks on BUTTON/INPUT/TEXTAREA/A don't bubble to the toggle (`render.js:589-597`).
  - **React migration:** the whole thing collapses into a `<TaskCard task onSave>` with `expanded`/form state. `_triage` disappears (parent calls handlers via refs or a keyboard hook operating on the focused card's task id). `stopPropagation` guards mostly vanish (React click handling per element).

### 9a. PushChip (sub-component of TaskCard header)
- **Responsibility:** Per-task Jira push outcome chip derived from `task.push_result`. Success → "Pushed" + linked issue key; failure → severity chip (`Rate-limited`/`Auth error`/`Failed`) keyed to `error_class` with error in `aria-label`/`title`. Absent when never pushed.
- **State/props read:** `t.push_result` (`success`, `jira_issue_key`, `jira_issue_url`, `error`, `error_class`), `jiraServerUrl`.
- **DOM-complexity/coupling:** MED. `buildPushChip` (`render.js:452-516`) all createElement; URL scheme-checked via `jiraBrowseUrl` (http/https only, `render.js:170-179`). The link gets `tabindex=-1` + `stopPropagation` because it lives inside the `role=button` header (WCAG 4.1.2). → JSX sub-component; keep the `jiraBrowseUrl` scheme guard as a util.

### 10. ProgressOverlay (`#progressOverlay`, `ui/index.html:144-163`)
- **Responsibility:** Full-screen run overlay shown during extraction/push. Holds the beUI loader mount (`#progressLoader`, Newton's cradle), step title, message, progress bar + percentage, live log console, and Dismiss/Cancel(Stop) buttons. Renders live poll updates; on failure re-purposes Dismiss into a severity-aware recovery button.
- **State/props read:** poll `status` payload (`is_running`, `progress`, `message`, `current_step`, `kind`, `run_id`, `logs[]`, `error`, `error_class`) — read inside the `setInterval` in `polling.js:153-233`, NOT the store.
- **Handlers:** `btnDismissProgress` click → hide overlay (`main.js:236-240`) OR the injected recovery action (`polling.js:renderFailureRecovery`, 78-101); `btnCancelProcess` click → immediate UI teardown + best-effort `cancelRun` with 3s AbortController (`main.js:242-266`).
- **DOM-complexity/coupling:** VERY HIGH. Everything is imperative DOM mutation on a static overlay:
  - `showProgressOverlay` sets `progressOverlay.style.display='flex'`, toggles Cancel visible / Dismiss hidden (`polling.js:122-129`).
  - The poll loop imperatively writes `progressStepTitle.textContent`, `progressMessage.textContent`+`.style.color`, `progressBarFill.style.width = pct%`, `progressPercentage.textContent`.
  - **Log console append pattern:** compares `logConsole.children.length` to `status.logs.length` and appends only new `<p>` nodes with inline styles, then `scrollTop = scrollHeight` (`polling.js:174-186`) — an incremental-append optimization. In React → render `status.logs.map(...)` + a scroll-to-bottom `useEffect`; drop the diff-count logic.
  - **Loader mount:** `mountLoader(progressLoaderMount, 'newton', {label:'Extracting'})` if the mount is empty (`main.js:80-82`). The loaders (`loaders.js`) are motion-based imperative handles (`{el, start, stop, destroy}`) — either wrap as a `<Loader type>` React component or swap for beUI/Framer-Motion React loaders directly (they're the beUI ports).
  - **Failure recovery (`polling.js:renderFailureRecovery`):** inserts a `.sev-chip` before the message, mutates the Dismiss button's label + `_recoveryAction` + a **guarded single listener** (`_recoveryBound`), and `clearFailureRecovery` nulls it on success (`polling.js:107-120`). This stashing-action-on-DOM-node pattern → React: `recovery` derived from `errorRecovery(error_class)`, button label/onClick from state. The `overlay.style.display !== 'none'` scan (`main.js:isOverlayOpen`) → modal-open state.
  - The overlay reuses the `.progress-overlay`/`.progress-card` markup that the a11y modal helpers (`modals.js`) also target.

### 11. SettingsModal (`#settingsModal`, `ui/index.html:166-228`)
- **Responsibility:** LLM provider + Jira credential settings. Provider `<select>` (populated from `/api/providers`), provider-specific field toggling (base URL, Azure deployment/version, OpenRouter help, API key hidden for Ollama), debounced model discovery + Fetch Models, "Use recommended model", Jira Server URL + API token + Test Connection, Save/Cancel.
- **State/props read:** `providerRegistry` (module, `modals.js:15`), `providerSettingsCache` (module, `modals.js:16`), `fetchSettings()` payload on open (`modals.js:442-447`), live field `.value`s at save (`modals.js:460-469`).
- **Handlers (all in `initSettingsModal`, `modals.js:423-554`):** `btnSettings` open → load providers + settings, populate, `openModal`, `scheduleModelFetch`; `btnCancelSettings` → `closeModal`; `btnSaveSettings` → `saveSettings` + update cache + toast; `providerSelect` change → `updateProviderUI` + `scheduleModelFetch`; `providerModelSelect` change → mirror to hidden `providerModel`; `providerApiKey`/`providerBaseUrl`/azure inputs `input` → `scheduleModelFetch` (400ms debounce, `modals.js:380-383`); `btnFetchModels` → `fetchModels`; `btnTestJira` → `runJiraConnectionTest`; `btnUseRecommendedModel` → set `google/gemini-2.5-flash`.
- **DOM-complexity/coupling:** VERY HIGH.
  - `loadProviders` clears + rebuilds `providerSelect` options via createElement (`modals.js:270-282`).
  - `updateProviderUI` (`modals.js:284-324`) is a big imperative show/hide: toggles `.style.display` on `baseUrlGroup`/`azureFields`/`openrouterModelHelp`/`apiKeyGroup` by provider, and writes `.value`/`.placeholder` on ~7 fields from the cache — **all conditional-render + controlled-input territory** in React.
  - `fetchModels` clears + rebuilds `providerModelSelect` options and writes `modelFetchStatus.textContent`+`.style.color` for Fetching/✓N/✗err (`modals.js:326-378`) — status-as-DOM-text → state.
  - **Secret masking convention:** stored token shown as placeholder `'Stored (re-enter to change)'`, cache stores `'***'` sentinel (`modals.js:308-315, 476-484`) — preserve carefully in React state (don't render the sentinel as a real value).
  - `runJiraConnectionTest` (`modals.js:398-421`) writes `statusEl.innerHTML` with `escapeHtml`-wrapped server-echoed text → JSX with `{escaped}` (React handles escaping) but keep the classify branches (success/user_fixable/transient/thrown).
  - Uses the shared `openModal`/`closeModal` a11y layer.

### 12. Collapse-All / group & card expand state (cross-cutting)
- **Responsibility:** `btnCollapseAll` collapses every `.task-group` and un-expands every `.task-card`, rotating chevrons.
- **Handlers:** `main.js:389-403` — `document.querySelectorAll('.task-group').forEach(...)` adds `.collapsed` + sets `.group-chevron` `style.transform`, and `.task-card` loop removes `.expanded` + resets chevron.
- **DOM-complexity/coupling:** HIGH (imperative, cross-component). This reaches directly into every group/card node's classes — a **React anti-pattern**. Migration: lift collapse/expand into shared state (a `collapsedGroups` Set + per-card `expanded`), and "Collapse All" flips that state; chevrons rotate via `className`/`data-expanded`, not `style.transform`.

### 13. ConfirmDialog (`showConfirm`, built in JS — no HTML)
- **Responsibility:** Themed replacement for `window.confirm` on destructive/bulk actions (Approve All, Push, Delete session). Returns `Promise<boolean>`.
- **State/props read:** `{title, message, confirmLabel, cancelLabel, danger}` opts (`modals.js:212`).
- **Handlers:** Cancel → resolve(false), Confirm → resolve(true); Escape/backdrop/programmatic close via `openModal` `onClose` → resolve(false) (`modals.js:259-266`).
- **DOM-complexity/coupling:** MED-HIGH. Entirely `createElement`s a `.progress-overlay > .progress-card.confirm-card` + heading/message/actions, appends to `body`, and removes on settle (`modals.js:212-268`). The Promise-per-invocation pattern (imperative `await showConfirm(...)`) is convenient in the current handlers — in React this becomes a `<ConfirmDialog>` driven by state, or a `useConfirm()` hook that returns a promise while rendering a portal. Callers: `main.js:275` (delete), `main.js:566` (approve all), `main.js:600` (push).

### 14. Toasts (`showToast`, `#toastContainer`)
- **Responsibility:** Transient notifications. Info/success auto-dismiss after 3s; **errors persist until clicked** (cursor+title+click handler) with a fade-out.
- **State/props read:** `(message, type)`; container `#toastContainer` (`ui/index.html:242`).
- **DOM-complexity/coupling:** MED. `createElement('.toast')`, sets `borderLeftColor` var by type, appends, `setTimeout(remove, 3000)` or click-to-dismiss (`render.js:120-142`). Called from ~20 sites across main.js/modals.js. **React migration:** a toast store/queue + `<ToastContainer>` mapping toasts; `showToast` becomes `pushToast(...)` dispatching to that store (this is the one imperative API worth keeping as a thin wrapper so the 20 call sites barely change).

### 15. PushResultsPanel (`#pushResultsPanel`, built in JS)
- **Responsibility:** After a push completes, a results panel inserted **above** `#taskList`: header (title + "N pushed, M failed" summary + × close), an aggregate failure row (severity chip + first error), and a list of pushed tasks each linking to its Jira issue.
- **State/props read:** `{tasks, jiraServerUrl, failedCount, firstError, errorChip}` (`render.js:181-182`); `errorChip` from `mapFailureChip(errorClass)` → `errorRecovery` (`main.js:534-537`); `lastPushFailure` module local (`main.js:513`).
- **Handlers:** close button → `panel.remove()`.
- **DOM-complexity/coupling:** MED-HIGH. `renderPushResults` (`render.js:181-264`) is idempotent-by-removal (`getEl('pushResultsPanel')?.remove()` then rebuild) and `insertBefore(panel, taskListEl)` — DOM-position-coupled to the task list. In React → conditional `<PushResultsPanel>` rendered above `<TaskList>` when `lastPushFailure || pushedTasks.length`. Reuses `jiraBrowseUrl` scheme guard.

---

## Cross-cutting concerns to promote to state/context in React

- **`main.js` module locals that are really component state:** `selectedFile`, `lastOp` ('extraction'|'push'), `lastPushRequest`, `jiraConfigured`, `lastPushFailure`. These drive op-aware Retry (`pollRecovery.onRetry`, `main.js:107-117`) and preflight — must be lifted to state/context, not module scope.
- **DOM-as-state (the big one):** filter checkboxes, sort select, extraction-config fields, provider fields, `card.classList('expanded')`, `group.classList('collapsed')`, `card._triage`, button `.textContent` ('Pushing…'/'Saving...'/'Testing…'/'Dismiss'), `sessionSwitcher.value`. All read/written imperatively today; all become React state/props.
- **The 3 nanostores atoms survive as-is** (`$activeSessionId`, `$taskData`, `$pollStatus`) consumed via `@nanostores/react`'s `useStore`. `api.js` pure helpers (`computeStats`, `shouldShowTask`, `statusDisplay`, `pushPreflight`, `decideSessionSwitch`, `bucketOf`, hierarchy labels, `TASK_STATUS`) port unchanged — keep them, they're already the "view-model."
- **Polling is a `setInterval` closure over injected `onComplete`/`recovery`** (`polling.js:141-234`) to dodge an import cycle. In React → a `useEffect`/custom `usePolling(sessionId)` hook writing to `$pollStatus`; the injected callbacks become effects reacting to poll state. The stale-response guard (`$activeSessionId.get() !== polledSession`, `polling.js:158`) stays as a captured-ref check.
- **Modal a11y layer (`modals.js` openModal/closeModal/trapFocus, 32-203)** — focus trap, Escape stack, backdrop click, focus restore. shadcn/Radix `Dialog` gives all of this for free; the hand-rolled `modalStack`/`WeakMap`/`FOCUSABLE_SELECTOR` machinery can be **deleted** on migration.
- **Imperative-DOM patterns to eliminate (JSX/state replaces them):** `innerHTML=''` wipes (`render.js:301`, and the settings `.innerHTML=''` option clears); `<template>.content.cloneNode` (`render.js:525`); template-literal `innerHTML` group header (`render.js:385-398`); ~all `document.createElement` factories (task cards, badges, options, toasts, confirm, push panel, clear-file button, loaders); direct `classList.toggle`/`style.display`/`style.transform`/`style.color`/`style.width` mutations everywhere; `_triage`/`_recoveryAction`/`_recoveryBound` properties stashed on DOM nodes; `insertAdjacentElement`/`insertBefore`/`replaceWith` for surgical patching (`patchTaskCard`, `render.js:746-791`) — React reconciliation + keys make `patchTaskCard` and the whole "swap one card in place, preserve expanded" optimization unnecessary.
- **Inline SVG strings** (empty-state icon `render.js:344-345`, chevrons in template + group header, delete/chevron in HTML) → JSX SVG components or an icon set (lucide, which shadcn uses).
- **Keyboard triage** (`main.js:408-425` + `resolveTriageAction`/`isTypingTarget`/`moveCardFocus`/`triageCard` in `render.js`) — pure `resolveTriageAction`/`isTypingTarget` port unchanged; the focus-navigation (`moveCardFocus` walking `.task-card` nodes) and `triageCard(action, card._triage)` become a `useKeyboardTriage` hook operating on the focused card's task id + the tasks array, dispatching the same save/approve/reject handlers.
