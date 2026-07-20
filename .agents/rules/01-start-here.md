# Start Here

## Layout

- `cmd/main.go`: root process entrypoint; run with `go run cmd/main.go`.
- `internal/api`: Fiber handlers and response boundary.
- `internal/v1/routes/SetupRoutes.go`: the one route assembly point.
- `services/<service_name>`: orchestration and business limits by capability.
- `internal/repositories`: interfaces plus raw PostgreSQL queries.
- `internal/profile`: safe profile filesystem, permissions, and snapshots.
- `internal/runtime`: CLI and Hermes Runs API adapters.
- `pkg/edition`: public open-source/enterprise policy contracts.
- `extensions/hermes_api`: packaged Python adapter loaded by each profile API server.
- `src`: Vite, React, and TypeScript application.
- `bin/images`: checksummed split OCI bundles; never store an unsplit image tar.
- `docs`: human-authored architecture and API documentation.
- `docs/plans.md`: authoritative plan features, recommended limits, and enforcement semantics.
- `docs/implementation/README.md`: prioritized cross-repository implementation roadmap and parallel work ownership.
- `docs/contracts`: public, versioned protocols shared with the enterprise control plane.

## Go style

- Start every Go file with the repository's `<Summary>` comment block.
- Use PascalCase filenames matching the primary exported function or type; only `cmd/main.go` stays lowercase for the required entrypoint.
- Prefer focused files, early returns, request contexts, and bounded timeouts.
- Handlers parse and respond; services own rules; repositories own persistence.
- Use zerolog structured fields and OpenTelemetry context propagation.
- Route setup belongs only in `internal/v1/routes/SetupRoutes.go`; routes call services directly, never another local HTTP service.
- Do not use `SELECT *`; list columns and use `$1`-style placeholders.

## Safety and ownership

- Resolve and validate every user-controlled path beneath its profile root.
- Use temp-file-plus-rename for config and content writes.
- Never return or log credentials, runtime tokens, or provider keys.
- Open-source requests use an unrestricted local principal. Enterprise
  authentication applies only to Enterprise API features and managed cloud
  resources.
- Loss of the Enterprise API must not restrict agents, skills, MCP, providers,
  profiles, teams, or local cron in a self-hosted deployment.
