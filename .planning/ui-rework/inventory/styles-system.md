# Design System Inventory — `styles-system`

Source of truth: `ui/styles.css` (1050 lines) + `ui/index.html` (307 lines).
Font: **Space Grotesk** (loaded from Google Fonts, `index.html:8-10`), fallback `system-ui, sans-serif`.
Theme: single dark theme, no light mode. Accent = **vibrant orange** in oklch.

This doc captures the CURRENT system so a React + Tailwind/shadcn migration can map each token/class family to its new home. Verdicts: KEEP (port verbatim as a token/primitive), REVISE (concept good, values dated/thin), DROP (generic; shadcn/beUI supersedes).

---

## 1. Design Tokens (`:root`, `styles.css:3-73`)

### 1.1 Color tokens
The palette is **oklch-based for neutrals + orange accent**, and **hardcoded hex/rgba for semantic status colors**. That split is the single biggest inconsistency to resolve on migration.

| Token | Value | Role | Verdict |
|---|---|---|---|
| `--bg-app` | `oklch(15% 0 0)` | deep near-black app bg | KEEP (→ `background`) |
| `--bg-surface` | `oklch(20% 0 0)` | card/sidebar surface | KEEP (→ `card`) |
| `--bg-surface-hover` | `oklch(25% 0 0)` | hover/raised surface | KEEP (→ `muted`/`accent`) |
| `--border-subtle` | `oklch(25% 0 0)` | hairline dividers | KEEP (→ `border`) |
| `--border-strong` | `oklch(30% 0 0)` | input borders | KEEP (→ `input`) |
| `--border-focus` | `oklch(65% 0.2 45)` | focus ring (== accent) | KEEP (→ `ring`) |
| `--text-primary` | `#ffffff` | headings/values | REVISE — pure white on near-black is harsh; move to `oklch(~98%)` and unify onto oklch |
| `--text-secondary` | `#a3a3a3` | body copy | REVISE — hardcoded hex, break oklch scale; → `foreground`/`muted-foreground` |
| `--text-tertiary` | `#737373` | placeholders/empty | REVISE — same, → `muted-foreground` |
| `--accent-primary` | `oklch(65% 0.2 45)` | orange brand accent | KEEP (→ `primary`) — this IS the brand |
| `--accent-primary-hover` | `oklch(70% 0.2 45)` | accent hover | KEEP |
| `--accent-primary-text` | `#ffffff` | text on accent | KEEP (→ `primary-foreground`) |
| `--success` | `#10b981` (emerald 500) | approved | REVISE — generic Tailwind hex; keep hue, express in oklch + `-bg` companion |
| `--error` | `#ef4444` (red 500) | rejected/terminal | REVISE — same |
| `--warning` | `#f59e0b` (amber 500) | pending/transient | REVISE — same |
| `--info` | `#3b82f6` (blue 500) | info (unused visually) | DROP-ish — defined but barely used |
| `--{success,error,warning,info}-bg` | `rgba(...,0.1)` tint | soft-fill chip bg | KEEP concept (→ derive via `color-mix`/opacity in the new system) |

Semantic **status tokens** (aliases, `styles.css:52-57`) — these are excellent domain-modeling and must survive the migration verbatim as semantic aliases:
- `--status-approved → success`, `--status-rejected → error`, `--status-pending → warning`, `--status-pushed → accent-primary`, `--status-closed → text-secondary`.
- Design intent explicitly noted: "so no dot is colorless" — every `TaskStatus` maps to a color, and the `.status-indicator` default is `--text-tertiary` so an unknown status is never invisible (`styles.css:430-438`).

**Error-severity tokens** (`styles.css:59-63`) map to `ErrorClass`: `--sev-transient` (amber/retryable), `--sev-fixable` (orange/user-must-fix), `--sev-terminal` (red/permanent). KEEP — these drive real failure UX and are cited across the app.

### 1.2 Type scale (`styles.css:4-13`)
Modular scale, ratio **1.25 (major third)**, base 1rem:
`--text-xs .75rem` · `sm .875rem` · `base 1rem` · `lg 1.25rem` · `xl 1.563rem` · `2xl 1.953rem` · `3xl 2.441rem` · `4xl 3.052rem`.
Verdict: KEEP the scale (near-identical to Tailwind's default up to `lg`, diverges above). `4xl` is defined but never used. Only weights 400/500/600/700 are loaded and used.

### 1.3 Spacing scale (`styles.css:15-24`)
Named by **px value at 4pt grid** but stored in rem, and the larger steps are **fluid `clamp()`**:
`--space-2 .125rem` · `4 .25rem` · `8 .5rem` · `12 .75rem` · `16 clamp(1rem,2vw,1.25rem)` · `24 clamp(1.5rem,3vw,2rem)` · `32 clamp(2rem,4vw,3rem)` · `48 clamp(3rem,6vw,4rem)` · `64 clamp(4rem,8vw,5rem)`.
Verdict: REVISE naming (the `--space-16 = 1rem` naming clashes with Tailwind's `space-16 = 4rem`; will confuse anyone on shadcn). The **fluid clamp() steps are a genuinely nice touch** worth preserving as fluid utilities, but standard shadcn/Tailwind uses static spacing — decide whether to keep fluid or go static.

### 1.4 Radii (`styles.css:68-70`)
`--radius-sm 4px` · `--radius-md 8px` · `--radius-lg 12px`. Verdict: KEEP → maps cleanly to shadcn's `--radius` convention (shadcn derives sm/md/lg from one `--radius: 0.5rem`).

### 1.5 Shadows (`styles.css:65-66`)
`--shadow-sm: 0 4px 6px rgba(0,0,0,.5)` · `--shadow-md: 0 10px 40px -10px rgba(0,0,0,.7)`. Verdict: REVISE — very heavy/opaque for a dark theme (0.5–0.7 alpha reads as a black smear on `oklch(15%)`). Thin these out; add a subtle border-glow option for elevation instead.

### 1.6 Motion (`styles.css:72`)
Single token `--transition-ease: 300ms cubic-bezier(0.16, 1, 0.3, 1)` — an "expo-out" ease used everywhere. Verdict: KEEP the curve (nice, decelerating), but 300ms on hover states feels sluggish; split into fast (150ms hover) vs deliberate (300ms layout) tokens for Framer-Motion parity.

---

## 2. Layout Structure

- **Root shell**: `.app-layout` CSS grid `300px 1fr`, `min-height:100vh` (`styles.css:100-104`). Sidebar fixed 300px, main fluid.
- **Sidebar** `.sidebar` (`107-130`): sticky full-height column, `overflow-y:auto`, thin custom scrollbar (`119-121`), flex-column with `--space-32` gaps. `.sidebar-header` carries the signature **2px orange left-border accent line** (`129`) — this is the one distinctive brand gesture in the chrome.
- **Main** `.main-content` (`295-300`): `max-width:1100px`, centered, `--space-64` padding.
- Sidebar is a dense **control panel**: New Session → session switcher (+ delete icon) → Extraction Config (upload zone, hierarchy/project/max-nodes) → Overview metrics → Filters → Global Actions. Everything separated by `.divider` (1px, `132-136`).
- **Responsive** (`680-709`): 1024px → sidebar shrinks to 240px; 768px → single column, and cleverly **reorders task list above sidebar** via `order` (task-list-first on mobile); 400px → full-width buttons. Verdict: KEEP the responsive intent; the reorder trick is smart and must be preserved in the React layout (likely a `lg:` breakpoint + flex order).

---

## 3. Component Class Families

| Family | Classes | Notes / Verdict |
|---|---|---|
| **Buttons** | `.btn` base + `.btn-primary/.btn-success/.btn-danger/.btn-outline/.btn-secondary/.btn-ghost/.btn-danger-ghost/.btn-icon/.btn-inline` + modifiers `.btn-block/.btn-mt-8/.btn-mb-16` (`242-292`, `725-770`) | Deliberate **3-tier hierarchy** (primary/secondary/ghost) documented inline (`725-728`). `min-height:44px` accessible touch target on `.btn`. `.btn-success`/`.btn-danger` are soft-tint-until-hover (nice). Verdict: KEEP the semantics → maps directly to shadcn `Button` variants (`default/secondary/ghost/destructive/outline` + `size="icon"`). The `-mt-8`/`-block` utility modifiers should become Tailwind classes, not bespoke CSS. |
| **Inputs** | `.input-field` (text/textarea/select), `.checkbox-group` custom checkbox (`178-239`) | Custom checkbox draws its own checkmark via `::after` rotate-border trick. Focus ring is a double box-shadow (`236`). Verdict: KEEP behavior → shadcn `Input`/`Textarea`/`Select`/`Checkbox`. The custom checkmark can be dropped for shadcn's Radix checkbox. |
| **Task cards** | `.task-card` (+`.expanded`), `.task-header`, `.task-title-area`, `.task-body`/`.task-body-content` grid-rows accordion, `.task-details`, `.task-actions` (`397-505`) | The **grid-template-rows: 0fr→1fr accordion** (`461-474`) is a clean height-animation technique. `.task-card:focus-visible` ring for keyboard triage (`831-834`). Verdict: KEEP the accordion concept → in React use Framer-Motion `AnimatePresence`/`layout` or shadcn `Accordion`/`Collapsible` (Radix), which is cleaner than the grid trick. This is the app's central primitive. |
| **Task groups (hierarchy)** | `.task-group`(+`.collapsed`), `.task-group-header` (orange left-border), `.task-group-title/-icon/-label/-meta/-count/-approved`, `.group-chevron`, `.task-group-body` (indented, left-border rail) (`314-395`) | Represents Jira Epic/Story grouping. Left-border-rail indentation. Verdict: KEEP → shadcn `Collapsible` per group. |
| **Status indicator** | `.status-indicator` + `.approved/.rejected/.pending/.closed/.pushed` — 10px dot with matching `box-shadow` glow (`425-438`) | The glowing dot is a signature. Paired with `.status-label` text (`650-656`) for WCAG 1.4.1 (color not sole signal). Verdict: KEEP as a `<StatusDot>` component; the glow is on-brand. |
| **Badges/chips** | `.badge` (pill), `.conf-badge` (+`.conf-low/mid/high`, tabular-nums), `.sev-chip` (+ 3 severity variants), `.task-push-chip` (success/failure), `.task-push-chip-key/-label` (`446-454`, `606-648`, `819-828`) | Rich, domain-specific chip system: confidence band, error severity, per-task Jira push result. All carefully text-labelled (WCAG 1.4.1 comments throughout). Verdict: KEEP the semantics → shadcn `Badge` variants; the WCAG discipline (text carries meaning, color reinforces) must be preserved. |
| **Metrics** | `.metrics-grid` (2-col), `.metric-card` (hover translateY -2px), `.metric-total` (full-span), `.metric-label`, `.metric-value` (`138-175`) | Sidebar stats + reused as generic label/value pair (e.g. form labels reuse `.metric-label`, `index.html:48` etc.). Verdict: REVISE — `.metric-label` is overloaded as both a stat label AND every form field label; split into a proper `<Label>` vs stat `<Metric>` in React. |
| **Upload zone** | `.upload-zone` (+`.drag-over`, `.has-file`, `.upload-zone-highlight` pulse), `.clear-file-btn` (`547-565`, `845-866`) | Dashed dropzone with drag state, file-set state, and an attention **pulse animation** (`857-860`) to nudge users. Verdict: KEEP behavior; the pulse-to-onboard is a nice affordance. |
| **Overlays/modals** | `.progress-overlay` (fixed, backdrop blur), `.progress-card` (also `.settings-card/.confirm-card`), `.modal-field/-last`, `.modal-actions` (`567-588`, `658-663`, `780-843`) | Modals are hand-rolled `.progress-overlay` reused for progress/settings/confirm. `.progress-card` capped `max-height:90vh; overflow:auto; width:min(600px,100vw-2rem)` (`658-663`). Verdict: DROP the hand-rolled overlay → shadcn `Dialog` (Radix) gives focus-trap, ESC, scroll-lock for free. Migration must preserve the confirm-dialog and settings-form field layout. |

---

## 4. Existing UX Affordances (the good stuff — do NOT lose these)

- **Progress overlay** (`index.html:144-163`): loader mount + step title + message + **progress bar** (`.progress-bar-bg`/`.progress-bar-fill` with orange glow, `590-604`) + **percentage** + **live log console** (`.log-console`, monospace, `aria-live=polite`, `804-814`) + Dismiss/Stop actions. This is the long-running-pipeline polling UX. KEEP wholesale.
- **beUI motion loaders** (`styles.css:959-1049`, `ui/src/loaders.js`): three ported loaders — `scramble` (JS glyph cycle), `helix` (paired oscillating dots), `newton` (5-ball cradle) — animated in accent orange via the **`motion` package's vanilla `animate()`** (already a dependency; `loaders.js:11`). All have `prefers-reduced-motion` fallback to an opacity pulse (`1030-1041`). There's a `ui/loaders-demo.html`. **HIGH-VALUE for migration**: these are literally beUI components hand-ported to vanilla; in React they become the real beUI/motion-react components — the migration *simplifies* this code, doesn't rebuild it.
- **Skeletons** (`.skeleton-list`/`.skeleton-card`, `868-886`): shimmer gradient loading cards. KEEP → shadcn `Skeleton`.
- **Toasts** (`.toast-container`/`.toast`, `522-545`; `index.html:242`): bottom-right, orange left-border, `slideIn` keyframe, `aria-live`. Verdict: REVISE → replace with `sonner` (shadcn's toast) for stacking/queue/dismiss for free.
- **Empty states** (`.empty-state`, `.empty-state-onboarding` centered w/ icon, `507-520`, `897-905`): dashed-border panels; onboarding variant has orange icon + CTA. KEEP.
- **First-run de-emphasis** (`body.is-first-run` grays out filters + global actions, `888-895`): progressive disclosure so a new user isn't overwhelmed. Verdict: KEEP — thoughtful onboarding, toggled via `render.js:151` `classList.toggle`.
- **Push-results panel** (`.push-results-*`, `907-957`): success-tinted panel listing pushed Jira keys (linked, monospace) + failure rows. KEEP — this is the payoff screen.
- **a11y helpers**: `.sr-only` (`666-674`), global `:focus-visible` orange ring (`675-678`), card-level focus ring, `aria-live` regions, `role=log/status`, WCAG-1.4.1 comments everywhere. Verdict: KEEP the discipline — this is a genuinely accessibility-conscious codebase; the React rebuild must not regress it.

---

## 5. Overall Assessment: Keep vs Generic/Dated

**Worth keeping (brand + domain equity):**
- The **oklch dark neutral ramp + orange (`oklch 65% 0.2 45`) accent** — distinctive, not the default indigo/violet. This is the identity.
- Space Grotesk with tight negative letter-spacing on headings + italic-secondary `h1 i` treatment (`styles.css:93-96`).
- The signature **orange left-border accent line** on sidebar header + task-group headers.
- **Glowing status dots** and the full **semantic status/severity/confidence token + chip system** — deep domain modeling that generic component libs won't give you.
- The **WCAG-1.4.1 discipline** (color never the sole signal; text labels everywhere).
- **beUI motion loaders + progress/log console** long-running-task UX.
- **First-run de-emphasis** onboarding + fluid `clamp()` spacing.

**Generic / dated / worth revising or dropping:**
- **Token duality**: neutrals/accent in oklch but status colors as raw Tailwind hex (`#10b981`/`#ef4444`/`#f59e0b`/`#3b82f6`) — inconsistent; unify onto oklch (or `color-mix`) so the whole palette is one system.
- **`--info` mostly unused**, `--text-4xl` unused — dead tokens.
- **Heavy black shadows** (0.5–0.7 alpha) read as smears on a dark bg — thin them.
- **Hand-rolled modal overlay** reused for 3 dialogs — replace with Radix/shadcn `Dialog`.
- **Custom toast** — replace with `sonner`.
- **Overloaded `.metric-label`** doing double-duty as form label + stat label.
- **Ad-hoc utility classes** (`.btn-mt-8`, `.btn-mb-16`, `.is-hidden`, `.text-success`) grown organically ("ui-24" cleanup pass) — these are proto-Tailwind; Tailwind supersedes them entirely.
- **Spacing token names** (`--space-16 = 1rem`) collide with Tailwind's numbering — rename to avoid confusion.
- **300ms transition on everything** including hovers — feels slow; split motion tokens.

---

## 6. Migration Mapping (styles → React + Tailwind v4/shadcn + Framer-Motion)

1. **Tokens → `@theme` / CSS vars in shadcn form.** Map current oklch neutrals + orange onto shadcn's semantic slots: `--background`(bg-app) `--card`(bg-surface) `--muted`/`--accent`(bg-surface-hover) `--border`(border-subtle) `--input`(border-strong) `--ring`(border-focus) `--primary`(accent-primary) `--primary-foreground`(accent-primary-text) `--foreground`(text-primary) `--muted-foreground`(text-secondary/tertiary) `--destructive`(error). Keep the **domain semantic aliases** (`--status-*`, `--sev-*`, `--conf-*`) as an extra layer on top — these have no shadcn equivalent and are the app's IP. Unify status colors into oklch while migrating.
2. **Keep Space Grotesk** — set as `--font-sans` in the Tailwind theme; preserve heading letter-spacing + `h1 i` italic treatment as a typography component/utility.
3. **Buttons → shadcn `Button`**: `btn-primary→default`, `btn-secondary→secondary`, `btn-ghost→ghost`, `btn-danger→destructive`, `btn-outline→outline`, `btn-icon→size="icon"`. Retain 44px min touch target. Drop `.btn-mt-8`/`.btn-block` bespoke utilities → Tailwind classes.
4. **Task card accordion → Radix `Collapsible`/`Accordion` + Framer-Motion `layout`** instead of the `grid-rows 0fr→1fr` trick. Preserve `:focus-visible` ring, status dot, chips.
5. **Task-group hierarchy → nested `Collapsible`**, keep orange left-rail + indentation.
6. **Modals → shadcn `Dialog`** (progress, settings, confirm). Preserve field layout (`modal-field`, `modal-actions`) and the `max-height:90vh/overflow:auto` viewport clamp.
7. **Loaders → real beUI / `motion/react` components.** `motion` is already installed; swap the vanilla `animate()` port in `loaders.js` for the React versions — a net code deletion. Preserve `prefers-reduced-motion` fallback.
8. **Skeleton → shadcn `Skeleton`; Toast → `sonner`; Badge/chips → shadcn `Badge` variants** carrying the semantic + WCAG text-label discipline.
9. **Preserve behavioral affordances that aren't components**: first-run de-emphasis (`is-first-run` body class → a store-driven prop/`data-` attr), upload-zone drag/pulse states, progress log console with `aria-live`, mobile task-list-first reorder.
10. **Thin the shadows**, split the single motion token into fast(150ms)/deliberate(300ms), and drop dead tokens (`--info`, `--text-4xl`) on the way through.

---

## 7. Notes for other inventory areas
- `motion` (Framer Motion's framework-agnostic engine) is **already a dependency** (`ui/src/loaders.js:11`, `loaders.test.js:9`) — Framer-Motion in React is a natural continuation, not a new dep.
- No `package.json` found under `ui/` in this checkout (Vite/dist is prebuilt in `ui/dist`); the deps/build config live outside the files in scope for this area — flag for the build/tooling inventory.
- `.planning/ui-reviews/` contains only gitignored local artifacts (two dated dirs `04-2026040...`), no committed prior audit to fold in.
- Rendering is template-clone + `innerHTML` + `classList` toggles driven from `ui/src/render.js` (e.g. `taskListEl.innerHTML=''` at `render.js:301`, status class add at `:534`, expand toggle at `:581`) — the CSS was designed around imperative class toggling; React will replace those toggles with state-driven `className`/variants.
