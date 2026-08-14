# E05 — Architecture

Data model, the annotated `auth.yaml`, the permission catalog, principal
resolution, login sequences, SCIM slice, endpoint table, deployment
topologies, and break-glass. Cross-links: [README.md](README.md),
[findings.md](findings.md), [approaches.md](approaches.md),
[implementation.md](implementation.md), [validation.md](validation.md).

Everything server-side extends the `xnobrain-enterprise` repo laid out in
[E01 architecture.md](../E01_control_plane_foundation/architecture.md); new
packages are listed in [implementation.md](implementation.md).

## 1. Data model (migration `00xx_orgs_auth_rbac`)

Plain SQL, PostgreSQL 16, no ORM, extending — never rewriting — E01's
`0001_init` (and E02's usage migration if already merged; take the next
free number). ID prefixes via `internal/ids`: `org_`, `inv_`, `ses_`,
`ras_`, `idn_`.

```sql
-- 00xx_orgs_auth_rbac.up.sql

-- One org per organization tenant. The tenant row (kind='organization')
-- carries plan assignment (E01 plan_assignments.tenant_id); the org row
-- carries identity/settings surfaced to APIs.
CREATE TABLE orgs (
    id          text PRIMARY KEY,                    -- org_01…
    tenant_id   text NOT NULL UNIQUE REFERENCES tenants(id),
    name        text NOT NULL,
    settings    jsonb NOT NULL DEFAULT '{}'::jsonb,  -- e.g. {"self_signup": false}
                                                     -- self_signup is false and
                                                     -- immutable in v1 (fixed req 1)
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- Membership + account lifecycle. One state machine for invites and SCIM
-- (findings.md §6): invited → provisioned → active → suspended
--                                → deprovisioned (terminal)
CREATE TABLE org_memberships (
    id          text PRIMARY KEY,                    -- mem_01…
    org_id      text NOT NULL REFERENCES orgs(id),
    user_id     text NOT NULL REFERENCES users(id),
    status      text NOT NULL DEFAULT 'active'
                CHECK (status IN ('invited','provisioned','active',
                                  'suspended','deprovisioned')),
    scim_external_id text NOT NULL DEFAULT '',       -- IdP's externalId, if SCIM
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (org_id, user_id)
);
-- v1 tenancy rule (approaches.md C): at most ONE non-deprovisioned org
-- membership per user. Multi-org later = drop this index, nothing else.
CREATE UNIQUE INDEX idx_membership_single_org ON org_memberships (user_id)
    WHERE status <> 'deprovisioned';
CREATE INDEX idx_membership_org ON org_memberships (org_id, status);

-- Email invitations (fixed req 1). Token hashed at rest like every token.
CREATE TABLE invitations (
    id          text PRIMARY KEY,                    -- inv_01…
    org_id      text NOT NULL REFERENCES orgs(id),
    email       text NOT NULL,
    roles       text[] NOT NULL DEFAULT '{member}',  -- roles granted on accept;
                                                     -- validated against the
                                                     -- catalog at insert time
    token_hash  bytea NOT NULL UNIQUE,               -- sha256(raw invite token)
    expires_at  timestamptz NOT NULL,                -- default now()+7d
    created_by  text NOT NULL REFERENCES users(id),
    accepted_at timestamptz,
    revoked_at  timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (org_id, email)                           -- one live invite per address
);

-- Dynamic role assignments (file defines role SHAPES; this table holds
-- per-user GRANTS — findings.md §5, approaches.md A). One row per
-- (user, org, role): grants are individually auditable and revocable.
CREATE TABLE user_role_assignments (
    id          text PRIMARY KEY,                    -- ras_01…
    user_id     text NOT NULL REFERENCES users(id),
    org_id      text NOT NULL REFERENCES orgs(id),
    role        text NOT NULL,                       -- must exist in loaded auth.yaml
                                                     -- or be a built-in; validated
                                                     -- on write AND on read (a role
                                                     -- deleted from the file simply
                                                     -- stops resolving)
    granted_by  text NOT NULL,                       -- usr_… | 'scim' | 'breakglass'
    granted_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, org_id, role)
);
CREATE INDEX idx_ras_org ON user_role_assignments (org_id);

-- Which backend authenticates which user. A user has ≥1 identity;
-- backend_id references auth.yaml backends by their stable id.
CREATE TABLE auth_identities (
    id           text PRIMARY KEY,                   -- idn_01…
    user_id      text NOT NULL REFERENCES users(id),
    backend_id   text NOT NULL,                      -- 'local' | 'corp-oidc' | …
    external_sub text NOT NULL,                      -- OIDC sub / SAML NameID /
                                                     -- LDAP DN / gRPC subject /
                                                     -- email for local
    created_at   timestamptz NOT NULL DEFAULT now(),
    last_login_at timestamptz,
    UNIQUE (backend_id, external_sub)
);

-- Local-account credentials (backend 'local' only). argon2id.
CREATE TABLE local_credentials (
    user_id       text PRIMARY KEY REFERENCES users(id),
    password_hash text NOT NULL,     -- argon2id PHC string ($argon2id$v=19$…);
                                     -- params in the string ⇒ rehash-on-login
                                     -- when policy strengthens
    updated_at    timestamptz NOT NULL DEFAULT now(),
    failed_attempts int NOT NULL DEFAULT 0,
    locked_until  timestamptz                        -- simple backoff lockout
);

-- User sessions: short-lived access + longer refresh, both opaque and
-- hashed at rest (mirrors E01 device_tokens; approaches.md D).
CREATE TABLE user_sessions (
    id                 text PRIMARY KEY,             -- ses_01…
    user_id            text NOT NULL REFERENCES users(id),
    org_id             text REFERENCES orgs(id),     -- active org context; NULL =
                                                     -- personal-tenant context
    backend_id         text NOT NULL,                -- which backend logged in
    access_token_hash  bytea NOT NULL UNIQUE,        -- sha256(raw b4e_at_…)
    refresh_token_hash bytea NOT NULL UNIQUE,        -- sha256(raw b4e_rt_…)
    access_expires_at  timestamptz NOT NULL,         -- default now()+15m
    refresh_expires_at timestamptz NOT NULL,         -- default now()+30d
    idp_groups         text[] NOT NULL DEFAULT '{}', -- groups captured at login;
                                                     -- re-evaluated per request
                                                     -- against auth.yaml mappings
    created_at         timestamptz NOT NULL DEFAULT now(),
    last_used_at       timestamptz,
    revoked_at         timestamptz
);
CREATE INDEX idx_sessions_user ON user_sessions (user_id) WHERE revoked_at IS NULL;

-- SCIM bearer tokens (per-org provisioning credentials), hashed at rest.
CREATE TABLE scim_tokens (
    id          text PRIMARY KEY,                    -- sct_01…
    org_id      text NOT NULL REFERENCES orgs(id),
    token_hash  bytea NOT NULL UNIQUE,
    created_by  text NOT NULL REFERENCES users(id),
    created_at  timestamptz NOT NULL DEFAULT now(),
    revoked_at  timestamptz
);

-- Audit extension: org scoping + request correlation. actor_type/actor_id/
-- tenant_id/details already exist (E01). Append-only trigger untouched.
ALTER TABLE audit_events ADD COLUMN org_id text;
ALTER TABLE audit_events ADD COLUMN request_id text NOT NULL DEFAULT '';
CREATE INDEX idx_audit_org ON audit_events (org_id, occurred_at)
    WHERE org_id IS NOT NULL;
```

`00xx_orgs_auth_rbac.down.sql` reverses in order (dev hygiene only;
production rolls forward — E01 convention). Personal-tenant bootstrap needs
**no schema change**: E01's lazy user+tenant creation stands; org
membership is purely additive (fixed requirement 5, approaches.md C).

### Account lifecycle

```
              (invite email)            (accept: set password / link IdP)
 org admin ──► invited ─────────────────► active ◄──── reactivate
 SCIM POST ──► provisioned ─► (first SSO login) ─┘        │ suspend
                                                          ▼
                                            suspended ── deprovision ──► deprovisioned
                                                                          (terminal:
                                                                           sessions +
                                                                           device claims
                                                                           revoked)
```

Every transition writes an `audit_events` row (action list in §7).

## 2. `auth.yaml` — the complete annotated example

Loaded at boot from `AUTH_CONFIG_PATH` (default
`/etc/xnobrain/auth.yaml`; the repo ships
`config/auth.yaml.example`). Hot-reload on SIGHUP +
`POST /admin/v1/authcfg/reload` (decision A): parse → validate → atomic
swap; an invalid file is rejected and the previous config stays live.
Secrets are **never inline** — `_env`/`_file` indirection only, so the
YAML is safe to commit.

```yaml
# auth.yaml — xnobrain-enterprise authentication & RBAC configuration.
# Versioned contract: bump `version` only on incompatible schema change.
version: 1

# ── Backends ─────────────────────────────────────────────────────────────
# Each backend has a stable `id` (referenced by auth_identities.backend_id)
# and a `type` ∈ local | oidc | saml | ldap | external_grpc.
# `enabled: false` keeps config in place without offering the login path.
backends:
  - id: local
    type: local                     # built-in accounts, argon2id (fixed req 2a)
    enabled: true
    password_policy:
      min_length: 12
      lockout_attempts: 10          # then exponential locked_until backoff
    # Air-gapped installs typically run ONLY this backend (or ldap):
    # zero external egress (validation.md §6).

  - id: corp-oidc
    type: oidc                      # (2b) any OIDC IdP: Okta, Entra, Keycloak…
    enabled: true
    issuer: https://idp.example.com/realms/acme
    client_id: xnobrain-enterprise
    client_secret_env: OIDC_CLIENT_SECRET      # env indirection, never inline
    scopes: [openid, email, profile, groups]
    groups_claim: groups            # claim carrying IdP group names
    redirect_url: https://cp.example.com/auth/v1/oidc/callback

  - id: corp-saml
    type: saml                      # (2c) SAML 2.0 SP-initiated
    enabled: false
    idp_metadata_file: /etc/xnobrain/idp-metadata.xml   # file indirection —
                                                          # works air-gapped
    sp_entity_id: https://cp.example.com/auth/v1/saml/metadata
    acs_url: https://cp.example.com/auth/v1/saml/acs
    groups_attribute: memberOf

  - id: corp-ldap
    type: ldap                      # (2d) simple-bind against a directory
    enabled: false
    url: ldaps://ldap.example.com:636
    bind_dn_template: "uid={username},ou=people,dc=example,dc=com"
    # optional service account for group lookup:
    search_bind_dn: "cn=svc-xnobrain,ou=svc,dc=example,dc=com"
    search_bind_password_file: /etc/xnobrain/secrets/ldap-pass
    group_search_base: "ou=groups,dc=example,dc=com"

  - id: acme-authsvc
    type: external_grpc             # (2e) the docs/enterprise-extension.md seam:
    enabled: false                  # "principal resolution should call the
    address: authsvc.internal:8443  #  existing external auth service over gRPC"
    tls_ca_file: /etc/xnobrain/secrets/authsvc-ca.pem
    # Proto shape is an open question (findings.md §8.1); the backend is a
    # config-flagged plug behind the same Backend interface.

# ── Roles ────────────────────────────────────────────────────────────────
# Role SHAPES live here (versioned, reviewable); per-user role GRANTS live
# in PostgreSQL user_role_assignments (findings.md §5).
# The four built-ins below are COMPILED IN with exactly these permission
# sets; restating them here is optional documentation. A file may ADD
# custom roles but may not weaken a built-in (validator rejects a built-in
# name with a different permission list). Permission strings must exist in
# the frozen catalog (§3) — unknown strings fail validation.
roles:
  org_admin:                        # manage, don't necessarily see-all (decision B)
    description: Manage members, roles, invitations, org settings, devices.
    permissions:
      - org.read
      - org.manage
      - members.read
      - members.invite
      - members.manage
      - roles.read
      - roles.assign
      - policies.read
      - policies.write
      - devices.manage
      - scim.manage
  org_manager:                      # read-EVERYTHING in the org (fixed req 4)
    description: Read-only oversight of all members' usage, devices, audit.
    permissions:
      - org.read
      - members.read
      - roles.read
      - policies.read
      - usage.read_org
      - usage.export
      - devices.read_org
      - audit.read
  member:                           # self-scope only
    description: Regular org member.
    permissions:
      - org.read
      - usage.read_self
      - devices.read_self
  auditor:                          # audit-trail reader
    description: Reads and exports the org audit log; nothing else.
    permissions:
      - org.read
      - audit.read
      - audit.export
  # Custom role example (additive):
  billing-analyst:
    description: Exports usage for chargeback.
    permissions: [org.read, usage.read_org, usage.export]

# ── Static bindings (optional) ───────────────────────────────────────────
# Bootstrap + air-gapped installs: bind roles to identities without any DB
# write. Evaluated by email (matched against the verified login identity).
# platform.operate holders (managed-cloud / self-host operators) are bound
# here in v1 (findings.md §8.5).
bindings:
  - email: root-admin@example.com
    roles: [org_admin, org_manager]
  - email: ops@xnobrain.example
    roles: [platform_operator]      # built-in: the only role with platform.operate

# ── IdP group → role mappings ────────────────────────────────────────────
# Evaluated per request from the session's captured idp_groups. Removing a
# user from the IdP group removes the role at next token use — no deploy,
# no DB write.
group_mappings:
  - backend: corp-oidc
    group: xnobrain-admins
    roles: [org_admin]
  - backend: corp-oidc
    group: xnobrain-managers
    roles: [org_manager]
  - backend: corp-oidc
    group: everyone
    roles: [member]
  - backend: corp-saml
    group: CN=B4A-Auditors,OU=groups,DC=example,DC=com
    roles: [auditor]

# ── Sessions ─────────────────────────────────────────────────────────────
session:
  access_ttl: 15m                   # opaque b4e_at_…, hashed at rest
  refresh_ttl: 720h                 # 30d sliding; rotation on every refresh
```

## 3. Permission catalog (frozen in Phase 0)

`resource.verb` strings; the closed set for this wave. Unknown strings are
a validation error (findings.md §7.2). Later plans extend the catalog by a
reviewed addition to `internal/authz/catalog.go` + this table — never
ad hoc.

| Permission | Grants | Built-in roles holding it | Used by (endpoints) |
|---|---|---|---|
| `org.read` | see org name/settings/own membership | all four | `GET /orgs/v1/current` |
| `org.manage` | change org settings | org_admin | `PATCH /orgs/v1/current` |
| `members.read` | list members + lifecycle status | org_admin, org_manager | `GET /orgs/v1/members` |
| `members.invite` | create/revoke invitations, CSV import | org_admin | `POST /orgs/v1/invitations`, `POST /orgs/v1/members/import` |
| `members.manage` | suspend/reactivate/deprovision, revoke sessions | org_admin | `PATCH/DELETE /orgs/v1/members/{id}` |
| `roles.read` | see role definitions + assignments | org_admin, org_manager | `GET /orgs/v1/roles` |
| `roles.assign` | grant/revoke role assignments | org_admin | `PUT /orgs/v1/members/{id}/roles` |
| `policies.read` | read loaded auth config summary (no secrets) | org_admin, org_manager | `GET /admin/v1/authcfg` |
| `policies.write` | trigger config reload | org_admin | `POST /admin/v1/authcfg/reload` |
| `usage.read_self` | own usage only (E02, `user=self` forced) | member | `GET /admin/v1/usage` (self-scoped) |
| `usage.read_org` | all members' usage in the org | org_manager | `GET /admin/v1/usage` (org-scoped) |
| `usage.export` | CSV export | org_manager | `GET /admin/v1/usage/export.csv` |
| `devices.read_self` | own devices | member | `GET /admin/v1/devices` (self-scoped) |
| `devices.read_org` | all org devices | org_manager | `GET /admin/v1/devices` (org-scoped) |
| `devices.manage` | revoke/rename org devices (E03 lifecycle later) | org_admin | `POST /device/v1/revoke` (admin variant) |
| `audit.read` | read org audit trail | org_manager, auditor | `GET /orgs/v1/audit` |
| `audit.export` | export org audit trail | auditor | `GET /orgs/v1/audit/export` |
| `scim.manage` | mint/revoke SCIM tokens | org_admin | `POST/DELETE /orgs/v1/scim-tokens` |
| `platform.operate` | cross-tenant operator surface | platform_operator (bindings only) | `GET /admin/v1/tenants`, cross-org variants |

Reserved (defined now, no endpoint this wave, so E03/E04 are born gated):
`voice.read_org` (E04 metering), `fleet.manage` (E03 lifecycle).

## 4. Principal resolution (middleware flow)

`internal/authz/middleware.go`, wrapping every `/admin/v1/*`, `/orgs/v1/*`,
and the user-auth side of `/device/v1/claim` and
`/entitlements/v1/current`. Tenant identity flows into every downstream
call per `docs/enterprise-extension.md` ("policy, repositories, storage
prefixes, telemetry, and quota reservations").

```
 request
   │  Authorization: Bearer b4e_at_…
   ▼
 [1] token → session
   sha256(raw) → user_sessions lookup
   revoked_at IS NULL AND access_expires_at > now()?  ──no──► 401 invalid_token
   │
   ▼
 [2] session → user + lifecycle
   users row + org_memberships (session.org_id)
   membership status = 'active'?  ──no (suspended/deprovisioned)──► 403 account_suspended
   │                                   (+ session revoked on the spot)
   ▼
 [3] org context
   principal = {user_id, tenant_id(org or personal), org_id|∅, backend_id}
   │
   ▼
 [4] effective roles  =  file bindings (auth.yaml bindings by email)
                       ⊕ DB assignments (user_role_assignments for org_id)
                       ⊕ group mappings (session.idp_groups × auth.yaml
                                          group_mappings for backend_id)
   ▼
 [5] effective permissions = ∪ permissions(role) over effective roles
        (roles resolved against the CURRENT loaded auth.yaml — a role
         removed from the file silently contributes nothing)
   ▼
 [6] gate: required permission ∈ effective set?
      ├─ yes ─► handler runs with principal in ctx; repository queries are
      │         forced to principal.org scope (org_manager) or user scope
      │         (member); telemetry span gets tenant-safe attrs
      └─ no ──► 403 permission_denied
   ▼
 [7] audit: every MUTATION (and every auth event) appends audit_events
     {actor_type:'user', actor_id, org_id, request_id, action, subject, details}
     — reads are not audited except audit.export itself
```

Deny responses use E01's error envelope with stable codes:
`401 invalid_token`, `403 permission_denied`, `403 account_suspended`,
`403 capability_unavailable` (plan lacks `rbac`/`sso`/`audit_export` —
`docs/plans.md`: unavailable paid features return 403).

## 5. Login sequences

### 5.1 Local accounts (argon2id)

```
 browser/OSS UI                      enterprise-api                PostgreSQL
      │ POST /auth/v1/login {email, password}
      ├──────────────────────────────────►│
      │                                   │ auth_identities(backend='local')
      │                                   │ local_credentials: locked_until?
      │                                   │ argon2id.Verify(hash, password)
      │                                   │   (constant-time; on param drift,
      │                                   │    rehash + update)
      │                                   │ membership status must be 'active'
      │                                   │ mint b4e_at_/b4e_rt_ (raw once);
      │                                   │ store sha256 in user_sessions
      │                                   │ audit: auth.login.success
      │◄──────────────────────────────────┤
      │ {access_token, refresh_token, access_expires_at, user, org, roles}
      │   failure → 401 invalid_credentials (same body for unknown email
      │   vs wrong password; failed_attempts++, lockout backoff);
      │   audit: auth.login.failure (email, backend, NO password material)
```

### 5.2 OIDC (authorization code; group capture)

```
 browser                 enterprise-api                       IdP (issuer)
    │ GET /auth/v1/oidc/start?backend=corp-oidc
    ├───────────────────────►│ state+nonce (server-side, TTL 10m)
    │◄── 302 to IdP authorize URL ────────┤
    ├─────────────────── authenticate (MFA = IdP's business, decision F) ──►│
    │◄── 302 /auth/v1/oidc/callback?code&state ─────────────────────────────┤
    ├───────────────────────►│ verify state; code→token exchange (backend
    │                        │ channel); verify id_token sig/iss/aud/nonce
    │                        │ identity: (backend_id, sub) → auth_identities
    │                        │   first sighting: JIT — user row + personal
    │                        │   tenant (E01 bootstrap); org membership only
    │                        │   if invited/provisioned/SCIM-created —
    │                        │   NO open org self-signup (fixed req 1)
    │                        │ capture groups claim → session.idp_groups
    │                        │ mint session pair; audit: auth.login.success
    │◄── tokens (fragment/redirect per client type) ──┤
```

SAML 2.0 is the same skeleton with SP-initiated AuthnRequest →
`POST /auth/v1/saml/acs` (assertion signature verified against
`idp_metadata_file`), `groups_attribute` → `idp_groups`. LDAP is
`POST /auth/v1/ldap/login` doing a simple bind with
`bind_dn_template` + optional group search — request/response identical to
local login.

### 5.3 Custom external auth service (gRPC seam)

```
 client                  enterprise-api                    org auth service
    │ POST /auth/v1/login {backend:"acme-authsvc", credentials:{…opaque…}}
    ├───────────────────────►│
    │                        │ gRPC VerifyCredentials(credentials)   ("verify"
    │                        ├──────────────────────────────────────► proto —
    │                        │◄─ {subject, email, display, groups[]} ┤ findings
    │                        │ (backend_id, subject) → auth_identities  §8.1)
    │                        │ same JIT + membership gate + session mint
    │                        │ as OIDC; groups[] → idp_groups
    │◄── tokens ─────────────┤
```

The gRPC call happens **only at login**; per-request validation is always
the local session row — an auth-service outage never breaks issued
sessions, only new logins.

### 5.4 Refresh / logout

- `POST /auth/v1/refresh {refresh_token}` → verify hash + TTL + revoked;
  re-check membership status (suspension caught here at the latest);
  rotate: new access+refresh pair, old refresh hash gets `revoked_at`
  (reuse of a rotated refresh token revokes the whole session — theft
  signal). Mirrors E01's "revoke previous on mint".
- `POST /auth/v1/logout` → `revoked_at` on the session; audit
  `auth.logout`.
- `members.manage` suspension → `UPDATE user_sessions SET revoked_at…
  WHERE user_id…`; next request fails at step [1].

## 6. SCIM 2.0 slice (Users only, decision E)

Auth: `Authorization: Bearer <scim token>` (per-org `scim_tokens` row,
hashed at rest, minted under `scim.manage`). Content type
`application/scim+json`. Lifecycle mapping table:
[findings.md](findings.md) §6.

| Method | Path | Effect |
|---|---|---|
| GET | `/scim/v2/ServiceProviderConfig` | static capabilities document (no Groups, no bulk, filter=userName only) |
| GET | `/scim/v2/Users?filter=userName eq "…"` | list/lookup projection |
| GET | `/scim/v2/Users/{id}` | single user projection |
| POST | `/scim/v2/Users` | create user + `auth_identities` (externalId→SSO backend) + membership (`active`/`provisioned`) |
| PATCH | `/scim/v2/Users/{id}` | `active` true/false → reactivate/suspend; name/email updates cached |
| PUT | `/scim/v2/Users/{id}` | full replace (same semantics as PATCH union) |
| DELETE | `/scim/v2/Users/{id}` | deprovision (terminal; sessions + device claims revoked) |

Groups endpoint, bulk operations, and arbitrary filters are **out of
scope** (Phase-2 of program build-map item 3). Every SCIM mutation writes
`audit_events` with `actor_type='system'`, `actor_id='scim:'+token_id`.

## 7. Endpoint table

New surfaces (all JSON, E01 error envelope):

| Method | Path | Auth / permission | Purpose |
|---|---|---|---|
| GET | `/auth/v1/backends` | none | enabled backends + types (drives login UI) |
| POST | `/auth/v1/login` | none | local / ldap / external_grpc credential login |
| GET | `/auth/v1/oidc/start` | none | begin OIDC code flow |
| GET | `/auth/v1/oidc/callback` | none | finish OIDC, mint session |
| GET | `/auth/v1/saml/metadata` | none | SP metadata XML |
| POST | `/auth/v1/saml/acs` | none | SAML assertion consumer, mint session |
| POST | `/auth/v1/refresh` | refresh token | rotate session pair |
| POST | `/auth/v1/logout` | access token | revoke session |
| GET | `/auth/v1/session` | access token | introspection: user, org, roles, effective permissions, capability flags (feeds OSS `/api/brain/v1/limits`) |
| POST | `/auth/v1/invitations/accept` | invite token | invited → active; local: set password; SSO: link identity |
| POST | `/orgs/v1` | authed user; cloud: plan gate `rbac` capability; self-hosted: first-boot bootstrap | create org (creator gets org_admin+org_manager) |
| GET | `/orgs/v1/current` | `org.read` | org profile + own membership/roles |
| PATCH | `/orgs/v1/current` | `org.manage` | settings |
| GET | `/orgs/v1/members` | `members.read` | members list view (name, email, status, roles, last_login) — the org-manager members list |
| POST | `/orgs/v1/invitations` | `members.invite` | invite by email (+roles) |
| GET | `/orgs/v1/invitations` | `members.invite` | pending invites |
| DELETE | `/orgs/v1/invitations/{id}` | `members.invite` | revoke invite |
| POST | `/orgs/v1/members/import` | `members.invite` | CSV import (email,roles per line → bulk invitations/provisioned rows) |
| PATCH | `/orgs/v1/members/{user_id}` | `members.manage` | suspend / reactivate |
| DELETE | `/orgs/v1/members/{user_id}` | `members.manage` | deprovision |
| PUT | `/orgs/v1/members/{user_id}/roles` | `roles.assign` | replace DB role grants |
| GET | `/orgs/v1/roles` | `roles.read` | loaded role shapes + assignment counts |
| GET | `/orgs/v1/audit?from&to&action&actor` | `audit.read` | org-scoped audit page |
| GET | `/orgs/v1/audit/export` | `audit.export` | JSONL/CSV export (the market-bar export endpoint) |
| POST | `/orgs/v1/scim-tokens` · DELETE `…/{id}` | `scim.manage` | SCIM credential lifecycle |
| GET | `/admin/v1/authcfg` | `policies.read` | loaded config summary + config_hash (no secrets) |
| POST | `/admin/v1/authcfg/reload` | `policies.write` | validate + hot-swap auth.yaml |
| * | `/scim/v2/*` | SCIM bearer | §6 |

Changed E01/E02 endpoints (the `ADMIN_TOKEN` retirement — full list also in
[findings.md](findings.md) §1.2, retrofit steps in
[implementation.md](implementation.md) Phase 5): `GET /admin/v1/devices`,
`GET /admin/v1/tenants`, `POST /device/v1/revoke` (admin variant),
`GET /admin/v1/usage`, `GET /admin/v1/usage/export.csv`. E02's usage
endpoint additionally gains server-forced scoping: `member` ⇒
`user=self`; `org_manager` ⇒ `tenant=own org tenant`; `platform.operate` ⇒
unrestricted.

Audit action vocabulary written by this plan: `auth.login.success`,
`auth.login.failure`, `auth.logout`, `auth.token.refresh`,
`auth.session.revoked`, `user.invited`, `user.invite_accepted`,
`user.imported`, `user.provisioned`, `user.suspended`, `user.reactivated`,
`user.deprovisioned`, `role.assigned`, `role.revoked`, `org.created`,
`org.updated`, `authcfg.reloaded`, `scim.token.created`,
`scim.token.revoked`, `breakglass.used`. Details JSONB never contains
passwords, hashes, tokens, or assertions (E01 logging discipline).

## 8. Deployment topologies (fixed requirement 3)

Same binary, same compose, same `auth.yaml` schema everywhere.

**Self-hosted (firm's servers):**

```
 firm network                                 ┌ optional, firm-owned ┐
┌───────────────────────────────────────┐     │  IdP (Okta/Keycloak) │
│ docker-compose: enterprise-api + pg16 │◄────┤  or LDAP directory   │
│  AUTH_CONFIG_PATH=/etc/…/auth.yaml    │     └──────────────────────┘
│  bindings bootstrap the first admin   │
└───────────────────────────────────────┘
 members' devices push usage outbound-only (E02) from inside the network
```

**Air-gapped:** backends `local` (and/or `ldap` against an in-network
directory) only; `oidc/saml/external_grpc` disabled or pointing at
in-network services; no external egress at all — the binary makes **zero**
outbound calls of its own (no telemetry phone-home, no CDN, invite email
optional/disabled: admin hands the invite link out-of-band). Verified by
network capture in [validation.md](validation.md) §6. First admin comes
from `bindings` + local password set via the break-glass CLI or seeded
invite.

**Managed cloud:** we operate the same compose/K8s deployment; standalone
users self-signup into personal tenants (Cloud Free/Pro — `docs/plans.md`);
orgs are created on the `enterprise` plan (capability-gated); operators
hold `platform.operate` via bindings. Org self_signup stays false — org
accounts arrive by invite/SCIM only (fixed req 1).

## 9. Break-glass (lockout recovery)

Scenario: IdP down/misconfigured, `auth.yaml` broken, or no remaining
`org_admin` ([findings.md](findings.md) risk 3). `ADMIN_TOKEN` is gone;
recovery is **local-only**:

1. Operator with shell access to the control-plane host (and
   `DATABASE_URL`) runs
   `enterprise-api break-glass --org org_01… --email ops@example.com`.
2. The command (no HTTP involved — direct DB): verifies DB connectivity,
   creates-or-finds the user, grants `org_admin` + `org_manager` in
   `user_role_assignments` with `granted_by='breakglass'`, and prints a
   **one-shot recovery session** (access token TTL 15 min, no refresh
   token).
3. It writes `audit_events` action `breakglass.used`
   (`actor_type='system'`, details: host, os user, target) — append-only,
   so the escape hatch is always visible to `auditor`.
4. Operator fixes `auth.yaml`/IdP with that session, then the recovery
   session expires by itself (and can be revoked earlier via
   `POST /auth/v1/logout`).

Threat note: break-glass requires host + DB access, which already implies
full data access — it adds auditability to a power the operator has anyway.
Tested in [validation.md](validation.md) §5.

## 10. Config (env additions)

| Var | Default | Meaning |
|---|---|---|
| `AUTH_CONFIG_PATH` | `/etc/xnobrain/auth.yaml` | the RBAC/backends file |
| `ACCESS_TOKEN_TTL` | `15m` | overrides `session.access_ttl` if set |
| `REFRESH_TOKEN_TTL` | `720h` | overrides `session.refresh_ttl` |
| `PUBLIC_BASE_URL` | — (required when oidc/saml enabled) | redirect/ACS URL base |
| `SMTP_URL` | empty | invite email delivery; empty ⇒ invite links returned to the caller only (air-gapped mode) |

`ADMIN_TOKEN` and `AUTH_MODE`/`AUTH_GRPC_ADDR` are **removed** (backends
subsume them). `/readyz` reports the loaded `config_hash`.
