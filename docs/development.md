# Development and verification

## Requirements

- Python 3.12 or newer
- Node.js 22 or newer and npm
- Hermes Agent for native/profile integration tests
- Docker Engine with Compose for the release path

Copy `.env.example` to `.env` when overrides are needed. Start the backend from
the repository root with `python server.py` or `make backend`; it listens on
`0.0.0.0:8642`. Start Vite with `make src`. `make dev` manages both processes.

The Community backend has no Go toolchain or PostgreSQL requirement.

## Checks

```bash
make check
make run
make smoke-api
```

`make check` runs Python unit/contract tests, compiles the full `open_lumora`
package, runs frontend tests, type-checks TypeScript, and builds the production
browser bundle. `make smoke-api` sends safe public/status requests through
Traefik and skips Enterprise-only dashboards when `ENTERPRISE_API_URL` is not
configured.

For profile mutations, verify that skills exist only at
`DATA_DIR/profiles/<id>/skills/<skill>/SKILL.md`, memory and config writes have
snapshots, traversal/symlink escapes fail, and delete moves the profile to the
recoverable trash tree. Chat requires at least one valid 9router provider;
provider failures must emit `run.failed`, not a false completed event.

## Latest evidence

On 2026-07-21, `make check` passed 19 Python tests and 31 frontend tests plus
the TypeScript/Vite production build. Both application images built. The live
Compose stack reached healthy state behind Traefik, Swagger exposed the unified
Hermes/Open Lumora schema, the safe API smoke suite passed, and a bounded live
mutation suite covered agents, config, skills, memory, MCP, workspace,
snapshots, cron, teams, and portable bundle export/inspect/dry-run/apply.

The installed local Hermes profile and the container path both completed a real
`gpt-5.5` request. The container request traversed FastAPI, the Hermes CLI, and
the bundled 9router process.
