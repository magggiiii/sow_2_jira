# UI/UX Direction — Operator Console lane ("PLATEN")

Grounded in real flows:
- Sidebar SPA: session switch + extraction config + metrics + filters + global actions (`ui/index.html:13-240`)
- Grouped task cards, inline edit, confidence badge, status label, push chip, keyboard triage a/r/e/j/k (`ui/src/render.js:71-94, 521-696`)
- Polling progress overlay: step title, bar, log console, error-class recovery (`ui/src/polling.js:141-234`)
- Status contract: `current_step`, `message`, `progress`, `logs`, `run_id`, `kind`, `error_class` (`ui/server.py:192-206`)
- Task states OPEN/CLOSED/MERGED/REJECTED/APPROVED/PUSHED (`models/schemas.py:133-139`)

See DIR_SCHEMA structured output for the full spec. Summary below.

## Concept: PLATEN
A precise, keyboard-first operator console. The register is a control surface for a technician
who runs many extractions and triages hundreds of task rows fast. Not terminal-green, not
SaaS-dark-blue. Light, high-contrast "instrument panel on paper" with a single sodium-amber
signal accent reserved for live/actionable state.

## Scene sentence (forces light)
"A dispatcher at a bright morning control desk under even overhead light, ledger open, a single
amber lamp the only thing that glows — everything else is legible paper, nothing decorative."

That scene forces LIGHT: a control desk is read for hours; glare-free legibility beats mood.
The one glowing amber lamp becomes the sole accent, earned by live/actionable state only.
