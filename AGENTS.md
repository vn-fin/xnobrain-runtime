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
- Self-hosted OSS access is unlimited for agents, profiles, skills, memory, MCP,
  providers, teams, and local cron. Subscription limits apply only to managed
  cloud resources and authenticated Enterprise API features.
- Enterprise behavior implements the public interfaces in `pkg/edition`; do not fork or weaken the open-source core.
- Keep the Go module and public HTTP endpoints at the repository root. The required commands are `go run cmd/main.go` and `npm run dev`.
- Put business capabilities in `services/<service_name>/`; `internal/v1/routes/SetupRoutes.go` is the only route assembly point.
- Name Go files in PascalCase after their primary exported function or type. `cmd/main.go` is the required Go entrypoint exception.
- Start every Go file with a short `<Summary>` block explaining why an agent should open or modify it.
- Preserve structured zerolog fields, OpenTelemetry spans/propagation, and the Hermes Runs API approval path.
- Hermes API extensions and Docker/Incus runtime packaging belong exclusively
  to this public repository. Enterprise consumes the released runtime artifact
  and must not carry a private copy.
- Docker starts in `START_MODE=local`: Traefik exposes only the public frontend
  and Studio backend. Studio calls its OSS Hermes runtime directly. The pulled
  Enterprise API image is optional functionality and cannot restrict local use.
- `make build` builds the Studio backend, frontend, and Hermes runtime images. Their
  OCI transfer bundle belongs under `bin/images`; every part stays below 50 MB.

## Validation

Run `make check`. For focused work, run `go test ./...` and `cd frontend && npm test && npm run build`.
