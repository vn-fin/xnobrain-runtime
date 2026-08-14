---
name: xnobrain-ui
description: Review or refine XNOBrain user interfaces and interaction flows in src/. Use for responsive layouts, connector/settings pages, chat and conversation history, agent navigation, notifications, dialogs, accessibility, visual regressions, or implementing feedback from screenshots and product documents.
---

# XNOBrain UI Review

Read `AGENTS.md`, `.agents/rules/01-start-here.md`, `.agents/rules/02-source-boundaries.md`, and `.agents/rules/03-trackable-ui-routes.md` first.

1. Inspect the referenced screenshot or document and translate each complaint into an observable behavior.
2. Locate the existing component and CSS contract before changing markup. Reuse tokens and established classes.
3. Prioritize task meaning, agent descriptions, status, and time over internal provider or runtime implementation names.
4. Keep dense screens compact without reducing touch targets, focus visibility, text wrapping, or mobile usability.
5. Verify desktop and narrow layouts, keyboard operation, dialogs, empty/error states, and relevant component tests.
6. Keep primary tabs and meaningful selected components trackable in the URL; verify direct links, reload, close-to-parent behavior, and browser Back/Forward.
7. Run focused Vitest tests and `npm run build`; use browser screenshots when a running UI is available.

Make UI changes only in authored `src/` files. Never hand-edit `dist/assets`, and do not edit the separate `app/` tree.
