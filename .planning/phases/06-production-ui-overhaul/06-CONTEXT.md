# Phase 6 Context: Production UI Overhaul

## Goal
Redesign the existing functional UI into a professional, high-quality production application. The current UI is functional but lacks polish, consistent spacing, and modern design system components.

## Requirements Mapping
- **UI-01**: Implement a modern, production-grade layout with cohesive typography and spacing.
- **UI-02**: Improve empty states and visual feedback mechanisms (toast notifications, spinners).
- **UI-03**: Add dark mode toggle and responsive design layout.

## Current State & Recent Changes
- The UI currently lives in `ui/index.html`, `ui/styles.css`, and `ui/app.js`.
- It uses basic HTML/CSS with some inline styles.
- An audit (Phase 4) revealed issues with typography consistency, missing accessible labels on the theme toggle, and a lack of a proper empty state when no SOW is loaded.

## Blockers & Concerns
- The current implementation relies on vanilla JavaScript. We need to maintain this architecture without introducing large frontend frameworks like React or Vue unless strictly necessary, to keep the portable extraction engine simple.
- Changes must not break existing functionality (Jira pushing, provider switching, configuration).

## Success Criteria
1. The UI looks professional and uses modern design system components (via updated CSS or a lightweight framework/component library).
2. The empty state clearly guides the user to upload a document.
3. Typography is consistent and accessible across the application.
4. Dark mode works flawlessly.
