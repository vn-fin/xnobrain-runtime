---
name: brain4all-backend
description: Implement, diagnose, or review Brain4All Python backend and runtime behavior under brain4all/, extensions/, runtime/, and server.py. Use for FastAPI routes, handlers, services, repositories, Hermes adapters, provider integrations, structured logging, telemetry, persistence, or backend tests.
---

# Brain4All Backend

Read `AGENTS.md`, `.agents/rules/01-start-here.md`, and `.agents/rules/02-source-boundaries.md` first.

1. Trace the route declaration, handler operation, service rule, integration, and tests before editing.
2. Declare public endpoints only through `brain4all/routes/setup.py` and the versioned route helpers.
3. Keep HTTP translation in handlers, rules in services, atomic files in repositories, and upstream runtime calls in integrations.
4. Never log secrets, headers, prompts, bodies, tool arguments, or tool output. Add only explicit metadata fields.
5. Preserve profile isolation, snapshots, atomic writes, streaming, approvals, and graceful optional-dependency failure.
6. Run the narrow Python tests first, then `make test` or `make check` when practical.

Do not edit `app/`, `dist/`, or `dist/assets/`. Do not move runtime code into a managed control-plane repository.
