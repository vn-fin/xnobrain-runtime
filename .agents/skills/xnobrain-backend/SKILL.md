---
name: xnobrain-backend
description: Implement, diagnose, refactor, or review the XNOBrain Python backend under xnobrain/, extensions/, runtime/, and server.py. Use for FastAPI service groups, routes, models, operation handlers, services, repositories, Hermes adapters, MCP, providers, persistence, telemetry, or backend tests.
---

# XNOBrain Backend

Read `AGENTS.md`, `.agents/rules/01-start-here.md`,
`.agents/rules/02-source-boundaries.md`, and
`.agents/rules/04-backend-service-groups.md` first.

1. Trace the matching route, model, operation handler, service, repository or integration, and tests before editing.
2. Keep one service group per same-named file in `routes/`, `models/`, `handlers/operations/`, `services/`, and `repositories/`.
3. Keep `routes/setup.py`, `models/__init__.py`, operation registries, `services/platform.py`, and repository facades as composition only; feature logic belongs in its group file.
4. Treat MCP as the `mcp` service group. Do not place MCP routes, models, handlers, or rules in workspace modules.
5. Keep HTTP translation in handlers, business rules in services, atomic files in repositories, and upstream runtime calls in integrations.
6. Preserve stable API paths and persisted product keys during internal package refactors unless a migration is explicitly requested.
7. Never log secrets, headers, prompts, bodies, tool arguments, or tool output. Add only explicit metadata fields.
8. Preserve profile isolation, snapshots, atomic writes, streaming, approvals, and graceful optional-dependency failure.
9. Run narrow Python tests first, then `make test` or `make check` when practical.

Do not edit `app/`, `dist/`, or `dist/assets/`. Do not move runtime code into a managed control-plane repository.
