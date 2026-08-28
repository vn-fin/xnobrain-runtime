---
name: xnobrain-local-dev
description: Run and validate the XNOBrain local Docker Compose development workflow for the Python/FastAPI Hermes runtime and Incus/runtime image packaging repository, including live reload, rebuilds, health checks, and focused tests.
---

# XNOBrain local development

Use this skill when changing `xnobrain-runtime` and the change needs a repeatable local
Docker/Compose validation loop. Read the repository `AGENTS.md`, its applicable
`.agents/rules/`, and the root `AGENTS.md` first. Work from the workspace root
`/home/kim/Documents/xno/xnobrain` so the coordinated stack uses the checked-out sibling repositories.

## Workflow

1. Inspect `git status` in the workspace and in `xnobrain-runtime`; preserve unrelated user edits.
2. Run the smallest focused test for the change before or alongside the container check.
3. Start the live coordinated stack with `make dev`. It builds the local runtime image,
   prepares the managed Incus test image, and runs the development Compose profile.
4. Keep the stack running while editing. When dependencies, Dockerfiles, Compose files,
   or built assets change, run `make dev-build` (or the narrow repository build) and
   restart only the affected service. Do not rebuild on every source edit when the
   existing bind mount/live reload handles it.
5. Check `make config`, `make local-status`, service logs, and the documented health/smoke
   endpoint. Use `make local-smoke` when the behavior crosses gateway, control, UI,
   router, AI, or Incus boundaries.
6. Stop with `make stop`; use `make remove` only when intentionally discarding local
   volumes. Run the repository checks and `git diff --check` before handoff.

The runtime image is prepared by the root `make local-runtime`/`make local-prepare` flow. Do not add router, Redis, vector databases, or a second runtime process to the runtime Compose file.

## Safety and boundaries

- Never use production `.env` values as a substitute for local configuration, and never
  print or commit `.env*`, tokens, cookies, prompts, or provider credentials.
- Root Compose exposes only Traefik on port 5173; service APIs remain private.
- Do not silently change image names, environment prefixes, ports, profiles, or the
  runtime/control/router/AI ownership boundaries to make local testing pass.
- If Docker or Incus is unavailable, run static Compose validation and focused tests and
  report the exact skipped integration checks; do not claim deployment success.
