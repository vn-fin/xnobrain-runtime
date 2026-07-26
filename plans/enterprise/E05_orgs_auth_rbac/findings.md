# E05 — Findings

What already exists, what the binding documents fix, what the market
demands, and the analyses behind the decisions in
[approaches.md](approaches.md). Cross-links: [README.md](README.md),
[architecture.md](architecture.md), [implementation.md](implementation.md),
[validation.md](validation.md).

## 1. What E01 provides, and what E05 changes

### 1.1 Provided (reused as-is)

From [`plans/enterprise/E01_control_plane_foundation/architecture.md`](../E01_control_plane_foundation/architecture.md):

- **Schema v1**: `tenants` (with `kind IN ('personal','organization')` —
  org tenants are already "schema-ready but have no E01 surface"), `users`
  (`auth_subject` unique reference; row created lazily on first
  authentication), `devices`, `device_tokens` (opaque tokens, only
  `sha256(raw)` at rest), `plans` + `plan_assignments` (user- and
  tenant-level rows, tenant-level "ready for org/contract plans"),
  append-only `audit_events` with `actor_type/actor_id/tenant_id` and a
  trigger blocking UPDATE/DELETE.
- **Personal-tenant bootstrap**: "Personal tenant is auto-created the first
  time a principal … is seen: one tenant, one member." E05 keeps this
  verbatim for standalone cloud users (fixed requirement 5).
- **`internal/authseam`**: `PrincipalResolver` interface + gRPC client
  skeleton + dev static resolver. E05 generalizes this seam into the
  pluggable-backend registry; the gRPC client becomes backend (e), the
  "custom external auth service".
- **Token discipline**: opaque prefixed tokens, hash-only storage, rotation
  on refresh, never-logged enforcement by test. E05's user session tokens
  mirror this exactly ([approaches.md](approaches.md) decision D).
- **ID conventions** (`internal/ids`): E05 adds prefixes `org_`, `inv_`,
  `ses_`, `ras_`, `idn_`.
- **Migrations runner** over plain SQL files; E05 appends the next free
  migration number (E02 claims one too — resolve the concrete number at
  merge time, see [implementation.md](implementation.md) Phase 2).

### 1.2 Changed: static admin token → RBAC

E01 decision D ([approaches.md](../E01_control_plane_foundation/approaches.md))
chose a "single static admin bearer token from `ADMIN_TOKEN` env" and stated
the exit criterion: when real auth arrives, "the `ADMIN_TOKEN` path is
deleted, not layered over." E05 executes that deletion. Every endpoint that
authenticates via `ADMIN_TOKEN` today moves to catalog permissions:

| Endpoint (E01/E02) | Today | After E05 |
|---|---|---|
| `GET /admin/v1/devices` | static `ADMIN_TOKEN` | session + `devices.read_org` (org-scoped) or `platform.operate` |
| `GET /admin/v1/tenants` | static `ADMIN_TOKEN` | session + `platform.operate` (cross-tenant; operators only) |
| `POST /device/v1/revoke` (admin variant) | static `ADMIN_TOKEN` | session + `devices.manage`; owner/device self-revoke unchanged |
| `GET /admin/v1/usage` (E02) | "admin auth comes from E01's principal resolution" (E02 architecture §7 — i.e. the stopgap) | session + `usage.read_org`, org filter forced to caller's org |
| `GET /admin/v1/usage/export.csv` (E02) | same stopgap | session + `usage.export` |

Unchanged: `/device/v1/register|prove|refresh` (device possession auth),
`POST /ingest/v1/usage` and `POST /ingest/v1/labels` (device-token auth,
E02), `/entitlements/v1/current` (device or user auth — gains org context
in the document subject), `/healthz`, `/readyz`.

`POST /device/v1/claim` changes *internally*: the "user bearer" it resolves
via `authseam` becomes an E05 session token; behavior is otherwise
identical.

### 1.3 What E02 provides that E05 gates

E02's [`architecture.md`](../E02_usage_collection/architecture.md) §7
defines `GET /admin/v1/usage?tenant=…&user=…&device=…&model=…&from=…&to=…&bucket=…`
and the CSV export, served from `usage_rollups_daily`, and explicitly
defers auth to this plan: "Admin auth comes from E01's principal resolution
… this plan only defines the routes and queries." E05 supplies the gate:
`org_manager` gets the whole org, `member` gets `user=self` forced, and the
`tenant` parameter is overridden server-side with the caller's org tenant
(cross-org queries only under `platform.operate`).

## 2. What the binding documents fix (quotes)

### 2.1 `docs/plans.md`

- Deployment row this plan's OSS surface implements: **"Self-hosted, signed
  in | Required only for extensions | Unlimited | Usage, traces, metrics,
  and plan features | Plan applies only to Enterprise API features."**
- Dormancy rule: **"The Enterprise API is external to this stack;
  authenticated routes remain unavailable until `ENTERPRISE_API_URL` is
  configured and a user signs in."**
- Plan matrix: "RBAC/shared workspaces" = Planned for Pro Max /
  Planned-contract for Enterprise; "SSO and audit policy" = ❌ for
  free/pro/promax, **"Planned/contract"** for Enterprise. So RBAC/SSO/audit
  are Enterprise capabilities — E05 must gate them by capability flags, not
  ship them to Free.
- Enforcement: "missing login returns `401`"; "unavailable paid features
  return `403`".

### 2.2 `docs/enterprise-extension.md`

- The auth seam: **"Enterprise principal resolution should call the
  existing external auth service over gRPC rather than migrating
  authentication code here."** E05 keeps that path as backend (e) — the
  custom external auth service — while adding the four self-contained
  backends the owner requires. The external service is one plug among five,
  not the only option.
- Tenant propagation: **"Tenant identity must flow into policy,
  repositories, storage prefixes, telemetry, and quota reservations."**
  E05's principal carries `{user_id, tenant_id, org_id}` and every
  repository call in the enforcement path takes it (see
  [architecture.md](architecture.md) § middleware flow).
- The tenancy sentence E05 must stay consistent with: **"Cloud Free and Pro
  each resolve one authenticated member in one personal tenant with
  different quotas and hardware classes. Enterprise adds organizations,
  multiple members, RBAC, SSO, audit policy, and contract-configurable
  entitlements."**
- Ownership: "The enterprise project owns authentication, tenant and plan
  resolution, billing entitlements, distributed quota reservations, RBAC,
  audit events, secret management…" — everything in this plan except the
  thin login stub belongs in `brain4all-enterprise`.
- "Enterprise migrations may extend but must not rewrite the open-source
  migration history" — applied analogously to E01's migration history
  inside the enterprise repo.

### 2.3 `docs/contracts/entitlements-v1.md`

**"Capabilities include managed scheduler, managed backup, managed
telemetry, hardware selection, custom plugins, advanced cron chains/scripts,
collaboration, RBAC, SSO, audit export, and batch trajectories."** E01
already seeds `rbac`, `sso`, `audit_export` capability flags in the `plans`
JSONB. E05 makes them real: an org tenant on the `enterprise` plan gets
`rbac: true, sso: true, audit_export: true`; a Free personal tenant gets
`false` and the corresponding endpoints return `403`
(`capability_unavailable` per the entitlements-v1 error vocabulary).

## 3. The market bar (program market research, verified 2026-07-25)

Cited as fixed anchors; do not extrapolate beyond these three sources.

- **n8n enterprise** (lowcode.agency/blog/n8n-enterprise): SAML 2.0 SSO
  (Okta/Azure AD), RBAC with viewer/editor/admin roles at workflow+folder
  level, audit logs covering every login, creation, credential change, and
  execution, and air-gapped deployments.
- **Dify enterprise** (gumloop.com/blog/dify-alternatives): SCIM/SAML,
  RBAC, admin dashboard, audit logs, custom data-retention rules, AI model
  access control, VPC deployment.
- **Enterprise evaluation checklist** (ivern.ai/blog/ai-agent-platform-enterprise-comparison):
  SOC 2, GDPR, HIPAA, ISO 27001 certifications plus RBAC/SSO/audit as the
  table-stakes feature triad.

Consequences for scope:

- **In scope this wave**: SAML 2.0 + OIDC SSO, RBAC, audit events for every
  auth/role/policy mutation with an export endpoint, air-gapped operation
  (n8n parity); SCIM 2.0 provisioning/deprovisioning and IdP group→role
  mapping (Dify parity; SCIM phase-able to Users-only —
  [approaches.md](approaches.md) decision E).
- **Explicitly out of scope** (later functions in the program README build
  map): SIEM streaming (item 10 follow-on), model/capability policy engine
  (item 7 — Dify's "AI model access control"), budgets (item 5),
  data-retention rules (item 8), compliance artifacts (item 11).

## 4. Tenancy analysis (fixed requirement 5)

The question: does a user have exactly one personal tenant XOR org
memberships, or both?

Facts that constrain the answer:

- `docs/enterprise-extension.md` (§2.2 quote above) fixes personal tenants
  for Cloud Free/Pro and organizations for Enterprise; it does not forbid a
  user from having both.
- E01 auto-creates a personal tenant on first principal sighting; deleting
  that behavior for org users would fork the bootstrap path and break
  E01's claim flow (claim maps device → user → *personal* tenant today).
- E02's data is keyed tenant → user → device. A device claimed into a
  personal tenant reports usage under it; if the same human joins an org,
  should past personal usage become org-visible? **No** — org visibility
  must start at org membership and cover only org-tenant data.
- Multi-org membership (a consultant working for two firms) is real but
  rare at our stage; it costs an active-org context switcher in every UI
  and an `X-Org-Id` disambiguation header on every admin call.

Resolution (argued fully in [approaches.md](approaches.md) decision C):
**every user always owns one personal tenant** (E01 unchanged); **org
membership is additive, at most 1 in v1**, enforced by a partial unique
index that is *dropped, not re-modeled* when multi-org arrives; org context
is explicit in the session. Devices are claimed into exactly one tenant
(personal or org) — an org-claimed device's usage is org-visible, a
personal device's is not, which cleanly answers the visibility question.

## 5. Config-file vs DB split (fixed requirement 2)

The owner requires roles/permissions declarable in a versioned config file.
The tension: role *membership* changes daily (people join/leave) and must
not require a config deploy; role *shape* changes rarely and benefits from
git review. The split that resolves it:

| Concern | Lives in | Why |
|---|---|---|
| Auth backend wiring (issuer URLs, LDAP DN templates, gRPC address) | `auth.yaml` | deployment property; differs per install; secrets referenced via env/file indirection, never inline |
| Role definitions (name → permission list) | `auth.yaml` | versionable, reviewable, diffable; identical across replicas; built-ins compiled in and non-overridable-downward |
| Static role bindings (email → roles) | `auth.yaml` (optional) | bootstrap + air-gapped installs with no IdP; small, ops-owned |
| IdP group → role mappings | `auth.yaml` | property of the IdP integration, same lifecycle as backend config |
| Per-user role assignments | PostgreSQL `user_role_assignments` | dynamic, audited (who granted, when), changed from the members UI/API without redeploy |
| Membership + lifecycle state | PostgreSQL `org_memberships` | dynamic; SCIM writes it |

Effective roles = union(file static bindings, DB assignments, group
mappings evaluated at login). Precedence and conflict rules are pinned in
[architecture.md](architecture.md) § "Principal resolution"; the full
tradeoff discussion (including the rejected all-DB and all-file options and
the HA reload story) is [approaches.md](approaches.md) decision A.

## 6. SCIM lifecycle mapping

Fixed requirement 1 (org-created accounts, deprovisioning) plus the Dify
anchor make SCIM in-scope, minimal (Users endpoint only, decision E). SCIM
operations map onto the *same* internal lifecycle as email invites — one
state machine, two front doors:

| SCIM 2.0 operation | Internal lifecycle transition |
|---|---|
| `POST /scim/v2/Users` (`active: true`) | create user + membership directly in `active` (IdP-provisioned; no invite email; sign-in via the SSO backend) |
| `POST /scim/v2/Users` (`active: false`) | create in `provisioned` (pre-staged; activates on first SSO login or SCIM patch) |
| `PATCH /scim/v2/Users/{id}` set `active: false` | `active → suspended` (sessions revoked; sign-in refused) |
| `PATCH` set `active: true` | `suspended → active` |
| `DELETE /scim/v2/Users/{id}` | `→ deprovisioned` (terminal: sessions + device claims revoked, membership tombstoned, audit row; user row retained for audit integrity) |
| `GET /scim/v2/Users[?filter=userName eq …]` | read projection of membership + lifecycle |

The email-invite path uses the same states: `invited → active` (on accept),
then the same suspend/deprovision transitions via
`PATCH/DELETE /orgs/v1/members/*`. The lifecycle table and DDL are in
[architecture.md](architecture.md) § "Data model".

## 7. Risks

1. **`auth.yaml` drift across HA replicas.** Two replicas loading different
   file versions enforce different permissions. Mitigations: the loader
   computes a SHA-256 `config_hash` exposed in `/readyz` and logged at
   load; replicas are expected to mount one shared/identical file
   (configmap or synced volume); every reload writes an
   `authcfg.reloaded` audit row carrying the hash, so drift between
   replicas is loudly visible in one query. Accepted residual: a brief
   window during rolling reload. See [approaches.md](approaches.md)
   decision A (reload design).
2. **Role explosion.** Free-form custom roles in `auth.yaml` can sprawl
   into dozens of near-duplicates. Mitigations: four built-in roles cover
   the fixed requirements; the validator warns above a role-count threshold
   (default 25) and rejects unknown permission strings outright (closed
   catalog — Phase 0 freeze).
3. **Lockout.** A wrong `auth.yaml` (e.g. no one holds `org.manage`), a
   dead IdP, or an expired cert can lock every admin out — and E05 deletes
   `ADMIN_TOKEN`, the previous escape hatch. Mitigation: **break-glass is a
   local CLI, not a network credential** — `enterprise-api break-glass`
   runs on the host with direct DB access, mints a one-shot recovery
   session, and writes a loud audit event. Full procedure in
   [architecture.md](architecture.md) § "Break-glass". The validator also
   refuses to load a config under which zero principals can reach
   `org.manage` for an existing org (static analysis over bindings is
   best-effort; the CLI is the guarantee).
4. **Invitation token leakage.** Invite links grant account creation.
   Mitigations: single-use, hashed at rest (same discipline as all tokens),
   default 7-day expiry, bound to the invited email, revocable, audited.
5. **Suspension latency.** In principle an access token stays valid until
   TTL after suspension. In this design tokens are opaque and every request
   joins the server-side session row, so suspend/deprovision revokes
   sessions and cuts access on the *next request*; the access-token TTL
   (15 min default) is only the worst-case bound if a validation cache is
   ever introduced. Pinned as an evidence line in
   [validation.md](validation.md).
6. **Cross-repo timing with E03.** E03's plan creates
   `src/src/features/system/EnterpriseSection.tsx`
   ([E03 implementation.md](../E03_fleet_management/implementation.md)
   step 10); E05 lands before E03 in program order and also needs a home
   for the login stub. Resolution: **E05 creates the file** with the
   sign-in slice; E03 extends it with device status (its step 10 becomes
   "extend, not create"). Coordinated in
   [implementation.md](implementation.md) Phase 10 — don't duplicate.

## 8. Open questions

1. **Custom gRPC auth-service protocol final shape.** E01 marked the proto
   "verify"; `docs/enterprise-extension.md` names the seam but no message
   schema exists in `docs/contracts/`. E05 Phase 8 defines a minimal
   `VerifyCredentials`/`ResolvePrincipal` proto in the enterprise repo and
   proposes publishing it as `docs/contracts/enterprise-auth-grpc-v1.md`
   once a real external service exists to test against. Until then the
   backend ships behind a config flag with a fake-server test double.
   **Open** — needs the owner of the external auth service.
2. **MFA for the local backend.** Recommendation: delegate MFA to the IdP
   (OIDC/SAML deployments inherit it for free) and defer built-in TOTP for
   local accounts to a fast-follow ([approaches.md](approaches.md)
   decision F). Open until the first customer requires TOTP on an
   air-gapped local-accounts install.
3. **SAML library.** Go SAML implementations vary in quality; the candidate
   is crewjam/saml ("verify" at Phase 8 — audit status, XML-signature CVE
   history, maintenance). Not load-bearing for the plan: the backend
   interface isolates the choice.
4. **Multi-org activation.** The v1 single-org constraint is one partial
   unique index; the open product question is the *UX* for org context
   switching when it drops (session-pinned active org vs per-request
   header). The schema is ready either way (decision C).
5. **Platform-operator surface on managed cloud.** `platform.operate`
   (cross-tenant endpoints like `GET /admin/v1/tenants`) is bound via
   `auth.yaml` static bindings in v1. Whether managed-cloud operators get a
   separate internal IdP integration is an ops decision outside this plan.
