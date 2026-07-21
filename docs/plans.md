# Product editions and deployment modes

This is the product contract for Open Lumora. Deployment mode and subscription
plan are separate decisions: self-hosting never limits local Hermes features,
while authenticated Enterprise API features follow the account plan.

## Runtime ownership

`open-lumora` owns the frontend and combined FastAPI/Hermes/9router Docker
runtime. It supports agents, profiles, skills, memory, MCP, providers, teams,
conversations, and local cron scheduling without an application database.

`open-lumora-enterprise` owns the authenticated Enterprise API, PostgreSQL plan
metadata, ClickHouse telemetry storage, trace ingestion, aggregate metric APIs,
future collaboration features, and managed/Incus cloud packaging. It consumes
the released OSS runtime interface rather than forking the `open_lumora`
application package.

## Deployment behavior

| Deployment | Login | Local Hermes access | Enterprise API features | Enforcement |
|---|---|---|---|---|
| Self-hosted, signed out | Optional | Unlimited | None | No local agent, skill, MCP, provider, team, or cron quotas |
| Self-hosted, signed in | Required only for extensions | Unlimited | Usage, traces, metrics, and plan features | Plan applies only to Enterprise API features |
| Cloud | Required | Managed Incus runtime | Usage, traces, metrics, and future collaboration | Plan applies to managed resources and Enterprise features |

The default Compose mode is self-hosted and signed out. It continues to work
without Internet or an authentication service. The Enterprise API is external
to this stack; authenticated routes remain unavailable until
`ENTERPRISE_API_URL` is configured and a user signs in.

## Plans

The stable plan IDs are `free`, `pro`, `promax`, and `enterprise`. The `free`
ID is displayed as **Basic**. Newly authenticated testing accounts default to
`free` until subscription integration is implemented.

| Capability | Basic (`free`) | Pro | Pro Max | Enterprise (Business) |
|---|---:|---:|---:|---:|
| Self-hosted local Hermes features | Unlimited | Unlimited | Unlimited | Unlimited |
| Managed telemetry | ✅ | ✅ | ✅ | ✅ |
| Trace and dependency explorer | ✅ | ✅ | ✅ | ✅ |
| Usage and runtime dashboards | ✅ | ✅ | ✅ | ✅ |
| Telemetry retention | 7 days | 90 days | 180 days | 365 days |
| Managed agents | 4 | 20 | 100 | Custom/unlimited |
| Managed sandboxes | 1 | 1 | 5 | Custom/unlimited |
| Managed teams | 1 | 10 | 50 | Custom/unlimited |
| Agents per managed team | 2 | 10 | 50 | Custom/unlimited |
| Managed MCP servers | 5 | 25 | 125 | Custom/unlimited |
| Provider connections/type | 1 | 5 | 25 | Custom/unlimited |
| Managed cron definitions | 4 | 50 | 250 | Custom/unlimited |
| Parallel managed cron runs | 1 | 5 | 25 | Custom/unlimited |
| Managed cron runs/day | 10 | 250 | 1,250 | Custom/unlimited |
| Managed cron runs/month | 200 | 5,000 | 25,000 | Custom/unlimited |
| Managed telemetry events/day | 100,000 | 1,000,000 | 5,000,000 | Custom/unlimited |
| RBAC/shared workspaces | ❌ | ❌ | Planned | Planned/contract |
| SSO and audit policy | ❌ | ❌ | ❌ | Planned/contract |

Pro Max quantitative allowances are five times Pro except for safety-sensitive
depth and retention settings. Numeric `-1` means unlimited; zero means a feature
is unavailable.

## Telemetry boundary

Telemetry is available only to an authenticated account or claimed device:

```text
FastAPI/runtime -> OTel Collector -> Enterprise API -> ingest channel
               -> fixed workers -> ClickHouse -> aggregate/filter APIs -> UI
```

The system stores spans, body-free operational log metadata, safe span events, token/character counts, costs,
latency, errors, dependency identifiers, and runtime CPU/memory/disk/network
metrics. It does not duplicate Hermes chat logs, prompts, responses, memories,
skill contents, tool arguments, credentials, or profile files. Conversation,
session, and agent references are opaque links back to Hermes-owned history.

## Enforcement rules

- Self-hosted OSS services always resolve unlimited local limits, even when the
  Enterprise API is offline or the signed-in plan is Basic.
- Cloud services enforce count and usage limits atomically in PostgreSQL.
- Enterprise telemetry ingestion authenticates before assigning a tenant and
  derives retention from that tenant's plan.
- Quota exhaustion returns `429`; unavailable paid features return `403`;
  missing login returns `401`.
- Telemetry is never a billing source of truth.
- No service may use an ORM.
