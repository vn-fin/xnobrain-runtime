# Open Lumora project summary

## Executive summary

Open Lumora is a self-hosted workspace for creating and operating Hermes AI
agents. It provides a React web interface, a Go API, isolated agent profiles,
conversations, memory, skills, teams, scheduled jobs, provider connections,
portable snapshots, usage limits, telemetry, and an operational dashboard.

The product is deliberately split into a public Studio repository and a
private Enterprise repository. The public build contains only the frontend and
backend. Enterprise owns the runtime, gateway, databases, authentication,
scheduling workers, and cloud/commercial capabilities.

The public application remains usable in local no-login mode with compiled
Free limits and file-backed data. When the gateway is available, Studio uses it
for plan limits, private runtime access, providers, telemetry dashboards, and
enterprise services.

## Repository ownership

| Area | Public `open-lumora` | Enterprise `open-lumora-enterprise` |
| --- | --- | --- |
| Web UI | React/Vite frontend image | — |
| Public API | Go/Fiber Studio backend image | — |
| Agent profiles | File-backed profiles and snapshots | Runtime access to the shared profile volume |
| Runtime | Public extension source contracts only | Hermes and 9router images; Docker and Incus builds |
| Gateway | Client/proxy contracts and Free fallback | `open-lumora-gateway`, limits, auth, runtime proxy |
| Persistence | Local files; no ORM | PostgreSQL with raw parameterized SQL; ClickHouse telemetry |
| Scheduling | CRUD and local policy contracts | Schedule listener, execution workers, missed-run delivery |
| Commercial plans | Public interfaces and Free defaults | Plans, tenants, billing, RBAC, SSO, quotas, audit |

The public repository never embeds an enterprise executable or runtime image.
Its `extensions/` source is consumed by the Enterprise runtime build.

## Runtime architecture

```text
Browser
  |
  v
Traefik :80
  |-- frontend container (static React UI)
  |-- Studio backend :3000 (public API)
          |
          | private open-lumora-control network
          v
  open-lumora-gateway :3100
      |-- rate limits, plan and trust policy
      |-- authentication for cloud deployments
      |-- private Hermes Runs API proxy
      |-- private 9router/provider proxy
      |-- ClickHouse aggregate dashboard queries
      v
  Hermes runtime + 9router (Enterprise-owned)
```

The two Compose projects share the external Docker network
`open-lumora-control`. The gateway uses the DNS alias
`open-lumora-gateway`. Studio and the runtime share the established Docker
volume `open-lumora_open_lumora_data`, mounted at `/opt/data`, so profile paths
remain identical across containers. The browser never receives a runtime,
gateway, PostgreSQL, or ClickHouse address.

## Public Studio features

### Agent workspace

- Create, list, configure, test, and remove agents.
- Per-agent model, provider, reasoning, approval, system prompt, and skill settings.
- Team definitions with bounded members and delegated workers.
- Workspace file browsing and safe file mutations.
- Conversations with streaming responses through the Hermes Runs API.
- Stop, approval, and resume events over server-sent events.

### Memory and skills

Each agent owns an isolated profile:

```text
profiles/<agent-id>/
  config.yaml
  AGENTS.md
  skills/<skill-id>/SKILL.md
  memories/MEMORY.md
  memories/USER.md
  workspace/
  sessions/
  cron/
  logs/
  snapshots/
```

Memory and skill writes are restricted to the selected profile. Skill creation
always uses `skills/<skill-id>/SKILL.md`; legacy categorized skill directories
are migrated automatically. Mutations use atomic writes and create immutable,
content-addressed snapshots. Snapshot restore returns the selected version to
the live profile.

Approval choices are intentionally limited to **Allow once**, **Always allow**,
and **Deny**. “Always allow” is persisted to the selected profile's
`config.yaml`, not only to the browser session, and survives process restarts.

### Providers and runtime

Studio supports provider connection and model APIs for Claude, Codex,
Antigravity, OpenAI, Anthropic, and Gemini. Provider credentials are handled by
9router/Hermes and are not stored in Studio. Agent runs use the remote gateway
path in container deployments and the same public runtime contract in host
development.

### Cron and teams

- Create, update, pause, resume, run, and remove cron definitions.
- Daily and monthly usage counters are reserved atomically.
- Parallel cron capacity is enforced before execution.
- Missed work is retained and surfaced as notifications when a device returns.
- Team and delegation limits are enforced for both HTTP requests and scheduler
  callers.

The Enterprise schedule listener owns due-job execution in the split deployment;
Studio remains the public CRUD and policy boundary.

### Portability

Portable `.lumora` bundles can contain versioned profiles, memory, skills,
workspaces, snapshots, conversations, cron definitions, teams, and checksums.
They exclude provider credentials, runtime tokens, PIDs, sockets, absolute
machine paths, and endpoint addresses. Imports are validated before extraction
and can remap ownership and conflicting identifiers.

## Plans and limits

Every plan can be self-hosted or cloud-hosted. Deployment mode describes who
operates infrastructure; the plan describes entitlements.

| Limit | Free default | Pro recommendation | Enterprise |
| --- | ---: | ---: | --- |
| Active agents | 4 | 20 | Contract-configurable |
| Agent teams | 1 | 10 | Contract-configurable |
| Agents per team | 2 | 10 | Contract-configurable |
| Delegated workers | 1 | 5 | Contract-configurable |
| Delegation depth | 1 | 2 | Contract-configurable safety ceiling |
| Cron definitions | 4 | 50 | Contract-configurable |
| Parallel cron runs | 1 | 5 | Contract-configurable |
| Cron runs per UTC day | 10 | 250 | Contract-configurable |
| Cron runs per UTC month | 200 | 5,000 | Contract-configurable |
| Connections per provider type | 1 | 5 | Contract-configurable |

Exhausted quotas return HTTP `429` with rate-limit headers. A plan-excluded
feature returns `403 plan_required`; paid authentication failures return `401`.
Middleware is only the HTTP presentation layer: services and scheduler paths
repeat the checks, while Enterprise performs distributed reservations in
PostgreSQL. `-1` represents unlimited; `0` means unavailable.

Free self-hosted mode uses the host's available hardware. Free cloud uses a
default hardware class; Pro cloud targets a two-times CPU/RAM class; Enterprise
can select contract-defined CPU, RAM, storage, accelerators, and placement.

## Storage and data responsibilities

### Public local mode

- No login is required.
- No PostgreSQL dependency.
- Agent/profile state is stored in files under `DATA_DIR`.
- Configuration and content writes use temporary-file-plus-rename semantics.
- Profile access validates traversal and symlink boundaries.
- Local Free limits remain available if the gateway is offline.

### Enterprise and cloud mode

Enterprise uses PostgreSQL for tenants, users, plans, quotas, sandboxes,
imports, audit metadata, and distributed reservations. ClickHouse stores
telemetry, traces, logs, usage events, and dependency data. The quota ledger is
authoritative for billing; telemetry is operational data and must never block a
runtime command.

## Telemetry and dashboard

The dashboard never receives raw telemetry. Enterprise aggregates data in
ClickHouse before returning compact responses to Studio:

- Run success, failure, duration, and p95 latency.
- Agents, teams, skills, tools, models, providers, and token usage.
- Input, output, cached tokens, estimated cost, and provider errors.
- CPU, memory, queue depth, and runtime health.
- Agent/team/skill/tool dependency graph.
- Cron execution and missed-run status.

Telemetry deliberately excludes prompts, model responses, tool arguments,
credentials, memory content, skill content, private keys, and raw local IDs.
Identifiers are hashed or tenant-scoped. Retention is configurable; the local
review default is seven days and synthetic smoke data can be disabled with the
enterprise dashboard setting before production.

## Security and reliability

- Traefik is the only public ingress in container deployment.
- Runtime, gateway, PostgreSQL, and ClickHouse have no browser-facing route.
- Profile paths are validated beneath the selected agent root.
- Provider secrets and runtime tokens are not logged or sent to the frontend.
- OpenTelemetry tracing propagates across Studio, gateway, scheduler, and runtime.
- Rate-limit and telemetry failures have safe fallback behavior.
- Immutable snapshots provide recovery for memory and skill mutations.
- No ORM is used; Enterprise database access uses parameterized raw SQL.

## Build and deployment

Public repository:

```bash
make check
make build       # backend + frontend images and <50 MB split bundle
make install     # load public bundle, create private network, start public stack
make smoke-api   # exercise public APIs through Traefik
```

Enterprise repository:

```bash
make build
python build_docker.py --path open-lumora-hermes-runtime:<tag>
python build_vm.py
```

The Enterprise gateway environment must provide `AUTH_SERVICE_BASE_URL` and
`AUTH_TIMEOUT`. Local no-login mode may leave the auth base URL empty; cloud
mode must validate it at startup. Transport-specific details such as gRPC,
TLS, and auth credentials remain private to Enterprise.

## Current status

Implemented and verified in the public repository:

- Frontend/backend ownership split.
- Profile-scoped memory and skill writes.
- Persistent approval decisions.
- Categorized-skill migration and snapshot recovery.
- Agent, team, cron, provider, portability, and limits APIs.
- Telemetry/dashboard contracts and dependency graph UI.
- Public Docker images and split offline bundle.
- Traefik routing and gateway network contract.
- Full tests, frontend build, Docker build, and public API smoke checks.

Remaining Enterprise handoff work is to rename the private binary/service to
`open-lumora-gateway`, attach the Enterprise Compose stack to
`open-lumora-control`, mount `open-lumora_open_lumora_data`, add
`AUTH_SERVICE_BASE_URL`, and package the runtime, PostgreSQL, ClickHouse, and
scheduler listener under Enterprise ownership.
