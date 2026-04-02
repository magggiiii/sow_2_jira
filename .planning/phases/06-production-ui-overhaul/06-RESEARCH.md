# Phase 6: Production UI Overhaul - Research

**Researched:** 2026-04-01
**Domain:** Frontend UI / UX Overhaul
**Confidence:** HIGH

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- Maintain vanilla JavaScript architecture (no large frontend frameworks like React/Vue unless strictly necessary).
- Keep existing portable extraction engine simple.
- Must not break existing functionality (Jira pushing, provider switching, configuration).

### the agent's Discretion
- Approach to implementing modern design system components via updated CSS or a lightweight framework/component library.
- Specific design aesthetic as long as it looks professional.

### Deferred Ideas (OUT OF SCOPE)
- Overhauling backend logic or introducing new UI frameworks.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| UI-01 | Implement a modern, production-grade layout with cohesive typography and spacing. | Enforced by `frontend-design` guidelines: Use modular type scale, fluid sizing (clamp), varied font weights. Replace default fonts with distinctive ones. Refine 4pt grid in `styles.css`. |
| UI-02 | Improve empty states and visual feedback mechanisms (toast notifications, spinners). | Guidelines state: "Design empty states that teach the interface, not just say 'nothing here'." Enhance existing toast and spinner mechanisms with exponential easing and state changes. |
| UI-03 | Add dark mode toggle and responsive design layout. | Existing toggle needs accessible labels. Utilize `@container` queries or fluid media queries. Ensure OKLCH color usage for robust dark/light themes. |
</phase_requirements>

## Summary

The goal of this phase is to refine the existing vanilla HTML/CSS/JS frontend to achieve a production-grade, highly polished aesthetic without introducing heavy frameworks like React or Vue. The current UI already implements a dark theme but relies on basic styling and spacing. By strictly applying the `frontend-design` skill guidelines (e.g., modular typography, intentional spacing rhythms, fluid CSS, OKLCH colors), the UI can achieve the requested professional feel.

**Primary recommendation:** Keep the existing vanilla architecture but heavily refactor `ui/styles.css` using modern CSS features (variables, OKLCH, `clamp()`, grid) and update `ui/index.html` structure. Enhance empty states and feedback interactions in `ui/app.js` while maintaining current event listeners.

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Vanilla HTML/JS | N/A | Structure and interactivity | Required by constraints to maintain simple portable engine |
| Modern CSS (Custom) | N/A | Styling and theming | Native CSS now supports features (OKLCH, container queries, clamp) that negate the need for heavy preprocessors |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| Phosphor/Lucide Icons | N/A | Scalable SVG icons | To replace any inline SVGs with a cohesive icon set (optional, inline SVGs currently used) |

**Installation:**
No new `npm` packages required based on constraints.

## Architecture Patterns

### Recommended Project Structure
```
ui/
├── index.html        # Refined semantic HTML layout
├── styles.css        # Modular, fluid CSS using OKLCH and variables
└── app.js            # Vanilla JS for logic, adding rich empty states
```

### Pattern 1: Fluid Typography & Spacing
**What:** Using `clamp()` and CSS variables to define a modular type scale and responsive spacing rhythm.
**When to use:** Global design system implementation.
**Example:**
```css
:root {
  --font-sans: 'Cabinet Grotesk', system-ui, sans-serif; /* Distinctive font */
  --space-base: clamp(1rem, 2vw, 1.5rem);
  --text-base: clamp(1rem, 1vw + 0.5rem, 1.125rem);
}
```

### Anti-Patterns to Avoid
- **Generic AI Aesthetics:** Avoid cyan-on-dark, purple-to-blue gradients, glowing accents, glassmorphism, and overused fonts like Inter or Roboto.
- **Heavy Frameworks:** Do not install React, Vue, or large UI component libraries (e.g., Material UI, Bootstrap).
- **Redundant Spacing:** Avoid monotonous layout grids. Create visual rhythm with varying spacing.

## Common Pitfalls

### Pitfall 1: Breaking Vanilla JS Selectors
**What goes wrong:** Restructuring HTML removes IDs or changes class hierarchies, breaking `document.getElementById` calls in `app.js`.
**Why it happens:** Aggressive DOM refactoring without checking `app.js` dependencies.
**How to avoid:** Audit `app.js` for `getEl()` and `querySelector` calls. Preserve all functional IDs and data attributes.

### Pitfall 2: Overlooking Accessibility
**What goes wrong:** Dark mode toggles or new components lack ARIA labels, making them inaccessible.
**Why it happens:** Focusing purely on visual aesthetics.
**How to avoid:** Ensure all interactive elements (buttons, toggles) have `aria-label`, `title`, or screen-reader-only text.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python | Backend / Serving UI | ✓ | 3.9.6 | — |
| Docker | Local testing | ✓ | 29.3.1 | — |
| Node/NPM | Frontend tooling (if any) | ✓ | v25.8.2 | — |

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | Backend: Pytest (implicitly via scripts), Frontend: Manual |
| Config file | none — see Wave 0 |
| Quick run command | `python test_jira_api.py` (ensure backend functional) |
| Full suite command | `make test` (if exists) or manual UI verification |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| UI-01 | Modern cohesive layout | manual | N/A | ❌ Wave 0 |
| UI-02 | Empty states & toasts | manual | N/A | ❌ Wave 0 |
| UI-03 | Dark mode & responsive | manual | N/A | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** Run FastAPI server locally and manually inspect UI changes
- **Per wave merge:** Manual visual regression testing across mobile/desktop views
- **Phase gate:** Verification of all core workflows (upload, parse, review, Jira push) in the new UI.

## Sources

### Primary (HIGH confidence)
- `.planning/phases/06-production-ui-overhaul/06-CONTEXT.md` - Phase constraints.
- `.gemini/skills/frontend-design/SKILL.md` - Distinctive aesthetic guidelines.

### Secondary (MEDIUM confidence)
- `ui/index.html` and `ui/styles.css` - Current architecture assessment.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - Constrained by explicit context.
- Architecture: HIGH - Dictated by vanilla architecture constraints.
- Pitfalls: HIGH - Common issues when refactoring vanilla DOM.

**Research date:** 2026-04-01
**Valid until:** 30 days
