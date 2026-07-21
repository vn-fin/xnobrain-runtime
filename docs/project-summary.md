# Open Lumora project summary

Open Lumora is a self-hosted and managed workspace for Hermes agents. The
Community product consists of a React UI and one runtime/backend image that
contains FastAPI, the original Hermes CLI/core, and 9router. Each agent uses an
isolated atomic file-backed profile. No Go API or PostgreSQL service is present.

## Runtime flow

```text
Browser -> Traefik -> React
                   -> FastAPI :8642 -> Hermes CLI/core -> 9router :20128
                                      -> default or named profile
```

One FastAPI process serves Open Lumora management routes and all native Hermes
CLI routes. Profiles are data, not servers. This removes per-profile service
lifecycle and keeps upstream Hermes feature updates available through one
runtime image.

## Feature boundary

Community provides unlimited local agents, profiles, prompts/config, skills,
memory, MCP, conversations and SSE runs, approvals, cron, providers, teams,
workspace files, immutable snapshots, and portable bundles. The optional
Enterprise API owns authentication, billing/plans, managed resources, retained
observability, and Incus deployment packaging.

`ENTERPRISE_API_URL` is the only application integration required for optional
Enterprise behavior. Local capabilities continue when it is unset or offline.

## Privacy and persistence

Open Lumora-owned state is stored under `DATA_DIR`; Hermes and 9router retain
their native embedded local state. Portable bundles and telemetry exclude
credentials, prompts, responses, memories, skills, tool arguments, and logs.
There is no ORM and no Community application database.

## Build

```bash
make check
make image
make install
```

The two application images are `open-lumora-frontend` and
`open-lumora-hermes-runtime`. Open <http://localhost> and use
<http://localhost/docs> for the unified OpenAPI documentation.
