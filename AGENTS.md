# Open Lumora Agent Guide

Open Lumora is an open-source Go/Fiber and React monolith. Read `TASKS.md`, then `.agents/rules/01-start-here.md` before changing code.

For roadmap work, also read `docs/implementation/README.md`, the assigned numbered specification, and every referenced versioned contract under `docs/contracts/` before editing.

## Non-negotiable rules

- Write only inside this repository. Related repositories are read-only references.
- Never introduce an ORM. Paid-server PostgreSQL access uses parameterized raw SQL through `pgx`; the target Community edition uses atomic profile/configuration files without a database.
- Agent-owned data belongs under `DATA_DIR/profiles/<agent-id>/`.
- Agent-created skills belong under `DATA_DIR/profiles/<agent-id>/skills/<skill-id>/SKILL.md`; never write them to the root/shared profile.
- Memory and skill approval changes that promise persistence must be written atomically to that agent's `config.yaml`.
- Every memory or skill mutation creates an immutable local snapshot before returning success.
- Keep the target Free limits explicit: one owner and sandbox, four active agents, four cron definitions, one parallel cron, 10 cron runs per UTC day, 200 per UTC month, and one connection per provider type. The current 100/month implementation must migrate together with the daily counter, tests, env, and API contract. Keep the full product contract in `docs/plans.md`.
- Enterprise behavior implements the public interfaces in `pkg/edition`; do not fork or weaken the open-source core.
- Keep the Go module and public HTTP endpoints at the repository root. The required commands are `go run cmd/main.go` and `npm run dev`.
- Put business capabilities in `services/<service_name>/`; `internal/v1/routes/SetupRoutes.go` is the only route assembly point.
- Name Go files in PascalCase after their primary exported function or type. `cmd/main.go` is the required Go entrypoint exception.
- Start every Go file with a short `<Summary>` block explaining why an agent should open or modify it.
- Preserve structured zerolog fields, OpenTelemetry spans/propagation, and the Hermes Runs API approval path.
- Keep `extensions/hermes_api` aligned with the read-only `sandboxes` runtime contract when Hermes changes.

## Validation

Run `make check`. For focused work, run `go test ./...` and `cd frontend && npm test && npm run build`.
