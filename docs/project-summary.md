# XNOBrain project summary

XNOBrain is a self-hosted workspace for AI agents. It consists of a separate
React UI and one runtime/backend image that contains FastAPI, the agent engine,
and provider runtime. Each agent uses an
isolated atomic file-backed profile. No Go API or PostgreSQL service is present.

## Runtime flow

```text
Browser -> Traefik -> React
                   -> FastAPI :8642 -> Hermes CLI/core -> 9router :20128
                                      -> default or named profile
```

One FastAPI process serves XNOBrain management routes and all native Hermes
CLI routes. Profiles are data, not servers. This removes per-profile service
lifecycle and keeps upstream Hermes feature updates available through one
runtime image.

## Feature boundary

XNOBrain provides unlimited local agents, profiles, prompts/config, skills,
memory, MCP, conversations and SSE runs, approvals, cron, providers, teams,
workspace files, immutable snapshots, and portable bundles. It has no managed
control plane or Enterprise API integration.

OpenTelemetry is optional and local-only. The Compose collector profile is off
by default and writes metadata-only span summaries to its container logs.

## Privacy and persistence

XNOBrain-owned state is stored under `DATA_DIR`; Hermes and 9router retain
their native embedded local state. Portable bundles include complete regular
profile-file trees with secret values redacted. Telemetry excludes credentials,
prompts, responses, memories, skills, tool arguments, and logs. There is no ORM
and no Community application database.

## Build

```bash
make check
make run
```

The UI image is built by `xnobrain-ui`; this repository builds
`xnobrain-runtime`. Open <http://localhost:5152> and use
<http://localhost:5152/xnobrain/api/runtime/swagger_docs> for the unified OpenAPI
documentation.
