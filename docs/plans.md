# Product editions and deployment modes

This is the engineering summary of the product contract. The canonical product details
are:

- [simple comparison](../product/specs/compare-features.md)
- [OSS specification](../product/specs/oss.md)
- [Enterprise specification](../product/specs/enterprise.md)
- [plans and cloud limits](../product/specs/plans.md)
- [entitlements protocol](contracts/entitlements-v1.md)

## Product rules

- Edition controls capabilities; deployment controls resources.
- `free` and `pro` are single-user personal editions.
- `enterprise` includes Pro and adds organizations, collaboration, governance, and
  administration.
- Self-hosted local resources resolve to unlimited.
- Cloud resources are limited because Brain4All supplies the hardware.
- Downgrade or quota exhaustion pauses or makes resources read-only; it never deletes
  user data.

## Runtime ownership

`brain4all` owns the React application and combined Python/FastAPI/Hermes/9router
runtime. It owns local agents, profiles, skills, memory, MCP, providers, teams,
conversations, Kanban, and cron without an application database.

`brain4all-enterprise` owns the Go control plane, PostgreSQL, authentication, tenants,
plans, billing-grade quota state, RBAC, audit, fleet management, encrypted collaboration
relay, hosted services, and managed cloud packaging. It consumes released OSS contracts
and must not fork the OSS runtime.

## Edition capabilities

| Capability | Free | Pro | Enterprise |
|---|:--:|:--:|:--:|
| Complete single-user runtime | ✅ | ✅ | ✅ |
| Marketplace install and publish | — | ✅ | ✅ |
| Speech-to-text | — | ✅ | ✅ |
| Hosted skill/memory versions | — | ✅ | ✅ |
| Organizations and many users | — | — | ✅ |
| Custom organization Kanban | — | — | ✅ |
| Cross-user agent teams | — | — | ✅ |
| SSO, SCIM, RBAC and administration | — | — | ✅ |
| Central usage and enforced budgets | — | — | ✅ |
| Audit, policy and fleet management | — | — | ✅ |

The four entitlement flags that distinguish Free from Pro are
`marketplace.install`, `marketplace.publish`, `voice.stt`, and
`snapshots.versions`. Marketplace install and publish are one product service group.
Enterprise inherits every Pro flag.

## Deployment behavior

| Deployment | Login | Capability set | Resource enforcement |
|---|---|---|---|
| Self-hosted Free | Optional | Free | Local resources unlimited |
| Self-hosted Pro | Required for paid services | Pro | Local resources unlimited |
| Cloud Free | Required | Free | `cloud_free` limits |
| Cloud Pro | Required | Pro | `cloud_pro` limits |
| Cloud Pro Max | Required | Pro | `cloud_pro_max` limits |
| Enterprise self-hosted | Required; air-gap supported | Enterprise | Contract/customer hardware |
| Enterprise Cloud | Required | Enterprise | Contracted org limits |

The default Compose deployment is self-hosted and signed out. It must continue to work
without Internet, login, or an Enterprise API.

## Cloud quota model

The recommended values live in
[the product plan](../product/specs/plans.md#72-what-we-limit--and-the-recommended-values).
The control plane stores them as versioned plan data rather than hard-coding them into
the OSS runtime.

Initial resource names include:

- vCPU, RAM, disk, concurrent turns, and idle-suspend threshold;
- agents, teams, agents per team, provider accounts, MCP servers, channels, skills,
  boards, and tasks;
- cron definitions, parallel runs, run-minutes, run duration, and runs per day;
- workspace bytes, backup count, detailed-history retention, snapshot versions, and
  snapshot bytes.

Numeric `-1` means unlimited and `0` means unavailable. Cloud Free is permanent. A Pro
trial is a temporary entitlement overlay; expiry returns the tenant to Cloud Free
without deleting data.

## Enforcement

- `Check` is advisory; `Reserve`, `Commit`, and `Release` are authoritative.
- Cloud count and usage limits are transactional in PostgreSQL.
- Self-hosted local capabilities remain available when the control plane is offline.
- Missing authentication returns `401`, unavailable capability returns `403`, exhausted
  quota returns `429`, and oversized input returns `413`.
- Telemetry and HTTP headers are never accounting state.
- No service uses an ORM.

## Privacy boundary

The control plane stores safe metadata, counts, operational events, and audit records. It
does not store plaintext prompts, responses, memories, skill content, tool arguments,
credentials, or profile files.

Cross-user task inputs, attachments, and results may be relayed only as end-to-end
encrypted payloads under the [device command protocol](contracts/device-command-v1.md).
Hosted skill/memory snapshots are client-side encrypted. Enterprise escrow use is
permissioned, justified, and audited.

Before cross-user execution ships, freeze a separate `collaboration-payload-v1` contract
for the encrypted inner payload. The existing device contract remains the delivery
envelope.
