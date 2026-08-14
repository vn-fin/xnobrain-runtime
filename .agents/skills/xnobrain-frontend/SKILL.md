---
name: xnobrain-frontend
description: Implement, diagnose, or review the XNOBrain React and TypeScript frontend under src/. Use for API clients, hooks, state, routing, components, localization, accessibility, unit tests, Vite configuration, or frontend builds.
---

# XNOBrain Frontend

Read `AGENTS.md`, `.agents/rules/01-start-here.md`, `.agents/rules/02-source-boundaries.md`, and `.agents/rules/03-trackable-ui-routes.md` first.

1. Trace data from `src/api` through hooks into components before editing.
2. Keep public runtime URLs aligned with `xnobrain/routes/definition.py`; update contract tests with any path change.
3. Reuse domain types and existing request/auth helpers. Do not bypass envelope parsing or token handling.
4. Preserve loading, empty, error, streaming, mobile, keyboard, and screen-reader behavior.
5. Route primary tabs and meaningful selected entities through `useRouter`; keep URL parsing, serialization, direct reload, and Back/Forward restoration symmetrical.
6. Update source tests for changed behavior and run focused Vitest tests plus `npm run build`.

Edit only authored files under `src/` and necessary root configuration. Never patch compiled JavaScript or CSS under `dist/assets`, and do not edit `app/`.
