# UX Flows & Pain-Points Inventory — SOW-to-Jira

Reconstructed from the current vanilla-JS UI (`ui/src/*.js`, `ui/index.html`, `ui/styles.css`) and the FastAPI backend (`ui/server.py`). Drives redesign priorities for the React+Vite revamp.

Register = **product** (a serious internal work tool). The audience is PM / delivery / dev users doing repeated triage work, so density, keyboard flow, and honest state feedback matter more than marketing polish.

---

## 0. App shell / mental model (read this first)

- **Two-column shell, fixed** (`ui/index.html:13`): a left `.sidebar` that crams EVERYTHING (title, session switcher + delete, upload zone, extraction config, overview metrics, filters, sort, global actions incl. Push/Settings) and a right `.main-content` holding the page header + task list. All modals (`#progressOverlay`, `#settingsModal`) live in `main-content` as fixed overlays.
- **One giant view, no routing.** There are no routes/tabs — upload, config, review, settings, and progress all share one screen and are shown/hidden by `.style.display` toggles from `main.js`. State is 3 nanostores (`ui/src/state.js`: `$activeSessionId`, `$taskData`, `$pollStatus`) plus module-local mutable vars in the `DOMContentLoaded` closure (`main.js:54-62`: `selectedFile`, `lastOp`, `lastPushRequest`, `jiraConfigured`).
- **Sidebar is doing far too much.** It is simultaneously the nav, the create-form, the dashboard, and the action bar. On a genuine first run the Filters + Global Actions blocks are just dimmed with `body.is-first-run` (`styles.css:889`, `render.js:149`) rather than hidden — still visually present as noise.

---

## 1. First-run / empty state

**Path:** `main.js` init (`loadSessions().then(...)` `main.js:793`) → no `activeSessionId`, no `lastViewed` → `loadData()` (`main.js:804`) → `renderTasks()` → `applyFirstRunState(true)` (`render.js:316`) + onboarding empty pane (`render.js:319-346`).

What exists today:
- A centered onboarding pane with a file-icon SVG, "No Statement of Work loaded yet", subcopy, and an **Upload SOW** CTA that scrolls to + highlights + clicks the sidebar upload zone (`render.js:332-340`, `.upload-zone-highlight` flash).
- `body.is-first-run` dims Filters + Global Actions to 45% opacity, grayscale, `pointer-events:none` (`styles.css:889-895`).
- `hasLoadedTasks` gate (`render.js:57-61`, `markTasksLoaded`) suppresses a "No SOW" flash before the first fetch resolves.

Friction / missing:
- **The primary action lives in two disconnected places.** The empty-pane CTA is center-stage but the *actual* upload zone + all config (hierarchy, project key, max nodes) is in the far-left sidebar. The CTA just scrolls-and-clicks it — a redirect, not a real entry point. A first-timer's eye ping-pongs across the screen.
- **No guidance on the config knobs.** `Jira Hierarchy`, `Project Key` (defaults `PROJ`), `Max Nodes (DoW Protect)` = 200 (`index.html:53-68`) are shown with zero explanation. "Max Nodes (DoW Protect)" is jargon; the LLM Provider select is `is-hidden` (`index.html:47`) so the user has no idea which model will run until they open Settings separately.
- **Settings is not surfaced in the first-run funnel.** Jira creds + LLM provider must be configured in the Settings modal, but nothing tells a fresh user to do that first. They can happily upload + extract, then hit a disabled Push button with only a tooltip explaining why (`api.js:101`).
- **Empty pane styling is inconsistent** — the base `.empty-state` is *left*-aligned (`styles.css:508-519`) while the onboarding variant is centered (`styles.css:897`). Two different empty visual languages.
- **No sample / demo affordance.** A serious tool benefits from "try a sample SOW" so a new user sees the payoff before committing their own doc.

---

## 2. Upload → index → extract → review (the core loop)

### 2a. Upload + config
**Path:** click/drop on `#uploadZone` → `acceptFile()` (`main.js:164`) validates PDF (`isPdf`, `main.js:158`), shows filename, reveals `#processConfig`, enables Start. Drop handler now shares the same validation (`main.js:226-231`, note ui-19).

Friction / missing:
- **No client-side size feedback.** Backend rejects >50 MB with a 413 (`server.py:645-650`) and non-PDF magic bytes with 400 (`server.py:653-654`), but the client only checks extension/MIME. A user can select a 200 MB file, wait through the upload, then get a generic toast.
- **Upload is fire-and-forget with no progress.** `uploadFile()` (`api.js:307`) `await`s the whole POST with only a `showToast('Uploading SOW...')` (`main.js:432`) — no spinner, no byte progress, no disabled state on the Start button during upload. Large PDFs feel frozen.
- **Config is buried and un-persisted per-field.** Hierarchy/project/max-nodes reset to defaults on every new session unless a session is reloaded (`loadData` restores them, `main.js:479-484`). No remembered "my usual settings."
- **The clear-file affordance is injected imperatively** (`main.js:191-207`) — a hand-built `×` button appended to the zone; fine, but it's the kind of ad-hoc DOM the React rework should formalize into a component.

### 2b. Extraction (long-running, polled)
**Path:** `startExtraction()` (`main.js:428`) → upload → `POST /api/process` → `run_pipeline_task` in a bg task (`server.py:510`) → `showProgressOverlay()` + `startStatusPolling(loadData, pollRecovery)` (`main.js:450-457`). Poll = `GET /api/status` every **1000 ms** (`polling.js:153`), updating step title, message, %, and appending new log lines to `#logConsole` (`polling.js:161-186`). Backend pushes `current_step`, `message`, `progress`, and a capped 50-line `logs` array (`server.py:552-558`).

The overlay (`index.html:144-163`): a Newton's-cradle beUI loader (`main.js:80-82`), step title, message, a progress bar, a %, a live log console, and Dismiss (hidden until done) + "Stop Extraction" buttons.

Friction / missing:
- **The overlay is a full-screen modal that blocks the entire app during extraction.** You cannot browse other sessions, tweak settings, or read prior results while a run is in flight — extraction can take minutes. This is the single biggest workflow blocker. It should be a non-blocking, dismissible progress surface (toast/drawer/inline banner), not a hostage-taking overlay.
- **Polling is a fixed 1 s `setInterval`** (`polling.js:153`) with no backoff, no jitter, no visibility-pause of the interval itself (it only *restarts* on visibilitychange, `main.js:752-767`). Multiple resume paths can stack intervals — `startStatusPolling` clears the previous one (`polling.js:142`) but the orchestration around `visibilitychange` + `autoResumeSession` + `switchToSession` all call it, so lifecycle is fragile.
- **Progress semantics are coarse.** `current_step` is a bare integer with title "Step N (Run: ...)" (`polling.js:166`) — no named phases (index / extract / dedup / gap-recovery) even though the pipeline HAS those distinct stages. The user sees "Step 3" not "Deduplicating tasks." Rich, phase-aware progress is a big opportunity.
- **The log console is raw text lines** (`polling.js:174-186`) styled as secondary-color `<p>`s — no severity, no grouping, no copy button, auto-scrolls only. It reads like a terminal dump inside a card.
- **"Reconnecting…"** only appears after 3 consecutive poll failures (`polling.js:228-231`) and there is no hard-fail / give-up state — it will say "Reconnecting…" forever if the backend is truly gone.
- **No time estimate / elapsed timer.** For a multi-minute job there's no ETA or "running for 2:14."

### 2c. Review / edit / approve tasks
**Path:** on completion `onComplete` → `loadData()` (`main.js:468`) → `GET /api/tasks` → `renderTasks(saveTask)` (`render.js:298`). Tasks are grouped by SOW section (`source_refs[0].section_title`, `render.js:365-370`), rendered from a `<template>` (`index.html:244`) into collapsible group + collapsible cards. Each card: status dot + label + confidence badge + flag badges + optional push chip on the header; editable Title / Short Description / Use Case / Acceptance Criteria / Considerations / Deliverables in the body; Approve / Reject / Save Edits buttons.

Supporting mechanics:
- **Sidebar Overview metrics** (Total / Approved / Rejected / Pending / Pushed, `index.html:77-98`, `updateStats` `render.js:268`).
- **Filters** (Pending/Approved/Rejected/Pushed/Flagged checkboxes) + **Sort** (section order / confidence asc / desc), re-render on change (`main.js:90-99`, `render.js:279-311`). Default sort = confidence **low first** (`index.html:123`) — good triage default.
- **Keyboard triage** (ui-14): `a`/`r` approve/reject, `e` expand, `j/k` + arrows move focus, scoped to the focused card and suppressed while typing or when a modal is open (`main.js:408-425`, `render.js:71-103`, `688-706`).
- **Optimistic in-place card patch** on save/approve/reject (`patchTaskCard`, `render.js:746`) preserves neighbours' expand state.
- **Approve All Pending** (`main.js:562`) with a styled confirm, then full `loadData()`.
- **Filtered-out empty state** ("No tasks match the current filters", `render.js:347-353`).

Friction / missing:
- **Every field is a raw `<input>`/`<textarea>` with multi-line lists stored as newline-joined text** (`render.js:617-619`, split back on save `render.js:633-644`). Acceptance criteria / deliverables / constraints are really *lists* but the UI treats them as freeform blobs — no add/remove/reorder line items, no chips. Big editing-UX opportunity.
- **No dirty-state tracking.** Editing a field then collapsing/switching silently discards edits unless the user clicks Save Edits first — there's no "unsaved changes" warning, no autosave, no visual dirty indicator. Approve/Reject *do* snapshot the current field values (`getUpdatedData`, `render.js:628`) so they save edits implicitly, but plain navigation does not.
- **Save/Approve/Reject each fire an individual `POST /api/tasks`** with a toast per action (`saveTask` `main.js:546`). Triaging 50 tasks = 50 toasts stacking bottom-right. No batch, no undo.
- **Confidence is shown three ways inconsistently:** a header badge "Conf 62%" with band class (`render.js:550-557`), and again as "Confidence: 62%" in the `.source-ref` line inside the body (`render.js:607-611`). Redundant.
- **Grouping key is fragile:** falls back to literal `'General'` when `source_refs` is empty (`render.js:367`), and the group header label ("Epic/Story/Section") is derived from the *current* hierarchy dropdown value at render time (`render.js:372`), so changing the hierarchy select silently relabels all groups even mid-review.
- **No bulk selection.** You can Approve *All* Pending but can't multi-select a subset. No "reject all in this section," no per-group approve.
- **Flag badges are opaque** — `f.value` or the raw string (`render.js:559-564`) with no tooltip/explanation of what a flag means or how to resolve it.
- **The whole list re-renders (`taskListEl.innerHTML = ''`, `render.js:301`) on any filter/sort change**, losing scroll position and every card's expand state. Only single-card edits are optimistic; filter toggles are not.
- **No search / free-text find** across tasks — for a 100+ task SOW that's painful.
- **`Merged` status has a bucket ('pending') and a label but no inline display treatment** (`api.js:36`, `render.js:52-54`) — a dedup outcome the user can't really see or reason about.

---

## 3. Session switching

**Path:** `#sessionSwitcher` `<select>` (`index.html:26`) → `switchToSession(id)` (`main.js:346`). Sets active store + localStorage `lastViewedSessionId`, hides the new-extraction container, then probes `GET /api/status`: if `is_running` → reopen overlay + resume polling (`decideSessionSwitch`, `api.js:113`); else `loadData()`. Sessions loaded via `GET /api/sessions` sorted newest-first (`server.py:453`, options built in `loadSessions` `main.js:298`). Delete via the trash button (`main.js:268`) with a styled confirm.

Friction / missing:
- **Session picker is a native `<select>`** labeled "Recent SOWs" showing `filename (Mon D, HH:MM)` (`main.js:316-317`). No status pill (running/done/failed), no task counts, no thumbnail, no search/filter of sessions. For a user with dozens of runs this is a flat unsearchable dropdown.
- **No "sessions" list view.** Sessions are a dropdown, not a browsable/sortable table. There's no dashboard of past extractions with their outcomes — a natural landing surface the rework should add.
- **Delete targets the `<select>.value`, not necessarily the active session** (`main.js:270`) — subtle coupling; the dropdown value IS the source of truth for delete but the store is the source for everything else. Fragile.
- **Switching mid-run reopens the blocking overlay** (`main.js:361-368`), so browsing to check on a running job re-traps you in the modal.
- **"Loading session…" is written into the upload label** (`main.js:372`) as the only loading feedback for a switch — an odd place, and the task list shows a skeleton separately (`setTaskListLoading`, `main.js:701`). Two uncoordinated loading signals.
- **Resume logic is spread across three call sites** (`switchToSession`, `autoResumeSession`, `visibilitychange`) each independently recovering `lastOp` from `/api/status` (`main.js:363, 757-763, 783-785`). This op-type recovery dance (extraction vs push) is a symptom of state living in closure vars instead of the store — a prime React/store consolidation target.

---

## 4. Settings + Jira test-connection

**Path:** Settings button (`index.html:137`) → `initSettingsModal` opens `#settingsModal` (`modals.js:438`), loads `/api/providers` + `/api/settings`, populates provider select, per-provider field toggles (`updateProviderUI`, `modals.js:284`), debounced model discovery (`scheduleModelFetch`, 400 ms, `modals.js:380`). Fields: provider, API key (password), base URL, Azure deployment/version (conditional), model select + Fetch Models, Jira server URL, Jira API token + **Test Connection**. Save → `POST /api/settings` (`modals.js:459`).

Test Connection (FE-2): `runJiraConnectionTest` (`modals.js:398`) → `POST /api/jira/test`, renders inline classified outcome beside the button — success "✓ Connected as <user>", or "✗ <error>" with a "— temporary, please retry" suffix for transient class (`modals.js:405-416`). All server text escaped (credential-adjacent).

Friction / missing:
- **LLM config and Jira config are crammed into ONE modal** (`index.html:166-228`) — two unrelated concerns (which model runs the pipeline vs where issues get pushed) in a single scrolling card. They should be separate tabs/sections.
- **The modal is a tall single-column scroll** with conditional Azure/OpenRouter blocks appearing inline (`modals.js:302-306`). On smaller screens it's a long scroll with a Save button at the very bottom; no sticky action bar.
- **Stored secrets are shown as empty with a "Stored (re-enter to change)" placeholder** (`modals.js:308-315`) — functional but subtle; a user can't tell at a glance whether creds are configured without reading placeholder text. No "Configured ✓" status chip.
- **Model discovery failure is a terse "✗ <message>"** in a tiny status span (`modals.js:372-377`); Fetch Models has no loading spinner beyond the text "Fetching...".
- **Test Connection only exists for Jira, not for the LLM provider.** You can save a bad LLM key and only discover it when an extraction fails minutes later. A parallel "Test LLM" would close the loop.
- **No validation before Save** — you can save with an empty model or malformed base URL; errors surface downstream.
- **Save closes the modal on success** (`modals.js:487`) but does NOT re-run `refreshJiraConfigured()` in `main.js` reactively — that only re-checks opportunistically (`main.js:123` on load). The Push pre-flight gate can lag behind a just-saved Jira cred until something else triggers `updatePushPreflight`. (Confirmed: `refreshJiraConfigured` is called at init only; save has no callback into it.)
- **The OpenRouter "recommended model" helper** (`index.html:203-206`, `modals.js:535`) is a nice touch but hard-codes `google/gemini-2.5-flash` and a "$0.49/run" claim inline — brittle copy the rework should data-drive.

---

## 5. Push to Jira + results

**Path:** Push button is pre-flight-gated (`updatePushPreflight`, `main.js:137`; `pushPreflight` `api.js:96`) — disabled unless `activeSessionId` + `approvedCount>0` + `jiraConfigured`, with the disabled reason in the tooltip. Click → styled confirm ("Push N approved task(s) to PROJ?") → `doPush` (`main.js:617`) → `POST /api/push` returns `{started, run_id}` → reopen overlay + `startStatusPolling(onPushComplete, ...)`. Push runs as a bg task (`run_push_task`, `server.py:1027`, `kind="jira_push"`, `PushGate` `server.py:992`). On completion `onPushComplete` (`main.js:680`) parses "N failed" from the status message, captures the aggregate failure, then `loadData()` → `maybeRenderPushResults` (`main.js:514`) builds `#pushResultsPanel` (`renderPushResults`, `render.js:181`): a list of pushed tasks linking to `<server>/browse/<KEY>` (scheme-checked) + an aggregate failure chip.

Friction / missing:
- **Push reuses the SAME full-screen extraction overlay** (`kind` just changes the title to "Jira Push", `polling.js:166`). Same blocking-modal problem as extraction, for what is often a quick operation.
- **Per-task push failure detail is NOT persisted server-side** — only an aggregate `first_error` + `error_class` rides the status (documented in `render.js:160-164`). So the results panel can list *successes* with deep links but only shows a single aggregate failure chip + first error for *all* failures (`render.js:216-231`). A user with 3 different failure reasons sees one. This is a real data-model gap the rework should flag to the backend.
- **The results panel is injected above the task list** (`render.js:262`) as a dismissible `<section>` — easy to miss, no scroll-to, disappears on the next `loadData`. Push outcomes deserve a persistent, per-task status the review list itself reflects (it partially does via `push_result` chips on cards, `render.js:566-569`, but the two representations can drift).
- **Push button label mutates to "Pushing…"** and disables (`main.js:622-624`) but the real feedback is the modal — double signaling.
- **Retry-push has a gnarly fallback** (`retryPush`, `main.js:653`): if there's no cached `lastPushRequest` (e.g. after a refresh), it reloads the session, re-enables the button, and re-pushes only if approved tasks exist — otherwise a warning toast. This complexity exists because push state lives in closure vars, not the store.
- **No pre-push summary / dry-run.** The confirm just says "Push N approved to PROJ" — no preview of the epic/task hierarchy that will be created, no way to see what maps to what before it's irreversible in Jira.

---

## 6. Error / retry recovery (cross-cutting)

**Path:** All async failures (extraction OR push) surface through the SAME polling branch (`polling.js:189-205`). Backend classifies exceptions into `ErrorClass` (`classify_exception`, `server.py:571-572`) → `error_class` on status. `errorRecovery` (`polling.js:29`) maps class → { chip label, chip class, action, action label }:
- **TRANSIENT** → "Retryable" chip → **Retry** button
- **USER_FIXABLE** → "Check credentials" chip → **Open Settings** button
- **TERMINAL / unknown** → "Failed" chip → **Dismiss**

`renderFailureRecovery` (`polling.js:62`) injects the severity chip above the message and repurposes the Dismiss button as the class-specific primary action. `main.js` wires `pollRecovery` (`main.js:107-117`): Retry is **op-aware** (`lastOp==='push'` → `retryPush`, else `startExtraction`), Open Settings clicks the Settings button, Dismiss hides the overlay + `resetToNewSession`.

Toasts (`render.js:120-142`): error toasts persist until clicked (`main.js`/`render.js:134-138`), success/info auto-dismiss after 3 s.

Friction / missing:
- **Recovery UI is bolted onto the reused overlay** — the chip + repurposed button are imperatively injected/removed (`renderFailureRecovery` / `clearFailureRecovery`, `polling.js:62-120`) with a `_recoveryAction`/`_recoveryBound` guard dance to avoid a stale listener wiping fresh tasks (`polling.js:87-98`). This is fragile plumbing that a declarative React state machine (idle/running/success/error-by-class) would eliminate.
- **The error message is the raw exception string** (`Error: ${status.error}`, `polling.js:194`) colored red — not user-facing copy. A stack-trace-ish string in a modal is intimidating and unhelpful.
- **Retry re-runs the WHOLE operation from scratch** — no resume-from-stage, no partial recovery. A transient LLM 429 on node 190/200 re-extracts all 200.
- **USER_FIXABLE only knows how to "Open Settings"** — it can't deep-link to the *specific* bad field (e.g. the LLM key vs the Jira token). The user opens a two-concern modal and guesses.
- **No global error boundary / offline state.** A dead backend just shows "Reconnecting…" forever (§2b). Route-load failures (`loadData` catch, `main.js:501-503`) only toast "Failed to load data" and leave an empty list.
- **Cancel ("Stop Extraction") is optimistic-first** (`main.js:243-265`): it tears down the UI immediately and signals the backend best-effort with a 3 s timeout — good resilience, but there's no confirmation the backend actually stopped, and the run is "abandoned client-side regardless." A half-stopped run can keep consuming LLM budget invisibly.

---

## 7. Loading & feedback state coverage (matrix)

| Surface | Empty | Loading | Error | Success |
|---|---|---|---|---|
| Task list | ✅ onboarding pane + filtered-out msg (`render.js:319-354`) | ✅ 3-card skeleton, only on first load (`main.js:701-720`) | ⚠️ toast only, list left empty (`main.js:501`) | ✅ grouped cards |
| Session switcher | ⚠️ "-- Active Sessions --" placeholder only | ⚠️ `aria-busy` + `is-loading` class, no visible spinner (`main.js:722`) | ❌ console.error only (`main.js:324`) | ✅ options list |
| Extraction | n/a | ✅ blocking overlay + Newton loader + logs | ⚠️ raw exception in overlay + recovery button | ✅ "Complete!" + toast |
| Push | n/a | ✅ (reused blocking overlay) | ⚠️ aggregate-only failure chip | ✅ results panel w/ deep links |
| Settings load | n/a | ❌ no loading state (modal opens populated or toasts fail) | ⚠️ "Failed to load settings" toast | modal opens |
| Model fetch | ⚠️ empty select | ⚠️ "Fetching..." text only | ✅ "✗ msg" inline | ✅ "✓ N models" |
| Jira test | n/a | ✅ "Testing…" + disabled btn | ✅ inline classified | ✅ "✓ Connected as X" |
| Upload | n/a | ❌ no progress/spinner (§2a) | ⚠️ generic toast on 400/413 | ✅ filename shown |

Legend: ✅ present & decent · ⚠️ present but weak · ❌ missing.

---

## 8. Cross-cutting theme / systemic observations (redesign priorities)

1. **Blocking overlays are the #1 UX debt.** Extraction AND push both hijack the whole app with a fixed full-screen modal. The rework should make long-running work a **non-blocking, per-session, dismissible progress surface** (drawer/inline banner + a session-level status pill) so users can keep working, switch sessions, and let jobs run in the background.
2. **State lives in DOM + closure vars, not a real store.** `lastOp`, `lastPushRequest`, `jiraConfigured`, `selectedFile`, expand/collapse, dirty edits, recovery-action — all imperative. Keeping nanostores but modeling the app as explicit machines (session lifecycle: `idle → uploading → running → review → pushing → done|error`) is the core migration win. React + `@nanostores/react` makes the state→view binding declarative and kills the `_recoveryBound`/`patchTaskCard`/`innerHTML=''` plumbing.
3. **Component boundaries are begging to be extracted** (drop-in shadcn/beUI targets): TaskCard, TaskGroup, StatusBadge/ConfidenceBadge/FlagBadge, ProgressPanel + LogConsole, SettingsDialog (tabbed: LLM | Jira), ConfirmDialog, Toast, SessionPicker/SessionList, PushResults, EmptyState, Skeleton, ListEditor (for AC/deliverables line-items), Loader (already framework-agnostic via `motion`, `loaders.js` — the one thing that ports cleanly).
4. **The sidebar is overloaded.** Split concerns: a slim nav/session rail, a dedicated review workspace (list + filters as a proper toolbar), and modal/drawer settings. Overview metrics belong as a compact header strip, not stacked in the sidebar.
5. **Review ergonomics need real editing primitives.** List fields as chip/line editors, dirty-state + autosave-or-warn, bulk multi-select + batch approve/reject, cross-task search, and stable scroll/expand across filter changes.
6. **Progress is under-informative.** Named pipeline phases (index/extract/dedup/gap-recovery), elapsed timer/ETA, and a structured (severity-tagged) log console instead of a raw text dump.
7. **Error copy + recovery should be first-class and declarative**, keyed off `error_class`, with human copy (not raw exceptions), field-specific deep-links for USER_FIXABLE, and a genuine terminal/offline state instead of infinite "Reconnecting…".
8. **Backend data-model gaps to flag** (not fixable in the view alone): per-task push failure detail isn't persisted (only aggregate) — the results UX is capped by this; and `current_step` is a bare int with no phase name.

---

## Backend contract quick-reference (what the view has to work with)

- `GET /api/status` → `ProcessingStatus` (`server.py:192-206`): `is_running, current_step, message, progress(0..1), error, error_class(TRANSIENT|USER_FIXABLE|TERMINAL), run_id, kind("pipeline"|"jira_push"), logs[<=50]`.
- `GET /api/tasks` → `{ tasks[], config{}, env_defaults{jira_project_key, jira_server} }` (`server.py:484-493`). Tasks carry `id, title, short_description, use_case, acceptance_criteria[], considerations_constraints[], deliverables[], status, confidence(0..1), flags[], source_refs[{section_title,page_start,page_end}], jira_issue_key, push_result{success,jira_issue_key,jira_issue_url,error,error_class,warning}`.
- `GET /api/sessions` → `[{run_id, filename, created_at, owner_id?}]` newest-first (`server.py:453-482`).
- `POST /api/upload` (PDF only, magic-byte sniff, ≤`SOW_MAX_UPLOAD_MB`=50 → 400/413), `POST /api/process` → `{run_id}`, `POST /api/push?session_id=` → `{started, run_id, message}`, `POST /api/tasks?session_id=`, `POST /api/tasks/approve_all`, `POST /api/jira/test` → `{success,user,server}|{success:false,error,error_class}`, `POST /api/providers/{p}/models`, `GET/POST /api/settings`, `POST /api/cancel/{id}`, `DELETE /api/sessions/{id}`, `GET /api/csrf`.
- Auth/CSRF only enforced when auth is configured; single-user mode is transparent (`server.py:233-268`). The React fetch layer already handles CSRF + 401 (`api.js:135-226`) — keep `api.js` as-is.
