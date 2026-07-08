# UI Test Inventory — React Migration Categorization

Scope: `ui/src/{api,modals,polling,render,state,loaders}.test.js`. Runner today is
**vitest 4.1.9 + jsdom 29** (`ui/package.json` devDeps; `npm test` → `vitest run`).
No config file exists — vitest defaults to jsdom-per-file via the `@vitest-environment jsdom`
pragma (loaders.test.js:1) and implicit jsdom elsewhere (all files touch `document`).

**Exact test count: 111** (grep `^\s*(it|test)\(`):
api 30 · render 32 · loaders 15 · modals 14 · polling 13 · state 7 = 111.

## The one fact that shapes the whole strategy

The current UI is **vanilla JS that hand-builds DOM nodes** (`render.js` clones a
`<template>` and sets `.textContent`/`.className`; `modals.js`/`polling.js` create elements
imperatively). So the tests are NOT React-component tests today — they split cleanly into:

1. **Pure view-model logic** — functions that take data and return data (no DOM). These
   survive a React port verbatim, because the whole point of the migration is to KEEP
   `api.js`/`state.js` and lift this logic out of the render layer.
2. **Imperative DOM-builder tests** — assert against nodes produced by `render.js` /
   `modals.js` / `polling.js` / `loaders.js` builder functions. These break the moment those
   builders become JSX components; they need a React Testing Library (RTL) rewrite that
   renders the component and asserts on the resulting tree.

There are essentially **no tests that would survive unchanged AND already look like RTL** —
the DOM ones all reach into module-level imperative builders that won't exist post-port.

## Per-file breakdown

### `state.test.js` — 7 tests — 100% PURE (survives as-is)
Tests the nanostores atoms + setters (`$activeSessionId`, `$taskData`, `$pollStatus`,
`setActiveSession`, etc.) and `sessionStorage` persistence. No DOM rendering at all —
touches `sessionStorage` only (state.test.js:20). Since the plan keeps nanostores
(`@nanostores/react` is view-only sugar), **this file needs zero changes**. The store IS the
port-invariant layer.

### `api.test.js` — 30 tests — 100% PURE (survives as-is)
Every test hits an exported pure/async function from `api.js`:
- View-model logic: `TASK_STATUS`, `STATUS_BUCKET`/`bucketOf`, `computeStats`,
  `shouldShowTask`, `statusDisplay`, `pushPreflight`, `decideSessionSwitch`,
  `containerLabelFor`/`childLabelFor` (api.test.js:19–213). Pure data→data.
- Network layer: `apiFetch` CSRF/credentials/401 behaviour and `testJiraConnection`
  (api.test.js:225–357), driven by `vi.stubGlobal('fetch', …)` and `document.cookie`.
  These test the fetch wrapper, which the plan explicitly KEEPS — no React involved.
The lone DOM touch is `document.cookie` (a jsdom global, not rendering). **No rewrite.**

### `polling.test.js` — 13 tests — 6 PURE / 7 DOM-render
- **Pure (6):** the `errorRecovery(error_class) → {chipLabel, chipClass, action, actionLabel}`
  mapping incl. case-insensitivity and fallbacks (polling.test.js:11–56). Pure lookup —
  survives as-is.
- **DOM-render (7):** `renderFailureRecovery` / `clearFailureRecovery` build & mutate a
  `.progress-card` DOM subtree, insert `.sev-chip` before a message node, and wire a passed-in
  `<button>` to `onRetry`/`onOpenSettings`/`onDismiss` handlers (polling.test.js:58–157).
  In React this becomes a `<FailureRecovery>` component + the pure `errorRecovery` helper;
  the 7 DOM assertions (chip position, no-duplicate re-render, button-wiring, the "clear then
  click does nothing" regression at :142) get rewritten with `render()` + `user-event` clicks.

### `modals.test.js` — 14 tests — 0 PURE / 14 DOM-render
All 14 exercise imperative dialog behaviour: `openModal`/`closeModal` set `role="dialog"`,
`aria-modal`, `aria-labelledby`, move/restore focus, trap Tab/Shift+Tab, close on Escape,
guard backdrop-vs-content clicks (modals.test.js:36–121); `showConfirm` builds a themed
overlay and resolves a promise on button/Escape (:123–161); `runJiraConnectionTest` renders
success/failure/transient/thrown states into a status `<span>` with escaping (:166–230).
Post-port these become a `<Modal>`/`<ConfirmDialog>` component (or a shadcn `Dialog`, which
brings its own focus-trap/Escape). **Full RTL rewrite** — and note several assertions
(focus trap, `aria-modal`, backdrop guard) may be **deleted entirely** if shadcn's Dialog
owns that behaviour, converting them from "test our code" to "trust the library."

### `render.test.js` — 32 tests — 12 PURE / 20 DOM-render
This is the heaviest rewrite file. Split:
- **Pure (12):** `escapeHtml` (:119), the whole `resolveTriageAction`/`isTypingTarget`
  keyboard-dispatch suite (:214–252, 7 tests — `isTypingTarget` touches DOM element `.tagName`
  but is trivially pure-ish and portable), and `sortTasksByConfidence` non-mutating sort
  (:312). These are decision helpers that stay as plain functions in React.
- **DOM-render (20):** everything driven by `renderTasks`/`patchTaskCard`/`renderPushResults`
  against the `mountDom()` `<template>` scaffold (:29) — XSS-inert section titles (:102),
  collapsible header a11y + Enter/Space toggle (:124), in-place patch preserving sibling
  expanded state (:167, the ui-11 "no innerHTML wipe" — a React reconciliation concern now),
  confidence badge rendering (:324), push-results panel links/XSS (:349), and the entire
  FE-1 per-task push-chip suite incl. anchor-tabindex/click-doesn't-toggle a11y and
  javascript:-scheme XSS guards (:408–622, 10 tests). All become `<TaskCard>` /
  `<PushResultsPanel>` / `<PushChip>` component tests under RTL. The `mountDom()` template
  and `markTasksLoaded()` scaffolding (:26–99) is thrown away — React renders the tree.

### `loaders.test.js` — 15 tests — 3 PURE / 12 DOM-render (special: `motion` mock)
- **Pure-ish (3):** `LOADER_TYPES` export shape, `createLoader('bad')` throws,
  and the uniform-handle contract (`.el/.start/.stop/.destroy`, role="status")
  (loaders.test.js:20–40). The handle-shape one touches `.el instanceof HTMLElement`.
- **DOM/animation (12):** dot/row/ball counts, `animate` call-counts + `.stop()` on destroy,
  idempotent start, scramble timer reveal with `vi.useFakeTimers()`, and
  prefers-reduced-motion behaviour (:42–149). These are the trickiest to port because
  `loaders.js` is imperative DOM+WAAPI and the tests **mock the `motion` package**
  (`vi.mock('motion', …)` at :7). Two realistic outcomes: (a) keep loaders as imperative
  helpers mounted via a thin `useEffect` wrapper and keep most tests nearly as-is, or
  (b) rebuild as Framer-Motion React components and rewrite these as RTL + mocked-motion tests.
  Recommend (a) short-term — the imperative loader already has a clean lifecycle handle.

## Bucket totals

| File | Tests | Pure (as-is) | DOM-render (RTL rewrite) |
|------|------:|-------------:|-------------------------:|
| state    |   7 |  7 | 0 |
| api      |  30 | 30 | 0 |
| polling  |  13 |  6 | 7 |
| modals   |  14 |  0 | 14 |
| render   |  32 | 12 | 20 |
| loaders  |  15 |  3 | 12 |
| **Total**| **111** | **58** | **53** |

~58 tests (52%) are pure view-model/network/store logic and **survive the React port
unchanged**. ~53 tests (48%) assert against imperatively-built DOM and need an RTL rewrite
(some modal/focus-trap ones may instead be *deleted* if shadcn owns the behaviour).

## Recommended migration strategy

1. **Keep the runner. Add three deps.** Stay on vitest + jsdom (already installed). Add
   `@testing-library/react`, `@testing-library/user-event`, `@testing-library/jest-dom`.
   Add `@vitejs/plugin-react` to the (currently absent) `vite.config`, and a
   `vitest.config`/`test` block with `environment: 'jsdom'`, `globals: true`, and a setup
   file importing `@testing-library/jest-dom` + `afterEach(cleanup)`. This is the standard
   React-on-vitest stack — no runner swap, no jest.

2. **Migrate the two pure files first, for free.** `state.test.js` and `api.test.js`
   (37 tests) need no edits — run them green against the ported code on day one to prove the
   store + api layer is truly port-invariant. This de-risks the whole migration.

3. **Extract the 21 remaining pure helpers before touching components.** The pure tests
   inside render/polling/loaders (`escapeHtml`, `resolveTriageAction`, `isTypingTarget`,
   `sortTasksByConfidence`, `errorRecovery`, `LOADER_TYPES`/factory contract) should move to a
   `lib/`/`viewmodel` module and keep their existing tests verbatim. Do this as a pre-React
   refactor so 79 of 111 tests (37 + ~42 pure) are green *before* any JSX exists.

4. **Rewrite the 53 DOM tests component-by-component with RTL + user-event.** Pattern:
   `render(<TaskCard task={…} onSave={fn} />)` → assert via `screen.getByRole('button')`,
   `screen.getByText`, then `await userEvent.keyboard('{Enter}')` / `userEvent.click`.
   Replace raw `dispatchEvent(new KeyboardEvent)` (render.test.js:75, modals.test.js:25) with
   `user-event`, which fires the full event sequence React expects. The a11y assertions
   (role="button", aria-expanded, aria-modal, aria-labelledby) map 1:1 onto RTL `getByRole`
   queries and actually get *cleaner*.

5. **Preserve the security tests exactly.** The XSS-inert / javascript:-scheme / escaping
   assertions (render.test.js:102/388/575/599, modals.test.js:177/194) are behaviour
   contracts, not implementation details — port them onto the new components unchanged. React
   auto-escapes text, so several become near-trivial, but keep them to lock the href-scheme
   allowlist (`renderPushResults`/`buildPushChip` reject `javascript:`).

6. **Loaders: wrap, don't rewrite (initially).** Mount the existing imperative `createLoader`
   handle in a `useEffect` and keep the `vi.mock('motion')` + fake-timer tests as-is; only
   rewrite if/when loaders become true Framer-Motion components. Cheapest path, preserves
   12 fiddly animation tests.

7. **Sequencing / effort.** Green-for-free: ~37. Cheap extract-and-keep: ~21. Genuine RTL
   rewrites: ~33 (render 20 + polling 7 + minus overlap) plus the 14 modal tests, of which
   several may be dropped in favour of trusting shadcn `Dialog`. Net real rewrite effort is
   the ~34 TaskCard/PushChip/PushResults/FailureRecovery tests; the modal focus-trap suite is
   the one place to consciously decide "test our modal" vs "delete and trust shadcn."
