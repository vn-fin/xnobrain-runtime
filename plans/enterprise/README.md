# Enterprise program — plans index

Design plans for the **XNOBrain Enterprise** edition: a central control plane that
manages a fleet of **single-user deployments** (each user runs the OSS runtime in an
Incus container or on their own PC) and collects **all usage — chats, tokens, cost —
into one central database**.

These are planning documents. Implementation is split across two repositories per
`docs/repository-ownership.md`:

- **`xnobrain-enterprise`** (private, Go + PostgreSQL) — the control plane: ingest
  API, database, dashboards, auth, fleet management. It consumes the released OSS
  runtime interface and must never be required by the OSS deployment.
- **`xnobrain`** (this repo, Python OSS) — only the thin edge pieces: the usage
  reporter, the device connector, and entitlement-gated UI. All OSS behavior stays
  fully functional when the enterprise server is unreachable (`AGENTS.md`: an
  Enterprise API outage must not restrict local features).

## Grounding (read before any plan)

| Source | What it fixes |
|---|---|
| `docs/plans.md` | Editions matrix (free/pro/promax/enterprise); telemetry pipeline `runtime → OTel → Enterprise API → ClickHouse`; the telemetry boundary (metadata + token/cost counts only — never prompts/responses) |
| `docs/contracts/device-command-v1.md` | **Device identity** (Ed25519 key, outbound-only TLS 443, device_id + short-lived tokens, at-least-once + idempotency, offline cursor, anonymous → account-claimed) |
| `docs/contracts/entitlements-v1.md` | Entitlement documents + quota Check/Reserve/Commit/Release with idempotency keys |
| `docs/enterprise-extension.md` | Go control plane, `pkg/edition.Policy`, **PostgreSQL is the billing source of truth**, reservation semantics |
| `docs/implementation/03-device-connector.md`, `05-telemetry.md` | Connector responsibilities and redaction rules. ⚠️ These numbered files are **retired Go-era specs** — concepts and contracts stand; package paths do not. OSS-side code is Python. |
| `plans/009_usage_analytics/` (implemented) | The local usage source: per-agent `state.db` `sessions` rows already carry input/output/cache/reasoning tokens, estimated/actual cost, api_call_count — read-only aggregation machinery exists (`xnobrain/integrations/analytics.py`) |
| `product/reports/` (esp. 11-integration-architecture, 07-business-model) | Go + PostgreSQL control-plane strategy; the moat lives in the enterprise layer |

## The core architecture: distributed usage → one database

The question this program answers: *each user runs their own isolated runtime (Incus
container or personal PC, usually behind NAT); how does the enterprise collect every
user's chat/token usage into one database?*

**Answer: outbound-only push with device identity, session-snapshot upserts, and an
offline outbox.** No inbound connections to user machines, no polling of user PCs.

```
 user PC / Incus container (per user)                    central enterprise server
┌──────────────────────────────────────┐               ┌────────────────────────────────┐
│ OSS runtime (FastAPI + Hermes)       │   HTTPS 443   │ xnobrain-enterprise (Go)      │
│  agents' state.db (sessions: tokens, │   outbound    │  POST /ingest/v1/usage         │
│  cost, counts — per plan 009)        │   only        │   device-token auth            │
│        │ read-only, watermarked      │               │        │ UPSERT (idempotent)   │
│        ▼                             │               │        ▼                       │
│  usage reporter (new, Python)        ├──────────────►│  PostgreSQL (ONE database)     │
│   session snapshots — NO content     │   batches +   │   usage_session_snapshots      │
│        │                             │   idempotency │   usage_rollups_daily          │
│        ▼                             │   keys        │   devices · users · tenants    │
│  outbox spool (atomic files,         │               │        │                       │
│  bounded; survives offline)          │◄──────────────┤  acked watermark per device    │
│  device identity (Ed25519,           │               │  dashboards / billing / export │
│  device-command-v1)                  │               │  (ClickHouse stays for traces) │
└──────────────────────────────────────┘               └────────────────────────────────┘
```

Why each choice:

1. **Push, not pull.** User PCs are behind NAT/firewalls; `device-command-v1` already
   mandates outbound-only TLS with no inbound listener. The same device identity
   (enrolled anonymously, then **claimed** by an org user account — mapping device →
   user → tenant) authenticates usage uploads.
2. **Session snapshots, not deltas.** A Hermes session's token counters are
   **monotonically cumulative** in `state.db`. The reporter sends the *latest
   snapshot* per session `(session_id, agent_hash, model, token counters, costs,
   api_calls, message_count, started_at, updated_watermark)`; the server **UPSERTs
   the latest row per (tenant, device, agent, session)**. At-least-once delivery +
   idempotent upsert = exactly-once accounting with zero delta bookkeeping and no
   double counting on retries.
3. **Offline outbox.** Snapshots are spooled locally (atomic files, bounded size,
   oldest-dropped-with-counter per `05-telemetry.md`) and pushed with backoff; a
   laptop offline for a week uploads correctly on reconnect. The server acks a
   **watermark** per device; the reporter advances its cursor only on ack.
4. **One database = PostgreSQL, billing-grade.** Per `docs/enterprise-extension.md`
   the control plane's Postgres is the billing source of truth. Usage rows land
   there (snapshots + daily rollups per user/device/model). The already-designed
   **ClickHouse** pipeline stays for high-volume *trace* telemetry (ops
   observability) — it is not the accounting store. Headers/traces are never
   accounting state (`entitlements-v1`).
5. **Privacy boundary is inherited, not new.** Only metadata and counters cross the
   wire — session/agent identifiers (hashed per `05-telemetry.md` allowed
   attributes), model names, token/cost numbers, timestamps. **Never prompts,
   responses, memories, skills, tool arguments, or credentials.** "Collect all
   chats" = chat *counts and token totals*, not chat *content*.

## Fixed product requirements (owner decisions, 2026-07-25)

These are set by the product owner and bind every plan in this program:

1. **Org-created accounts.** In org mode, accounts are provisioned by the
   organization (invite/import), not open self-signup.
2. **Pluggable auth + config-file RBAC.** The auth service is swappable (local
   accounts, OIDC/SAML SSO, LDAP, or a custom org auth service), and **services and
   roles can be declared in a config file** — an ops team can define
   roles/permissions in versioned YAML without touching a database console.
3. **Control plane runs self-hosted or cloud.** The same `xnobrain-enterprise`
   deploys on a firm's own servers (including air-gapped) or as our managed cloud.
4. **Org manager sees everything.** A built-in org-manager role can view all
   members' usage, devices, and dashboards across the org.
5. **Cloud users may be org members or individuals.** On the managed cloud, a user
   either belongs to an organization (org tenant) or stands alone in a personal
   tenant (consistent with `docs/plans.md`: Cloud Free/Pro personal tenants;
   Enterprise adds organizations).

## Important enterprise functions (the build map)

✦ = covered by a plan package below. Grouped the way enterprise buyers evaluate
(market anchors: n8n enterprise = SAML SSO, folder-level RBAC, full audit logs,
air-gapped deploys; Dify enterprise = SCIM/SAML, model access control, retention
rules, VPC; segment checklists = SOC 2 / GDPR / ISO 27001 + RBAC/SSO/audit — see
program research notes in E05 findings).

**Identity & access**
1. ✦ **Control-plane foundation** (E01) — Go skeleton, PostgreSQL schema, device
   enrollment/claim/revoke, entitlements endpoints.
2. ✦ **Orgs, accounts, auth & RBAC** (E05) — org model + org-created accounts,
   pluggable auth (local / OIDC / SAML / LDAP / custom service), **config-file role
   definitions**, org-manager view-all, personal-vs-org cloud tenancy, SCIM
   provisioning/deprovisioning (market bar), session policies (MFA per IdP, token
   TTLs).
3. **SSO federation hardening** — IdP-initiated flows, group→role mapping sync,
   just-in-time provisioning. (Phase 2 of E05.)

**Visibility & accounting**
4. ✦ **Central usage collection** (E02) — one PostgreSQL of chats/tokens/cost;
   org-manager dashboards; CSV export.
5. **Budgets, chargeback & cost centers** — per-user/team/org budgets with hard or
   advisory caps, cost-center tags, invoice/chargeback export. (Extends E02 +
   entitlements-v1 reservations.)
6. **Central telemetry at scale** — the ClickHouse trace pipeline from plans.md
   (retention per plan tier). Ops, not billing.

**Governance & policy**
7. **Model & capability policy** — org allowlists of models/providers/blends, tool
   and MCP-server policy (which capabilities members' agents may use), skill/plugin
   approval workflow for the fleet. (Market: Dify "AI model access control".)
8. **Data retention & residency** — org-set retention rules for centrally held
   data; region pinning for the managed cloud. (Content stays on user devices by
   design — see the privacy boundary; retention here governs central metadata.)
9. **External secrets integration** — org provider keys held in the org's vault
   (HashiCorp Vault/KMS) rather than our tables, for firms that require it.

**Compliance & audit**
10. **Audit log everywhere** — append-only audit of logins, role changes, device
    ops, policy changes, exports; **SIEM/log streaming**; evidence exports.
    (Foundation lands in E01/E05; streaming is a follow-on.)
11. **Compliance program** — SOC 2 / ISO 27001 artifacts, GDPR DPA, security
    reports. (Organizational workstream, tracked here for completeness.)

**Fleet & runtime**
12. ✦ **Fleet management** (E03) — Incus per-user containers, versions, health,
    remote lifecycle; PC-installed users in the same fleet.
13. ✦ **Voice I/O as enterprise capability** (E04) — central voice gateway,
    org-held provider keys, metered minutes.
14. **Backup / DR** — managed profile-bundle backups (portable-bundle-v1),
    encrypted; control-plane HA + DR runbooks.
15. **License & update management** — fleet version pinning, staged rollouts,
    offline license keys for air-gapped installs.

**Deferred / contested (decide later, stated honestly)**
- **Conversation-content audit ("eDiscovery mode").** Some enterprise buyers ask
  for org access to members' conversation content. This **conflicts with the
  program's privacy boundary** (content never leaves the user's machine). If ever
  built, it must be an explicit org-policy mode on *org-owned managed containers
  only*, disclosed to members, with content retained device-side and accessed via
  fleet policy — never silently streamed to the center. Not planned in this wave.

## Plan packages

| # | Plan | Repo(s) touched | Docs |
|---|------|-----------------|------|
| E01 | **Control-plane foundation** | `xnobrain-enterprise` (new) + contracts here | [README](E01_control_plane_foundation/README.md) · findings · architecture · approaches · implementation · validation |
| E02 | **Central usage collection** | both (reporter in OSS; ingest in enterprise) | [README](E02_usage_collection/README.md) · findings · architecture · approaches · implementation · validation |
| E03 | **Fleet management (Incus + devices)** | both (connector in OSS; orchestration in enterprise) | [README](E03_fleet_management/README.md) · findings · architecture · approaches · implementation · validation |
| E04 | **Voice I/O as enterprise capability** | both (gateway in enterprise; gated UI in OSS) | [README](E04_voice_io_enterprise/README.md) · findings · architecture · approaches · implementation · validation |
| E05 | **Orgs, accounts, auth & RBAC** | `xnobrain-enterprise` + thin OSS login/entitlement surfaces | [README](E05_orgs_auth_rbac/README.md) · findings · architecture · approaches · implementation · validation |
| E07 | **Enterprise boards (cross-account Kanban)** | both (board/dispatch in enterprise; local executor in OSS) | [README](E07_enterprise_boards/README.md) · findings · architecture · approaches · implementation · validation |
| E08 | **Service platform: Go app services + Python AI services** (first build: STT on Groq Whisper v3) | `xnobrain-enterprise` (refines E04's gateway split) | [README](E08_service_platform/README.md) · [implementation](E08_service_platform/implementation.md) |

**Order:** E01 → E02 (the user-visible value: one usage database) → E05 (orgs/auth —
required before real multi-user rollout) → E03 → E04. E02 is deliberately buildable
with only the *device enrollment* slice of E01; E05 replaces E01's static-admin-token
stopgap with real accounts and roles.

## Program principles

- The OSS repo stays a complete product; enterprise pieces in this repo are
  **dormant unless `ENTERPRISE_API_URL` is configured** and never block local use.
- Cross-repo protocol changes go through versioned contracts in `docs/contracts/`
  (new: `usage-ingest-v1`). Incompatible changes create `v2`.
- The control plane is Go + PostgreSQL; no ORM (both repos, per
  `docs/enterprise-extension.md`).
- Privacy: the `05-telemetry.md` allow/forbid lists apply to every byte that leaves
  a user's machine. Redaction tests are mandatory, not optional.
- Numeric `-1` = unlimited; telemetry/headers are never accounting state.

## Checklist

- [ ] E01 accepted (validation.md evidence)
- [ ] E02 accepted — a fleet of ≥2 devices reports usage into one Postgres; dashboard
      shows per-user tokens/cost; offline device catches up without double counting
- [ ] E03 accepted
- [ ] E04 accepted — voice works only with entitlement; minutes metered centrally
- [ ] `usage-ingest-v1` contract published in `docs/contracts/`
- [ ] Local plan 006 marked as relocated to E04 (see `plans/LOCAL_FEATURES_CHECKLIST.md`)
