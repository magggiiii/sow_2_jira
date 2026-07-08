# UI Rework Direction — Lane: Editorial / Document-Review Workspace

Concept name: **The Reading Room**

## Scene sentence (forces light vs dark)
A delivery lead sits at a wide oak desk mid-morning, a printed Statement of Work
open under a warm desk lamp, marking deliverables in the margin with a felt-tip
pen — the light is paper-bright and even, the mood is unhurried close reading.
→ This is a *daylight reading* activity, not a night-ops console. It FORCES a
**light, warm-paper** base. Choosing dark here would be the category reflex; the
work itself (reading long prose, editing acceptance criteria for minutes at a
time) is served by paper contrast, not by a glowing terminal.

## Register
PRODUCT. Design serves the reading-and-marking task. Chrome recedes; the SOW
content and the task prose it produces are the loudest things on screen. Density
is controlled per zone (dense in the section rail, generous in the reading
column). Distinctive via typography and margin structure, not via panels.

## Theme + color strategy — RESTRAINED (near-monochrome + one ink accent)
OKLCH; every neutral tinted toward the brand hue (warm ink-blue `~245°`), so no
neutral is ever pure. Base is warm paper, not white; ink is near-black blue, not
`#000`. Status colors are the ONLY saturated pigments and they read like
proof-reader's marks, used sparingly on edges/dots/underlines — never as fills
behind prose.

- `--paper`      oklch(97.5% 0.006 250)   page (warm off-white, tinted to ink)
- `--paper-sunk` oklch(95% 0.008 250)     rail / recessed zones
- `--ink`        oklch(24% 0.03 250)      primary text (blue-black, not #000)
- `--ink-soft`   oklch(44% 0.02 250)      secondary prose
- `--ink-faint`  oklch(62% 0.015 250)     captions / page refs
- `--rule`       oklch(89% 0.01 250)      hairline rules / borders
- `--accent`     oklch(52% 0.15 250)      ink-blue — links, focus, the one CTA
- proof marks (edges/dots only, never prose fills):
  - approved  oklch(58% 0.12 155) green ink
  - flag/pending oklch(70% 0.14 75) amber
  - rejected  oklch(58% 0.17 25) red ink
  - pushed    oklch(52% 0.15 250) = accent (shipped == of-record)
Confidence is shown as a marginal density mark (a short vertical "ink bar" whose
opacity tracks confidence) + the numeric `Conf 62%` label already in the data —
NOT a colored pill grid.
Dark mode is a *deferred* inversion (lamp-off "evening" paper), not the default.

## Typography — characterful, replace Space Grotesk
Space Grotesk is a geometric-grotesk reflex default and reads UI-ish, not
document-ish. Replace with an editorial pairing:
- **Headings / section titles / task titles:** a transitional serif with real
  voice — **Fraunces** (opsz variable; use higher optical size on H1) or
  **Newsreader**. This is the "manuscript" signal.
- **Body / task prose (descriptions, use case, AC):** **Source Serif 4** or
  **Newsreader** for reading columns — serifs earn their keep in long prose.
- **UI meta / labels / rail / numbers:** one grotesque for chrome only —
  **Inter** (or IBM Plex Sans) — tight, quiet, tabular numerals for page refs
  and counts.
- Scale ratio 1.333 (perfect fourth, > 1.25): 13 / 14 / 16(body) / 18 / 24 / 32 / 43.
  Body prose set at **16px / 1.6 line-height** and constrained to **68ch** reading
  measure. Hierarchy comes from serif-vs-sans + size + weight (400 body vs 560
  titles), not from color or boxes.

## Layout + density — margins over cards
Anti-card. The screen is a **document with margins**, not a grid of panels.
- Three-zone reading layout: **[left section rail | center reading column | right
  margin ]**. The center column is the manuscript; the right margin holds the
  proof-reader's controls (approve/reject/flag, source page ref, confidence
  mark) so actions sit *beside* the prose like margin notes, never stacked in a
  card footer.
- SOW sections become **document parts** with numbered running heads
  (`§2 · Platform Scope`), separated by generous whitespace and a single hairline
  rule — not each wrapped in its own bordered box. Rhythm via varied vertical
  space (32–64px between parts, 16px within a task).
- A task is an **entry in the manuscript**: title as a serif subhead, prose
  fields flowing beneath, editable in place (contenteditable-styled fields, not
  form boxes). Selecting a task lifts it (subtle elevation of the *row*, not a
  nested card) and reveals its margin controls.
- One persistent **top matter bar** (thin) for run identity / session switch /
  push — no boxed toolbar; it reads like a document header with a baseline rule.
- Explicitly avoid: identical card grids, nested cards, side-stripe accent
  borders, one big container wrapping everything, the hero-metric block.

## Motion — ease-out, high-impact, few
- Curves: quart/quint ease-out only (`cubic-bezier(0.22,1,0.36,1)`), 180–260ms.
  No bounce, no elastic. Never animate width/height/top/left — only transform +
  opacity (the current grid-rows expand trick is fine as a one-off; prefer
  transform-based reveals for the margin controls).
- High-impact moments (spend budget here): (1) **task settle** — on approve, the
  entry's margin gets a single green ink underline that draws left-to-right once
  (Framer `useAnimate` stroke) then the row eases up the list; (2) **run/indexing
  "page turn"** — the progress view reads like a page being scanned, a hairline
  sweep down the paper with the step name typeset large. (3) push success: pushed
  entries get their key stamped in as an of-record margin mark.
- Everything else (hover, focus, filter) is a quiet 120ms opacity/position shift.
  Respect `prefers-reduced-motion` (keep the existing pulse fallback for loaders).
- Keep the beUI motion loaders but retint to ink-blue accent; the scramble loader
  fits the "scanning text" metaphor for indexing.

## Key screens (3)

### 1. Review Workspace (the core — replaces sidebar+task-list)
Left **section rail** (paper-sunk, ~260px): SOW parts as a numbered contents list
(`§1 Scope … §7 Support`), each with a tiny count + approved fraction and a
confidence density mark; clicking scrolls the reading column. Filters live here
as understated toggles (text + check, not chips). Center **reading column** (max
68ch, centered): the SOW's sections rendered as running heads with their tasks as
manuscript entries — serif title, then Short Description / Use Case / Acceptance
Criteria / Considerations / Deliverables as flowing labeled prose blocks,
editable in place. Right **margin** (per focused entry): source page ref
(`pp. 12–14`) linking the provenance snippet, confidence bar, and the
approve / reject / flag / save controls as small ghost text-buttons stacked like
annotations. Keyboard triage (j/k/a/r/e — already in render.js) is first-class
and shown as a quiet legend in the top matter. Stats (total/approved/pending/
pushed) live as a single typeset **running tally** in the top matter
(`142 deliverables · 88 approved · 12 to push`), NOT a metric-card grid.

### 2. Run / Progress (indexing + extraction, long-running, polled)
Full-column "manuscript being scanned" view, not a modal overlay. The paper shows
the current step name typeset large in Fraunces (`Indexing document structure` →
`Extracting deliverables` → `Reconciling duplicates`), a **hairline scan line**
easing down the page, a thin baseline progress rule with the % as a tabular
figure, and the streaming log set as small monospaced marginalia in `--ink-faint`
(the log console already exists — restyle, don't rebuild). Cancel is a quiet
ghost; on failure the severity chip (transient/user-fixable/terminal, already
mapped in polling.js) appears as a stamped margin mark with its recovery action
as the single accent button. This is a high-impact moment screen — the one place
we spend motion budget.

### 3. Settings (LLM + Jira) — as a document form, not a modal
Move settings out of the `progress-overlay` modal into a **routed page** (React
Router) styled as a form on paper: labeled fields in a single 68ch column with
generous spacing, section headings in serif (`Language Model`, `Jira
Connection`), provider/model selects and the Fetch Models / Test Connection
actions inline. Password fields, Azure conditional fields, and OpenRouter model
help all carry over. Modal-as-first-thought is banned; this becomes a real
destination. (Confirm dialogs for destructive session delete may remain a small
centered Radix `AlertDialog` — the one justified modal.)

## Component-lib approach
React + Vite (keep `@nanostores/react` over the existing stores; keep `api.js`
and FastAPI untouched). **Tailwind v4** with the OKLCH palette + type scale as
`@theme` tokens (design system == tokens). **shadcn/ui on Radix primitives** for
the accessible, unstyled behavior we already hand-rolled (disclosure/collapsible
for task entries, Select for provider/hierarchy, Tabs for section rail, Tooltip,
AlertDialog for delete, Toast) — restyled hard toward the editorial look so they
never read as default shadcn. **Framer Motion** for the three high-impact moments
+ the `useAnimate` ink-underline; **beUI** motion loaders retained (retinted) for
indexing states. shadcn/Radix + Framer + beUI are all drop-in on this Tailwind
token base, satisfying the migration goal.

## AI-slop test — passes both altitudes
1. First-order: the category "dev/PM tool" reflexively yields dark + blue + a
   grotesque (exactly today's dark/orange/Space Grotesk). We commit to a
   **light warm-paper** base with **serif** typesetting — the opposite of the
   guessable answer.
2. Second-order: the anti-reflex to "dev tool that isn't SaaS-dark" is
   terminal-green or brutalist-mono. We reject both — no monospace-as-primary, no
   green phosphor. The intentional commitment is a **reading-room / manuscript**
   metaphor grounded in the real data (this app literally turns a *document* into
   reviewable items, and the task fields are prose), with color used only as a
   proof-reader's marks. That is defensible from the domain, not from a trend.

## Tradeoffs (deliberate, honestly stated)
- **Serif body + light theme** risks feeling "less like a tool" to users
  conditioned on dark IDEs; mitigated because the reading-heavy task genuinely
  benefits, and dark ("lamp-off") mode is a planned inversion for anyone who
  insists. Recommend shipping light-first.
- **Three-zone margin layout** is more layout work than restyling cards, and the
  right-margin controls need care on tablet (<1024px they collapse below the
  entry as an inline control row; <768px the section rail becomes a top sheet).
- **In-place editable prose** (vs form inputs) is the highest-effort piece and
  the biggest departure from `render.js`; if scope is tight, phase 1 can keep
  labeled inputs but drop the card chrome and adopt the reading column + margin
  controls first — the visual identity lands without the contenteditable work.
- Moving Settings from modal to a route touches routing that doesn't exist yet;
  low risk but new (React Router add).
