# Architecture

XNOBrain is a Python modular monolith layered onto the original Hermes CLI
FastAPI application. The sibling `xnobrain-ui` image serves the React UI, and
the browser reaches the UI and API through Traefik.
The managed runtime container starts exactly one application process: FastAPI
and Hermes on port 8642. router is not installed in the workspace.

```text
Traefik -> xnobrain-ui (React)
        -> FastAPI (Hermes native routes + XNOBrain routes)
             -> services -> repositories -> profile/config files
             -> integrations -> Hermes CLI/core
             -> integrations -> centralized LLM router
```

XNOBrain route assembly is centralized in `xnobrain/routes/setup.py`.
Handlers translate HTTP and SSE, services coordinate business rules,
repositories persist atomic files, and integrations isolate upstream APIs.
Pydantic models are bound to routes and generate
`/xnobrain/api/runtime/swagger_docs` and `/xnobrain/api/runtime/openapi.json`.

Hermes remains authoritative for its native sessions, cron, MCP, config,
skills, tools, provider, webhook, and gateway APIs. XNOBrain adds stable UI
compatibility APIs for agent lifecycle, per-profile files, snapshots, teams,
portable bundles, and streaming runs.

## Profiles and persistence

The default profile is `HERMES_ROOT_PROFILE`. Named profiles live at
`DATA_DIR/profiles/<agent-id>` and are discovered through the Hermes CLI
profile inventory. There is no application database. Hermes may use its own
profile-local `state.db` for native session history; that is an upstream
profile file, not an XNOBrain database or schema.

Every named profile owns config, prompts, skills, memory, workspace, session,
cron, log, MCP, and snapshot data. Portable bundles include every regular file
in the profile directory while redacting secret values. Imported credential
files are discarded, approvals reset to manual, and cron jobs are paused.

New named profiles are seeded from the installer-managed
`HERMES_ROOT_PROFILE/profile-template`, whose default model is `auto`. They do
not copy the mutable default profile's persona, memory, plugins, or workspace.
Enabled global skills are inherited separately. Provider credentials are never
copied into a profile or workspace.

## Local runtime boundary

Chat selects a profile and invokes the original Hermes CLI/core from the one
FastAPI process. Streaming emits structured `run.started`, `message.delta`,
terminal run events, and supports process interruption. Hermes' approval core
remains the resolver for pending approvals.

Managed LLM inference calls the centralized router directly with a Control-issued
API key. Runtime does not host the router, retain provider credentials, or
read router usage storage. Profile files, tools, memory, and other local agent behavior
remain local. The optional
OpenTelemetry collector is local-only and disabled by default. When
`OTEL_ENABLED=true`, the runtime exports metadata-only spans only to the
Compose collector or a loopback endpoint.
