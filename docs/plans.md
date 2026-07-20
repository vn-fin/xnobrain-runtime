# Plans, features, and limits

This document is the product contract for Community Free, Personal Pro, and Enterprise. Every plan can be self-hosted or cloud-hosted. A plan describes features and entitlements; deployment mode describes who operates the infrastructure.

Implementation order, repository ownership, parallel work packages, and acceptance gates are defined in [the implementation roadmap](implementation/README.md).

The values below are recommended launch defaults. Paid and cloud values must be stored in the enterprise plan service rather than compiled into the shared runtime. Enterprise values are contract-configurable.

## Feature comparison

| Capability | Free | Pro | Enterprise |
|---|---|---|---|
| Self-hosted deployment | ✅ | ✅ | ✅ |
| XNO cloud deployment | ✅ | ✅ | ✅ |
| No-login self-hosted mode | ✅ | ❌ | ❌ |
| Account login for cloud | ✅ | ✅ | ✅ |
| Multiple organization members | ❌ | ❌ | ✅ |
| RBAC and SSO | ❌ | ❌ | ✅ |
| Agent profiles, memory, skills, and workspaces | ✅ | ✅ | ✅ |
| Saved agent teams | ✅ **[1 team, 2 agents]** | ✅ **[10 teams, 10 agents/team]** | ✅ **[custom]** |
| Concurrent delegated workers | ✅ **[1, flat]** | ✅ **[5, depth 2]** | ✅ **[custom safety policy]** |
| Conversations and streaming | ✅ | ✅ | ✅ |
| Memory and skill snapshots | ✅ | ✅ | ✅ |
| Cron scheduling | ✅ **[1 concurrent]** | ✅ **[5 concurrent]** | ✅ **[custom]** |
| Scheduled cron runs per UTC day | ✅ **[10]** | ✅ **[250]** | ✅ **[custom]** |
| Scheduled cron runs per UTC month | ✅ **[200]** | ✅ **[5,000]** | ✅ **[custom]** |
| Multiple accounts for the same provider | ❌ **[1]** | ✅ **[5]** | ✅ **[custom]** |
| Portable profile export | ✅ | ✅ | ✅ |
| Import a local `.lumora` bundle | ✅ **[cloud]** | ✅ | ✅ **[policy controlled]** |
| Provider credential migration in bundles | ❌ | ❌ | ❌ **[unless separately approved]** |
| Managed backups | ✅ **[cloud only]** | ✅ | ✅ **[custom policy]** |
| Standard hardware class | ✅ **[1× default]** | ✅ **[2× default]** | ✅ |
| Custom CPU, RAM, storage, or accelerators | ❌ | ❌ | ✅ |
| Managed operational telemetry | ✅ **[cloud basic]** | ✅ | ✅ **[custom]** |
| Collaboration and shared ownership | ❌ | ❌ | ✅ |
| Immutable organization audit | ❌ | ❌ | ✅ |
| High availability | ❌ | ✅ **[cloud]** | ✅ |
| Support | Community | Standard | Contract and SLA |

`✅` means the feature is available, `❌` means it is unavailable, and a value in brackets is the plan limit or condition.

## Deployment comparison

| Deployment | Free | Pro | Enterprise |
|---|---|---|---|
| Self-hosted storage | Profile/configuration files; no database in the target Free architecture | PostgreSQL plus persistent profile volumes | PostgreSQL plus persistent profile or approved object storage |
| Cloud storage | PostgreSQL control plane plus one persistent profile volume | PostgreSQL control plane plus one persistent profile volume | Tenant-aware PostgreSQL plus contract storage |
| Authentication | None when self-hosted; account required for cloud ownership | Account required | Account required, with RBAC and optional SSO |
| Runtime | One local or cloud sandbox | One local or cloud sandbox | Contract-configurable sandbox pools |
| Offline behavior | Self-hosted continues offline | Self-hosted continues according to cached license policy | Defined by the on-premise license and identity deployment |

## Recommended quantitative limits

| Limited resource | Free | Pro | Enterprise |
|---|---:|---:|---:|
| Members | 1 | 1 | Contract-configurable |
| Workspaces or tenants | 1 | 1 | Contract-configurable |
| Active sandboxes | 1 | 1 | Contract-configurable |
| Active agents | 4 | 20 | Contract-configurable or unlimited |
| Saved agent teams | 1 | 10 | Contract-configurable or unlimited |
| Agents per team | 2 | 10 | Contract-configurable or unlimited |
| Concurrent delegated workers | 1 | 5 | Contract-configurable or unlimited |
| Delegation depth | 1 (flat) | 2 | Contract-configurable with a safety ceiling |
| Cron definitions | 4 | 50 | Contract-configurable or unlimited |
| Parallel cron runs | 1 | 5 | Contract-configurable or unlimited |
| Cron runs per UTC day | 10 | 250 | Contract-configurable or unlimited |
| Cron runs per UTC month | 200 | 5,000 | Contract-configurable or unlimited |
| Connections per provider type | 1 | 5 | Contract-configurable or unlimited |
| Cloud bundle upload | 2 GiB per import | 10 GiB per import | Contract-configurable |
| Self-hosted profile storage | Host disk capacity | Host disk capacity | Customer-configured |
| Cloud profile storage | 5 GiB | 100 GiB | Contract-configurable |
| Cloud telemetry retention | 7 days basic | 30 days | Contract-configurable, recommended 30–365 days |

Numeric `-1` in `pkg/edition.Limits` means unlimited. Do not use `0` as unlimited; zero means the capability is unavailable.

Storage, telemetry retention, team/delegation limits, and import size are recommended commercial defaults that still require cost validation before launch. The implementation enforces active sandboxes, agents, cron definitions, parallel cron runs, atomic daily/monthly cron runs, provider connections, teams, agents per team, delegated workers, telemetry ingestion, and import size at the applicable local or tenant-scoped service boundary.

## Hardware classes

The current Compose setup does not declare a CPU or memory limit for Studio/Hermes: it uses the resources made available by the host or container platform. Therefore `1× default` is currently a named baseline, not a fixed vCPU/RAM promise.

- Free self-hosted uses the resources provided by the user's machine. Free cloud receives the `default` hardware class.
- Pro self-hosted requires the operator to provide at least twice the default CPU and RAM if they want cloud parity. Pro cloud receives the `pro-2x` class with twice the default CPU and RAM. Storage remains governed by the separate storage quota.
- Enterprise selects contract-defined CPU, RAM, storage, accelerator, region, and placement classes.

Before cloud launch, define the exact `default` class once in the enterprise infrastructure catalog; derive `pro-2x` from it. Do not hard-code cloud CPU or RAM values in the open-source repository.

## Enforcement behavior

Different restrictions require different HTTP and scheduling behavior:

| Restriction | Behavior |
|---|---|
| Count, daily, or monthly quota exhausted | Return HTTP `429` with `RateLimit-Limit`, `RateLimit-Remaining`, `RateLimit-Policy`, and an applicable `RateLimit-Reset` |
| Manual cron exceeds parallel capacity | Return `429`; do not start another run |
| Scheduled cron exceeds parallel capacity | Keep the job queued; do not discard or bill the run until capacity is reserved |
| Provider rotation at the account limit | Allow replacement; only an additive connection consumes another slot |
| Feature not included in the plan | Return `403` with a stable `plan_required` error code |
| Missing or invalid login in a paid deployment | Return `401` |
| Import or upload exceeds its size limit | Return `413` before extraction |
| Storage capacity exhausted | Reject the mutation without leaving partial files and return a stable `storage_limit_reached` error |

HTTP middleware is only the presentation boundary. Services must repeat count and capability checks, and multi-replica paid deployments must reserve usage atomically in PostgreSQL. Traces and rate-limit headers are observability data, not the billing source of truth.

## Storage and portability contract

Self-hosted Free should ultimately use no database: agent identity comes from `profiles/<agent-id>/config.yaml`, durable Hermes data remains inside the profile, cron definitions and local usage use small atomically written files, and runtime IP/port data is a disposable cache recreated on startup. Cloud Free uses the paid PostgreSQL control plane because it needs account ownership, quota accounting, managed runtime identity, and backups.

Pro and Enterprise use PostgreSQL for users, tenants, plans, sandbox identity, desired hardware, agent-to-sandbox routing, quotas, imports, and audit metadata. VM IP addresses and ports are discovered runtime endpoints, not permanent identity. Profile contents remain on a persistent volume or approved object storage.

A portable `.lumora` bundle contains versioned manifests, profiles, memory, skills, workspaces, snapshots, optional conversations, cron definitions, and checksums. It excludes provider credentials, runtime tokens, PIDs, sockets, absolute machine paths, and endpoint addresses. Paid imports remap ownership and conflicting IDs, create PostgreSQL metadata, import cron jobs paused, reset persistent approvals, and require provider reconnection before execution.

## Current implementation status

The repository implements file-only Community persistence, the public `pkg/studio` composition contract, numeric quota middleware and service enforcement, atomic daily/monthly cron usage, managed/local cron separation, compact missed-run notifications, restart-safe profile data, safe portable bundles including teams, the outbound signed-command connector, disk-backed redacted telemetry delivery, and bounded agent teams with a Free UI. PostgreSQL, Redis, Kafka, managed hardware, and paid control-plane services remain isolated in `open-lumora-enterprise`. Cross-repository worker import plus disposable PostgreSQL migration, isolation, concurrency, restart, and rollback gates pass. Production image execution, scale, disaster recovery, and independent security evidence remain release gates.
