# Architecture

Open Lumora is a Python modular monolith around the upstream Hermes Agent
runtime. Uvicorn serves one FastAPI application on port `8642`. Open Lumora
imports Hermes CLI's native FastAPI app, adds its compatibility/management
routes ahead of Hermes' SPA catch-all, and leaves the original Hermes routes,
WebSocket APIs, cron scheduler, MCP, tools, sessions, skills, and config features
available from the same process.

```text
browser -> Traefik -> React UI
                   -> FastAPI :8642 -> Hermes core/CLI -> 9router :20128
                                      -> atomic profile files
                                      -> Enterprise API (optional)
```

The runtime image contains FastAPI and 9router, so there is one product runtime
container instead of a Go API plus one Hermes server per profile. Only Traefik
publishes a host port. 9router stays inside the runtime container.

## Python boundaries

All backend code is under the single `open_lumora` package:

```text
open_lumora/
  routes/          public URL assembly; the only route registration point
  handlers/        HTTP envelopes, SSE, uploads, downloads, Enterprise proxy
  models/          Pydantic request/response contracts used by Swagger
  services/        business orchestration and portable-profile behavior
  repositories/    atomic filesystem persistence owned by Open Lumora
  integrations/    Hermes CLI/core/config and 9router adapters
  app.py            dependency composition
  server.py         FastAPI/Uvicorn factory
  telemetry.py      redacted OpenTelemetry setup
```

There is deliberately no `open_lumora.data` package. Persistence and external
runtime interaction have different change reasons: repositories store Open
Lumora-owned state, while integrations translate calls to Hermes and 9router.

## Profiles and persistence

The default Hermes profile is `HERMES_ROOT_PROFILE`. Named agents live at
`DATA_DIR/profiles/<agent-id>/`, also exposed to Hermes as
`HERMES_HOME/profiles`. A named profile owns its config, `AGENTS.md`, skills,
memory, workspace, native Hermes session database, cron state, and snapshots.
`HERMES_ROOT_PROFILE/profiles.yaml` is the atomic profile registry. Each entry
contains `name` (the generated profile ID), `display_name`, `description`, and
`updated_at`; the UI never uses the generated ID as the human-facing label.

Filesystem writes use validated paths, temporary files, `fsync`, and atomic
replacement. Profile deletion is a recoverable move into the local trash tree.
Memory and skill mutations snapshot immutable content before returning success.
Portable import validates paths, symlinks, checksums, expansion ratios, file
counts, and declared profile ownership before making a profile visible.

Hermes itself uses SQLite for its native local session state and 9router uses
its own embedded local store. Open Lumora introduces no application database
and never queries PostgreSQL.

## Runs, providers, and scheduling

Chat runs invoke the original Hermes CLI with the selected profile's
`HERMES_HOME`; output is streamed as SSE and active subprocesses can be stopped.
Approval resolution calls Hermes' native approval core. Every profile is
normalized to the local 9router custom provider, while model selection remains
per profile. Skill and memory write approval gates default on and persist in
that profile's `config.yaml`; an Always allow response approves the current
write and disables only its matching gate. The local scheduler runs in-process and shares Hermes' file locks,
so it does not require a second scheduler service.

Saved agent teams support both parallel convoy runs and dependency-aware DAGs.
Ready workflow steps run in parallel within the team's concurrency policy,
downstream steps receive upstream summaries, cycles and out-of-team assignments
are rejected, and the configured orchestrator produces the final synthesis.

## Enterprise and telemetry

`ENTERPRISE_API_URL` is optional. When present, explicitly Enterprise-owned
dashboard, observability, and device operations are forwarded with auth and W3C
trace headers. When absent, local features remain unlimited and database-free.

FastAPI and outbound HTTP are OpenTelemetry-instrumented. Structured logs and
spans contain route templates, status, latency, and safe service metadata; user
content and credentials are excluded.
