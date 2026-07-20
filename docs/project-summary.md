# Open Lumora project summary

## Executive summary

Open Lumora is a self-hosted and managed workspace for Hermes AI agents. The
public product includes a React interface, Go Studio API, isolated profiles,
memory, skills, MCP, provider connections, teams, conversations, local cron,
snapshots, and the extended Hermes/9router runtime.

Self-hosted users have full local Hermes access with or without login. Login is
needed only for Enterprise API extensions such as usage, trace, dependency, and
runtime-resource dashboards. Cloud always requires login and applies plan
limits to managed Incus resources.

## Repository boundary

| Area | `open-lumora` | `open-lumora-enterprise` |
|---|---|---|
| Frontend and Studio API | Owns and builds | — |
| Hermes extensions and runtime | Owns Docker and Incus builds | Consumes released OSS artifacts |
| Local agent data | Profiles, skills, memory, chat, cron | Never duplicates it |
| Authentication and plans | Forwards credentials in cloud mode | Auth service, tenants, Basic/Pro/Pro Max/Enterprise |
| Observability | Instruments and sends after login | Authenticates, sanitizes, retains, aggregates |
| Databases | File-backed Hermes state; PostgreSQL container for pulled Enterprise API | Raw-SQL PostgreSQL and ClickHouse schemas |

No ORM is used.

## Self-hosted architecture

```text
Browser -> Traefik -> frontend
                   -> Studio API -> OSS Hermes/9router runtime
                                 -> pulled Enterprise API (extensions only)

Studio/runtime -> OTel Collector -> Enterprise API -> bounded Go channel
                                      -> workers -> ClickHouse
```

Only Traefik exposes a host port. Studio talks directly to Hermes on the
private control network. The Enterprise API cannot restrict self-hosted agents,
profiles, skills, MCP, providers, teams, or local cron. If it is unavailable,
local functionality continues unchanged.

The default signed-out mode does not start the optional Collector and exposes
no dashboard data. The `authenticated` Compose profile enables the Collector
after account and tenant-bound telemetry credentials are configured.

## Cloud architecture

Cloud uses the same OSS runtime contract packaged as an Incus image. Login is
mandatory. Enterprise PostgreSQL stores users, tenants, subscriptions, managed
resource metadata, and quota reservations. ClickHouse stores sanitized traces,
body-free operational log metadata, and runtime metrics. Future Enterprise work
adds RBAC, shared workspaces, organization policies, and SSO.

## Plans

| Plan | Stable ID | Telemetry retention | Managed agents | Managed MCP | Managed providers/type |
|---|---|---:|---:|---:|---:|
| Basic | `free` | 7 days | 4 | 5 | 1 |
| Pro | `pro` | 90 days | 20 | 25 | 5 |
| Pro Max | `promax` | 180 days | 100 | 125 | 25 |
| Enterprise (Business) | `enterprise` | 365 days | Custom/unlimited | Custom/unlimited | Custom/unlimited |

Pro Max uses five times Pro's quantitative managed allowances, with separate
safety ceilings for delegation depth and retention. New authenticated testing
tenants default to Basic until subscription billing is integrated.

## Telemetry privacy and APIs

Telemetry stores trace topology, safe identities, token and response-character
counts, cost, latency, errors, runtime CPU/memory/disk/network metrics, and
body-free operational log correlation. It does not store prompts, responses,
chat logs, memories, skill contents, tool arguments, credentials, or profile
files. Opaque agent/conversation/session references open the authoritative
Hermes chat when needed.

Enterprise endpoints include authenticated OTLP `/v1/traces`, `/v1/metrics`,
and `/v1/logs`, plus aggregate dashboard, trace/dependency exploration, and a
filtered metrics API. A bounded Go channel and fixed retrying workers write to
ClickHouse without Kafka or an ORM.

## Build and deployment

Public repository:

```bash
make build
python build_docker.py --path open-lumora-hermes-runtime:local
python build_vm.py
make install
```

Enterprise repository:

```bash
make build
```

The public offline OCI bundle contains frontend, backend, and runtime images in
checksum-protected parts smaller than 50 MB. Enterprise builds only its API
image.
