# Architecture

Brain4All is a Python modular monolith layered onto the original Hermes CLI
FastAPI application. The browser reaches the React UI and API through Traefik.
The runtime container starts exactly two processes: FastAPI on 8642 and 9router
on 20128.

```text
Traefik -> React
        -> FastAPI (Hermes native routes + Brain4All routes)
             -> services -> repositories -> profile/config files
             -> integrations -> Hermes CLI/core
             -> integrations -> 9router
             -> optional Enterprise API
```

Brain4All route assembly is centralized in `brain4all/routes/setup.py`.
Handlers translate HTTP and SSE, services coordinate business rules,
repositories persist atomic files, and integrations isolate upstream APIs.
Pydantic models are bound to routes and generate `/docs` and `/openapi.json`.

Hermes remains authoritative for its native sessions, cron, MCP, config,
skills, tools, provider, webhook, and gateway APIs. Brain4All adds stable UI
compatibility APIs for agent lifecycle, per-profile files, snapshots, teams,
portable bundles, streaming runs, and optional Enterprise features.

## Profiles and persistence

The default profile is `HERMES_ROOT_PROFILE`. Named profiles live at
`DATA_DIR/profiles/<agent-id>` and are discovered through the Hermes CLI
profile inventory. There is no application database. Hermes may use its own
profile-local `state.db` for native session history; that is an upstream
profile file, not an Brain4All database or schema.

Every named profile owns config, prompts, skills, memory, workspace, session,
cron, log, MCP, and snapshot data. Portable bundles exclude `.env`,
credentials, logs, caches, and provider secrets; imported approvals reset to
manual and imported cron jobs are paused.

## Runtime and Enterprise boundary

Chat selects a profile and invokes the original Hermes CLI/core from the one
FastAPI process. Streaming emits structured `run.started`, `message.delta`,
terminal run events, and supports process interruption. Hermes' approval core
remains the resolver for pending approvals.

`ENTERPRISE_API_URL` is the only deployment-time Enterprise application
dependency. It enables dashboard, observability, and device functionality.
When absent or unavailable, all local OSS capabilities remain unrestricted.
Telemetry exports metadata-only spans when an OTLP endpoint is configured.
