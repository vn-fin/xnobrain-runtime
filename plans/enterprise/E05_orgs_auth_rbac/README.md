# E05 — Orgs, accounts, auth & RBAC

Priority: **P1 — required before any real multi-user rollout.** E01's
static-admin-token stopgap ([E01 approaches.md decision D]
(../E01_control_plane_foundation/approaches.md)) was explicitly designed to be
"deleted, not layered over" — this plan deletes it. E02's central usage
database exists today but can only be read by whoever holds one shared secret;
the moment a second real human needs access, E05 is the blocker. Program
order: E01 → E02 → **E05** → E03 → E04
([program README](../README.md) § "Plan packages").

This plan implements program build-map items **2 (Orgs, accounts, auth &
RBAC)** and the Phase-2 foundation of **3 (SSO federation hardening)** from
[`plans/enterprise/README.md`](../README.md) § "Important enterprise
functions". Implementation lands almost entirely in the private
`brain4all-enterprise` repo (Go + PostgreSQL, no ORM); this repo gains only a
thin, dormant sign-in surface and one versioned contract.

Read the sibling documents in order:

- [findings.md](findings.md) — what E01/E02 provide and what changes, the
  binding doc quotes, the market bar (program market research), the tenancy
  and file-vs-DB analyses, SCIM lifecycle mapping, risks, open questions.
- [architecture.md](architecture.md) — full DDL, the complete annotated
  `auth.yaml`, the permission catalog, principal-resolution flow, login
  sequences per backend, SCIM slice, endpoint table, deployment topologies,
  break-glass procedure.
- [approaches.md](approaches.md) — six decisions (role shapes file-vs-DB,
  admin/manager split, tenancy v1, token format, SCIM now-vs-later, MFA)
  with options and rationale.
- [implementation.md](implementation.md) — ordered phases, file-by-file for
  both repos, Go test plan per phase, E01/E02 handler retrofits one by one.
- [validation.md](validation.md) — acceptance checklist with evidence lines.

Also read before starting (mandatory grounding):
[`plans/enterprise/README.md`](../README.md) (program frame + fixed
requirements), [`plans/enterprise/E01_control_plane_foundation/`](../E01_control_plane_foundation/README.md)
(the schema and endpoints this plan extends),
[`plans/enterprise/E02_usage_collection/`](../E02_usage_collection/README.md)
(the admin endpoints this plan gates),
[`docs/plans.md`](../../../docs/plans.md),
[`docs/enterprise-extension.md`](../../../docs/enterprise-extension.md),
[`docs/contracts/entitlements-v1.md`](../../../docs/contracts/entitlements-v1.md),
[`docs/repository-ownership.md`](../../../docs/repository-ownership.md),
[`AGENTS.md`](../../../AGENTS.md).

## Goal

Give the control plane real people: organizations with org-provisioned
accounts, pluggable authentication, config-file-declared roles, and RBAC
enforcement on every admin surface — deployable identically on a firm's own
servers (including air-gapped) and on our managed cloud.

Concretely:

1. **Org model on E01's schema** — `orgs` (backed by E01's
   `tenants.kind='organization'`), `org_memberships`, `invitations`,
   `user_role_assignments`, session storage, and org/actor-scoped
   `audit_events`; personal-tenant bootstrap for standalone cloud users
   stays exactly as E01 built it.
2. **Pluggable authn** — five selectable backends: built-in local accounts
   (argon2id), OIDC, SAML 2.0, LDAP bind, and the custom external auth
   service over gRPC that `docs/enterprise-extension.md` already anticipates
   (E01's `internal/authseam` grows into one backend among five).
3. **Config-file RBAC** — `auth.yaml`: backend wiring, role definitions
   (permission lists), optional static bindings, IdP group→role mappings.
   Role *shapes* live in the versioned file; per-user role *assignments*
   live in PostgreSQL ([approaches.md](approaches.md) decision A).
4. **RBAC enforcement everywhere** — a permission model of `resource.verb`
   strings and a principal-resolution middleware applied to every existing
   E01/E02 admin endpoint (each one listed in
   [implementation.md](implementation.md) § "Retrofits"), replacing
   `ADMIN_TOKEN`.
5. **Account lifecycle + SCIM** — invite → accept → active → suspended →
   deprovisioned; email invites, CSV import, and a minimal SCIM 2.0 Users
   endpoint mapping to the same lifecycle (market bar — see
   [findings.md](findings.md) §4).
6. **Org-manager visibility** — `org_manager` reads all members' E02 usage
   dashboards and the members list; `member` reads only self; `auditor`
   reads the audit trail.
7. **Thin OSS surface** — the "self-hosted, signed in" row of
   `docs/plans.md`: a login stub, session storage, and an extended
   `/api/v1/limits` payload with org context + capability flags. Dormant
   without `ENTERPRISE_API_URL`.

## The five fixed requirements (owner decisions, 2026-07-25)

Restated from [`plans/enterprise/README.md`](../README.md) § "Fixed product
requirements"; this plan implements exactly these:

1. **Org-created accounts.** In org mode the organization provisions
   accounts — email invites, CSV import, SCIM. Open self-signup is disabled
   for org tenants. Cloud *personal* tenants may still self-signup; that
   distinction is plan-level (`docs/plans.md` Cloud Free/Pro) and preserved.
2. **Pluggable auth + config-file RBAC.** Auth backends selectable per
   deployment (local / OIDC / SAML 2.0 / LDAP / custom gRPC service), and
   roles/permissions/service wiring declarable in versioned `auth.yaml` —
   an ops team defines roles without touching a database console.
3. **Self-hosted or cloud control plane.** The same binary and compose file
   run on a firm's servers — including air-gapped, with zero external calls
   when the backend is local or LDAP — or as our managed cloud. The
   config-file-first design of requirement 2 is what makes this true.
4. **Org manager views all.** Built-in `org_manager` role: read-everything
   in the org — all members' usage dashboards (E02), devices/fleet (E03
   when it lands), voice metering (E04 when it lands), audit log — distinct
   from `org_admin` (manage members/roles/policies). One person may hold
   both ([approaches.md](approaches.md) decision B).
5. **Cloud: org member or standalone.** Every authenticated user owns a
   personal tenant (E01's bootstrap); a user may additionally hold at most
   one org membership in v1, with the schema ready for N
   ([approaches.md](approaches.md) decision C). Consistent with
   `docs/plans.md`: Cloud Free/Pro = one member in one personal tenant;
   Enterprise adds organizations with multiple members.

## Non-goals

- **No SIEM/log streaming.** Audit events are stored append-only and
  exportable via an endpoint; pushing them to Splunk/Datadog/syslog is
  program build-map item 10's follow-on, not this plan.
- **No model/capability policy engine.** Org allowlists of
  models/providers/tools are build-map item 7.
- **No budgets, billing, or subscription integration.** Build-map item 5.
  Plan assignments remain manual rows per E01.
- **No compliance artifacts.** SOC 2 / ISO 27001 / DPA documents are
  build-map item 11 (organizational workstream). E05 builds the audit
  *substrate* those programs need, nothing more.
- **No conversation content access.** The program README's "Deferred /
  contested" note stands verbatim: eDiscovery-style org access to member
  conversation content conflicts with the privacy boundary and is not
  planned in this wave. `org_manager` sees counts, tokens, and cost — never
  content.
- **No SSO federation hardening beyond the base flows.** IdP-initiated
  SAML, JIT provisioning refinements, and continuous group sync are Phase 2
  (program build-map item 3); this plan ships SP-initiated OIDC/SAML with
  group→role mapping evaluated at login.
- **No admin web UI build-out.** JSON endpoints plus the minimal members
  list and the E02 dashboard org gate. Full console UI is later.
- **No changes to OSS local behavior.** Per `AGENTS.md`, an Enterprise API
  outage must never restrict local features; every OSS piece here is
  dormant without `ENTERPRISE_API_URL`.

## Dependencies

- **E01 (hard).** Schema v1 (`tenants`, `users`, `devices`,
  `audit_events`, `plans`), the `internal/authseam` seam, ID conventions,
  the migrations runner, and the admin endpoints being retrofitted. E05
  migrations extend E01's history; they never rewrite it
  (`docs/enterprise-extension.md`).
- **E02 (soft).** The org-manager dashboard requirement gates E02's
  `GET /admin/v1/usage` and CSV export. If E02 is not yet merged in the
  enterprise repo, the RBAC middleware still lands and E02 adopts it on
  arrival; the acceptance scenario needs both.
- **E03/E04 (none).** They come after E05 and are born RBAC-gated; this
  plan defines the permissions they will use (`devices.manage`,
  `voice.read_org` reserved in the catalog).

## Phases

- **Phase 0 — Freeze the contracts.** Permission catalog + `auth.yaml`
  schema + the OSS-facing `enterprise-auth-v1` HTTP contract. These are
  referenced by every later plan; they freeze first.
- **Phase 1 — Config loader.** `auth.yaml` parse/validate/hot-reload.
- **Phase 2 — Schema + store.** Migrations for orgs, memberships,
  invitations, role assignments, credentials, identities, sessions, audit
  extensions; raw-SQL repositories.
- **Phase 3 — Local backend + sessions.** argon2id accounts, opaque
  access/refresh tokens hashed at rest, logout/revocation.
- **Phase 4 — Authz engine + middleware.** Effective-permission resolution,
  enforcement, audit of every decision-relevant mutation.
- **Phase 5 — Retrofit E01/E02 admin endpoints.** `ADMIN_TOKEN` deleted;
  break-glass CLI replaces it for emergencies.
- **Phase 6 — Orgs, invitations, lifecycle, CSV import, members list.**
- **Phase 7 — OIDC backend + group→role mapping.**
- **Phase 8 — SAML, LDAP, external-gRPC backends.**
- **Phase 9 — SCIM 2.0 Users slice.**
- **Phase 10 — OSS thin surface + org-scoped E02 dashboard; joint
  validation.**

File-by-file steps are in [implementation.md](implementation.md).

## Definition of done

- On a **self-hosted** control plane (single compose, no external SaaS), an
  org admin defines roles in `auth.yaml`, boots the stack, and invites two
  members by email.
- One invitee signs in via **OIDC**, the other via a **local password**
  (argon2id-hashed); both become active org members.
- A user holding `org_manager` opens the E02 usage view and sees **both**
  members' usage; each `member` sees **only their own**; a user holding
  `auditor` reads the audit trail containing every step above (invites,
  accepts, logins, role grants) with actors attributed.
- On the **managed cloud**, a standalone user signs up, gets a personal
  tenant, and uses usage/entitlement features **without any org existing**.
- `ADMIN_TOKEN` no longer exists in the enterprise repo; every former
  static-token endpoint enforces a catalog permission; the break-glass
  procedure is documented and tested.
- All evidence lines in [validation.md](validation.md) are green, including
  the security lines: argon2id verified, tokens hashed at rest and never
  logged, suspended users cut off within the access-token TTL, org A
  manager cannot read org B, air-gapped run shows zero external egress.
