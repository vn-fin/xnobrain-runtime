# E05 — Approaches

Six decisions with options and tradeoffs. Cross-links:
[README.md](README.md), [findings.md](findings.md),
[architecture.md](architecture.md), [implementation.md](implementation.md),
[validation.md](validation.md).

## A. Role shapes: config file vs database

**Chosen: role *shapes* (name → permission list), backend wiring, static
bindings, and group mappings in versioned `auth.yaml`; per-user role
*assignments* in PostgreSQL (`user_role_assignments`).**

### Option A1 — Everything in the DB (rejected)

Roles, permissions, and grants all in tables, edited through an admin UI.

- Pro: one source of truth; no reload problem; no replica drift.
- Con: violates fixed requirement 2 verbatim — the owner requires that
  "services and roles can be declared in a config file … without touching
  a database console". Also loses git review/diff/rollback of permission
  shapes, and makes air-gapped bootstrap awkward (you need a working admin
  session to define the roles that create admin sessions).

### Option A2 — Everything in the file (rejected)

Grants too: `user@example.com: [org_manager]` lines for every member.

- Pro: fully declarative; GitOps-pure.
- Con: membership churns daily; every hire/departure becomes a config
  deploy; SCIM (fixed-requirement deprovisioning) would have to *write the
  file*, which is operationally absurd across replicas; grants lose the
  who-granted-when audit trail the market bar demands (n8n: audit logs of
  every credential/role change — program market research).

### Option A3 — Shapes in file, assignments in DB (chosen)

- Pro: shapes are versionable, reviewable, identical across replicas, and
  reviewable offline (air-gapped); assignments are dynamic, individually
  audited rows (`granted_by`, `granted_at`), and SCIM/invite flows write
  plain SQL. Static `bindings` in the file cover bootstrap and pure-GitOps
  installs. Group mappings stay with the backend config they belong to.
- Con: two places contribute to effective roles — resolved by a pinned
  union rule (architecture.md §4 step [4]) and by validation: unknown
  role names in DB grants simply stop resolving when removed from the
  file (fail-closed, logged), never error.

**HA reload:** SIGHUP + `POST /admin/v1/authcfg/reload`
(`policies.write`); parse → validate → atomic pointer swap; invalid file ⇒
keep previous config + error. Each replica reloads independently;
`config_hash` in `/readyz` + an `authcfg.reloaded` audit row per reload
make replica drift observable ([findings.md](findings.md) risk 1). Chosen
over fsnotify auto-watch (silent half-rollouts) and over restart-only
(role edits must take effect without downtime — pinned as a
[validation.md](validation.md) evidence line).

## B. `org_admin` vs `org_manager`

**Chosen: split roles. `org_admin` = management permissions
(members/roles/policies/org settings/devices/SCIM). `org_manager` =
read-everything (usage, devices, members list, audit). Neither implies the
other; one user may hold both (the org creator gets both by default).**

### Option B1 — admin ⊇ manager (one superrole) (rejected)

- Pro: simpler mental model; fewer grants to manage.
- Con: violates least privilege in the direction enterprises actually care
  about: the person who *administers* accounts (IT) is often not the
  person entitled to *see* everyone's usage and audit trail (a team lead /
  finance), and vice versa. A compliance reviewer wants read-all without
  the ability to quietly grant themselves more; a helpdesk admin wants
  member management without usage visibility. Folding them together makes
  both grants maximal.

### Option B2 — split, admin can also hold manager (chosen)

- Pro: least-privilege oversight (fixed requirement 4 asks for a *view-all*
  role, not a superuser); clean audit story ("who could read org-wide
  usage" is one role query); trivially recovers B1's convenience — grant
  both roles to the same person, as the org-creation flow does.
- Con: an org_admin can grant themselves org_manager via `roles.assign`.
  Accepted: that grant is an audited `role.assigned` event — escalation is
  possible but visible, which is the enterprise-acceptable posture (the
  alternative, admin-cannot-self-grant, creates unrecoverable orgs).

`member` (self-scope) and `auditor` (audit read/export only) complete the
built-in set; catalog in [architecture.md](architecture.md) §3.

## C. Tenancy v1 (fixed requirement 5)

**Chosen: every user always owns a personal tenant (E01 bootstrap
unchanged); org membership is additive with a v1 cap of one
non-deprovisioned org per user, enforced by a single partial unique index;
schema is N-ready.**

### Option C1 — XOR: personal tenant OR org membership (rejected)

- Pro: simplest visibility story.
- Con: breaks E01's already-built claim flow (device → user → personal
  tenant) for org users; forces a destructive migration when an individual
  Pro user's company later buys Enterprise (their personal tenant would
  have to be dissolved or orphaned); contradicts the smooth
  standalone→org upgrade path the managed cloud needs. Also inconsistent
  with `docs/enterprise-extension.md`, which describes personal tenants
  and organizations as coexisting models, not alternatives.

### Option C2 — full multi-org now (rejected for v1, honest cost)

- Pro: consultants/agencies served day one; no later index drop.
- Cost if built now: an active-org switcher in every UI; `X-Org-Id`
  disambiguation on every admin call and in the OSS session surface;
  per-org session context or multi-context sessions; org-qualified
  invitation-acceptance conflicts ("you're already in another org" UX);
  test matrix roughly doubles across authz, SCIM, and dashboards. None of
  the fixed requirements needs it, and no current buyer does.

### Option C3 — personal always + ≤1 org, N-ready schema (chosen)

- Pro: consistent with `docs/plans.md` ("Cloud Free and Pro each resolve
  one authenticated member in one personal tenant… Enterprise adds
  organizations, multiple members" — via `docs/enterprise-extension.md`);
  zero migration risk later — multi-org = `DROP INDEX
  idx_membership_single_org` plus product/UX work
  ([findings.md](findings.md) §8.4), no table changes;
  `user_sessions.org_id` already models active-context switching.
- Con: consultants must pick one org in v1. Accepted and documented.

Data visibility corollary: a device is claimed into exactly one tenant;
org managers see org-tenant data only — a member's personal-tenant devices
and usage are invisible to the org ([findings.md](findings.md) §4).

## D. Session token format: opaque server-side vs JWT

**Chosen: opaque prefixed tokens (`b4e_at_`, `b4e_rt_`), 32 random bytes
base64url, only `sha256(raw)` at rest in `user_sessions` — exactly E01's
device-token discipline (E01 approaches decision A).**

### Option D1 — JWT access tokens (rejected)

- Pro: stateless validation; no DB hit per request; standard libraries.
- Con: **revocation complexity is the killer** — suspension and
  deprovisioning must take effect fast (fixed requirement 1's
  deprovisioning; market bar audit posture), which forces a server-side
  denylist that re-introduces the DB hit JWTs were meant to avoid, plus
  key-rotation machinery, `alg` pitfalls, and claims that go stale against
  live role edits. Our scale (org admin surfaces, not per-message hot
  path) doesn't need stateless validation.

### Option D2 — opaque + hashed (chosen)

- Pro: revocation = one UPDATE, effective next request; zero cryptographic
  surface beyond `crypto/rand` + SHA-256; identical code, tests, and
  never-log tooling as E01 device tokens; roles/groups always evaluated
  fresh per request (hot role edits work — a validation.md line).
- Con: DB lookup per request (one indexed point read; acceptable), and
  sessions are Postgres-bound (fine: Postgres is already the control
  plane's source of truth).

Refresh rotation with reuse-detection (architecture.md §5.4) matches E01's
"revoke previous on mint".

## E. SCIM: this wave vs later

**Chosen: minimal SCIM 2.0 Users endpoint in this wave (Phase 9); Groups,
bulk, and rich filtering later.**

### Option E1 — defer SCIM entirely (rejected)

- Pro: smaller wave.
- Con: fixed requirement 1 names SCIM import explicitly, and automated
  *deprovisioning* is the half of SCIM enterprises actually buy (offboard
  in the IdP ⇒ access dies everywhere). The market bar lists SCIM at Dify
  (program market research); shipping org accounts without it invites
  manual-offboarding audit findings at the first serious customer.

### Option E2 — Users-only now (chosen)

- Pro: covers create/suspend/deprovision — the whole lifecycle
  ([findings.md](findings.md) §6) — with four routes and one token table;
  reuses the invite lifecycle state machine rather than adding one; Okta
  and Entra provisioning both work in Users-only mode.
- Con: role assignment via IdP groups uses SAML/OIDC group claims (already
  in scope) rather than SCIM Groups sync; acceptable until Phase 2 of
  program build-map item 3.

### Option E3 — full SCIM incl. Groups (rejected for now)

Groups sync duplicates the group→role mapping path and roughly doubles
SCIM surface/testing for no fixed requirement.

## F. Local-account MFA

**Chosen: delegate MFA to the IdP for OIDC/SAML/external backends (do
nothing — the IdP enforces it before we ever see the assertion); no
built-in MFA for the local backend this wave. TOTP for local accounts is a
named fast-follow behind a `password_policy.totp: required` config key
reserved now in the `auth.yaml` schema.**

### Option F1 — build TOTP now (rejected)

- Pro: air-gapped local-account installs get MFA day one.
- Con: enrollment UX, recovery codes, drift windows, and reset flows are a
  wave of their own; every SSO deployment gets it free from the IdP
  anyway; no fixed requirement or market anchor mandates built-in MFA
  (n8n/Dify anchors list SSO/SAML — MFA rides the IdP).

### Option F2 — delegate + reserved config key (chosen)

- Pro: ships the wave; the `auth.yaml` schema won't break when TOTP lands
  (key reserved, validator accepts-and-warns "not yet implemented");
  air-gapped installs can meanwhile pair the local backend with LDAP/IdP
  appliances that enforce MFA in-network.
- Con: local-accounts-only installs have password-only auth until the
  fast-follow; mitigated by argon2id + lockout backoff + short sessions.
  Tracked as [findings.md](findings.md) open question 2.
