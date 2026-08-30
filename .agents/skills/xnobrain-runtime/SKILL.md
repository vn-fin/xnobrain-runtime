---
name: xnobrain-runtime
description: Implement, diagnose, review, or validate XNOBrain runtime features in the Python/FastAPI Hermes and provider-runtime repository. Use for backend service groups, API contracts, profile persistence, integrations, packaging, and runtime tests.
---

# XNOBrain Runtime

Read `AGENTS.md`, `.agents/rules/01-start-here.md`,
`.agents/rules/02-source-boundaries.md`,
`.agents/rules/04-backend-service-groups.md`, and
`.agents/rules/05-feature-workflow.md` first. Read `docs/architecture.md`,
`docs/api.md`, or `docs/contracts/` when relevant.

1. Trace the existing route, model, operation, service, repository or
   integration, caller, and tests before editing.
2. Give the feature one stable service-group name across routes, models,
   handlers, services, and repositories. Keep composition modules as
   composition only.
3. Keep HTTP translation in handlers, business rules in services, atomic file
   persistence in repositories, and Hermes/central-router adaptation in integrations.
4. Preserve profile isolation, traversal/symlink checks, snapshots, atomic
   writes, streaming events, approval behavior, and optional dependency
   failure semantics.
5. Keep public paths and persisted keys stable unless a migration/compatibility
   change is explicitly part of the task. Never log secrets, prompts, bodies,
   tool arguments, or tool output.
6. Add and run focused source tests in `xnobrain/tests/`, then `make test` when broader source coverage is needed. Do not compile Nuitka artifacts or build images during ordinary development.

Keep UI source in `../xnobrain-ui` and managed services in their owning
repositories. Do not edit `app/`, `dist/`, or `dist/assets/` unless explicitly
requested; use the existing specialized frontend, Hermes, or Tauri skills for
those surfaces.
