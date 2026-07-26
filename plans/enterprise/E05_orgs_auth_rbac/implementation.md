# E05 — Implementation

Ordered phases, file-by-file. Track 1 is Go in `brain4all-enterprise`
(everything except Phase 10); Track 2 is the thin OSS surface in this
repo. Cross-links: [README.md](README.md), [findings.md](findings.md),
[architecture.md](architecture.md), [approaches.md](approaches.md),
[validation.md](validation.md).

Repo layout baseline is E01's
([E01 architecture.md](../E01_control_plane_foundation/architecture.md)
§ "Service layout") — align actual package names against the merged E01
skeleton in Phase 0, the same "verify" posture E02 takes.

## Phase 0 — Freeze the contracts

The permission catalog and the `auth.yaml` schema are contracts consumed
by every later plan (E03 `fleet.manage`, E04 `voice.read_org`, build-map
items 5/7/10). They freeze before any code.

1. **`internal/authz/catalog.go`** (new) — the closed permission set from
   [architecture.md](architecture.md) §3 as typed constants + the four
   built-in role → permission maps + reserved strings (`voice.read_org`,
   `fleet.manage`). One table test asserts the catalog matches the
   documented table (golden list) so additions are always a reviewed diff.
2. **`docs/authcfg-schema.md`** (enterprise repo) — the `auth.yaml` v1
   schema: keys, types, required/optional, validation rules, the built-in
   non-weakening rule, reserved keys (`password_policy.totp`). The
   annotated example in [architecture.md](architecture.md) §2 is the
   normative sample; `config/auth.yaml.example` (step 4 below) must stay
   byte-consistent with it.
3. **`docs/contracts/enterprise-auth-v1.md`** (THIS repo — cross-repo
   protocols live in the public `docs/contracts/`, program principle) —
   the OSS-facing slice only: `GET /auth/v1/backends`,
   `POST /auth/v1/login`, `POST /auth/v1/refresh`, `POST /auth/v1/logout`,
   `GET /auth/v1/session` (request/response JSON, error codes, token
   handling rules: never logged, stored under `DATA_DIR/enterprise/`,
   outage ⇒ features dormant never restricted per `AGENTS.md`). The
   OIDC/SAML browser flows and all `/orgs/v1/*` admin surfaces are
   enterprise-internal and stay documented in the enterprise repo.
4. **`config/auth.yaml.example`** (enterprise repo, new) — the
   architecture.md §2 example verbatim, shipping in the image and
   referenced by `docker-compose.yaml`.
5. Coordination checks ("verify"): E01 merged package names; whether E02's
   migration already took `0002` (pick next free number); confirm with E03
   that `EnterpriseSection.tsx` creation moves to this plan
   ([findings.md](findings.md) risk 6).

Tests: catalog golden test; example-file-parses test placeholder (real
loader in Phase 1).

## Phase 1 — Config loader (`internal/authcfg`)

- `internal/authcfg/types.go` — structs mirroring the schema: `Config`,
  `Backend` (per-type embedded config), `Role`, `Binding`, `GroupMapping`,
  `Session`.
- `internal/authcfg/load.go` — read `AUTH_CONFIG_PATH`, strict YAML
  decode (unknown keys = error), env/file secret indirection resolution
  (`*_env`, `*_file`), SHA-256 `config_hash` over the raw bytes.
- `internal/authcfg/validate.go` — every rule from
  [architecture.md](architecture.md) §2: permission strings ∈ catalog;
  built-in roles not weakened; backend ids unique; enabled oidc/saml
  require `PUBLIC_BASE_URL`; role-count warning (>25); best-effort
  "someone can reach org.manage" lockout lint
  ([findings.md](findings.md) risk 3).
- `internal/authcfg/reload.go` — atomic `atomic.Pointer[Config]` swap;
  SIGHUP handler wired in `cmd/enterprise-api/main.go`; reload result +
  hash logged and written as `authcfg.reloaded` audit row; invalid file
  keeps previous config.
- `internal/httpapi/authcfg.go` — `GET /admin/v1/authcfg` (summary, no
  secrets) + `POST /admin/v1/authcfg/reload` (permission-gated once Phase
  4 lands; temporarily loopback-only).

Tests: golden valid/invalid fixtures (unknown permission, weakened
built-in, missing secret env, duplicate backend id); reload swaps under
concurrent readers (`-race`); hash stability.

## Phase 2 — Schema + store

- `internal/store/migrations/00xx_orgs_auth_rbac.up.sql` / `.down.sql` —
  the complete DDL from [architecture.md](architecture.md) §1 (orgs,
  org_memberships + single-org partial index, invitations,
  user_role_assignments, auth_identities, local_credentials,
  user_sessions, scim_tokens, audit ALTERs). Extends E01/E02 history,
  never rewrites.
- `internal/store/orgs.go`, `memberships.go`, `invitations.go`,
  `roleassign.go`, `identities.go`, `credentials.go`, `sessions.go`,
  `scimtokens.go` — raw-SQL repositories, E01 store conventions (Store
  struct, tx helper, error mapping). Every org-scoped query takes the
  org/tenant id as a required argument — no implicit global reads
  (`docs/enterprise-extension.md`: tenant identity flows into
  repositories).
- Extend `internal/store/audit.go` with `org_id`/`request_id` params.

Tests: table-driven against dockerized Postgres 16 (E01 pattern);
migration up→down→up; single-org index violation; membership state
transitions reject illegal jumps (e.g. `deprovisioned → active`).

## Phase 3 — Local backend + sessions

- `internal/authn/backend.go` — the plug interface:
  `type Backend interface { ID() string; Type() string;
  Login(ctx, Credentials) (Identity, error) }` where `Identity =
  {ExternalSub, Email, DisplayName, Groups []string}`. Registry built
  from loaded `authcfg` backends.
- `internal/authn/local/local.go` — argon2id via
  `golang.org/x/crypto/argon2` (id variant; params ≥ RFC 9106 low-memory
  recommendation, PHC-encoded so future param bumps rehash on login);
  constant-time verify; `failed_attempts` + `locked_until` backoff;
  identical 401 body for unknown-email vs wrong-password.
- `internal/session/session.go` — mint (`b4e_at_`/`b4e_rt_` + 32 rand
  bytes, sha256 at rest), validate (join membership status), refresh with
  rotation + reuse-revocation, revoke, revoke-all-for-user. Mirrors
  `internal/deviceauth/token.go`.
- `internal/httpapi/auth.go` — `GET /auth/v1/backends`,
  `POST /auth/v1/login`, `POST /auth/v1/refresh`, `POST /auth/v1/logout`,
  `GET /auth/v1/session`; audit rows per
  [architecture.md](architecture.md) §7 vocabulary.
- Personal-tenant bootstrap: reuse E01's lazy user+tenant creation on
  first identity sighting (the E01 `authseam` code path moves here or is
  called from here — resolve against the merged skeleton).

Tests: argon2id round-trip + PHC param rehash; lockout backoff; token
never-logged grep (E01 validation pattern); refresh reuse revokes
session; suspended membership fails login and refresh.

## Phase 4 — Authz engine + middleware

- `internal/authz/engine.go` — `EffectivePermissions(cfg, principal,
  dbRoles, fileBindings, groups) map[Permission]struct{}` implementing
  architecture.md §4 steps [4]–[5]; pure function, fully table-testable.
- `internal/authz/middleware.go` — `Require(perm Permission)` HTTP
  middleware: token → session → membership gate → effective set → 401/403
  envelope codes (`invalid_token`, `permission_denied`,
  `account_suspended`, `capability_unavailable`); principal into
  `context`; request_id propagation into audit.
- Capability gate: org/RBAC/SSO/audit-export endpoints check the tenant
  plan's `rbac`/`sso`/`audit_export` flags (E01 `plans` JSONB) → `403
  capability_unavailable` (`docs/plans.md` 403 rule).
- Wire `POST /admin/v1/authcfg/reload` to `policies.write` (removing the
  Phase-1 loopback stopgap).

Tests: **the catalog table test** — for every (built-in role × catalog
permission) pair assert allow/deny against the documented matrix; group
mapping add/remove takes effect without new login; role removed from file
stops resolving; org A principal never yields org B permissions.

## Phase 5 — Retrofit E01/E02 handlers (delete `ADMIN_TOKEN`)

One by one ([findings.md](findings.md) §1.2 table):

1. `internal/httpapi/admin.go` — `GET /admin/v1/devices`: replace static
   middleware with `Require(devices.read_org)`; add self-scope fallback
   (`devices.read_self` ⇒ rows where `user_id = principal`); org filter
   forced to principal's org tenant; `platform.operate` unrestricted.
2. Same file — `GET /admin/v1/tenants`: `Require(platform.operate)`.
3. `internal/httpapi/device.go` — `POST /device/v1/revoke`: admin variant
   becomes `Require(devices.manage)` within the device's org; owning-user
   and device-self paths unchanged.
4. `internal/httpapi/device.go` — `POST /device/v1/claim`: user bearer
   resolves via `internal/session` instead of `authseam`; if the session
   has org context, the device claims into the **org tenant** (E02 usage
   then attributes to the org — approaches.md C corollary).
5. `internal/adminapi/usage.go` (E02) — `GET /admin/v1/usage`:
   `Require(usage.read_self|usage.read_org)` with server-forced scoping
   (member ⇒ `user=self`; manager ⇒ `tenant=org`); export.csv ⇒
   `Require(usage.export)`.
6. `internal/entitlements/document.go` — subject gains org context; org
   tenant plan resolution uses E01's existing tenant-level
   `plan_assignments`.
7. Delete: `ADMIN_TOKEN` config, static admin middleware, `AUTH_MODE`/
   `AUTH_GRPC_ADDR` envs, `internal/authseam` static resolver (dev mode
   is now the local backend + seeded user); update `.env.example`,
   `docker-compose.yaml`, README.
8. `cmd/enterprise-api/breakglass.go` — the CLI subcommand per
   [architecture.md](architecture.md) §9.

Tests: every retrofitted endpoint × {no token, expired, member, manager,
admin, auditor, platform} matrix; break-glass creates audited recovery
session; repo-wide grep asserts `ADMIN_TOKEN` is gone.

## Phase 6 — Orgs, invitations, lifecycle, members list

- `internal/orgs/service.go` — create org (creator → org_admin +
  org_manager grants; cloud path capability-gated, self-hosted first-boot
  path), settings, members list projection (name, email, status, roles,
  last_login — the org-manager members view), suspend/reactivate/
  deprovision (session + device-claim revocation), role grant/revoke.
- `internal/orgs/invitations.go` — invite mint (hash at rest, 7-day TTL,
  email-bound, single-use), accept (local: set password; SSO: link
  identity on next login), revoke, CSV import (`email,role[;role]` lines
  → bulk invites; SMTP optional — without `SMTP_URL` the API returns the
  invite links for out-of-band delivery, the air-gapped mode).
- `internal/httpapi/orgs.go` — all `/orgs/v1/*` routes from
  [architecture.md](architecture.md) §7 incl. `GET /orgs/v1/audit` +
  `/orgs/v1/audit/export` (JSONL/CSV streaming from `audit_events`
  filtered by `org_id`).

Tests: full lifecycle walk invite→accept→active→suspend→deprovision with
an audit-trail assertion after each step; org-B isolation on every route;
one-live-invite-per-email; expired/reused invite tokens.

## Phase 7 — OIDC backend + group mappings

- `internal/authn/oidc/oidc.go` — code flow per
  [architecture.md](architecture.md) §5.2 using
  `github.com/coreos/go-oidc/v3` + `golang.org/x/oauth2` ("verify"
  current recommended libs at implementation time); server-side
  state/nonce (TTL 10 min, single-use); groups-claim capture into
  `user_sessions.idp_groups`.
- `internal/httpapi/auth.go` — `GET /auth/v1/oidc/start`, `GET
  /auth/v1/oidc/callback`.
- JIT rule: first sighting creates user + personal tenant; org membership
  attaches **only** if an invitation/provisioned row matches the verified
  email or SCIM externalId — no open org signup (fixed req 1).

Tests: against a local fake IdP (httptest issuing signed JWKS tokens):
happy path, bad nonce/state, group→role mapping end-to-end (in group ⇒
org_manager allowed on `/admin/v1/usage`; removed from group ⇒ 403 on
next request without re-login).

## Phase 8 — SAML, LDAP, external gRPC

- `internal/authn/saml/saml.go` — SP-initiated flow, metadata endpoint,
  ACS; library candidate crewjam/saml ("verify" —
  [findings.md](findings.md) §8.3); `idp_metadata_file` keeps it
  air-gap-friendly.
- `internal/authn/ldap/ldap.go` — simple bind via `bind_dn_template`,
  optional group search; go-ldap ("verify").
- `internal/authn/extgrpc/extgrpc.go` + `proto/authsvc/v1/authsvc.proto` —
  `VerifyCredentials(credentials) → {subject, email, display, groups[]}`;
  proto shape stays draft until the external-service owner confirms
  ([findings.md](findings.md) §8.1); publish as
  `docs/contracts/enterprise-auth-grpc-v1.md` when settled.

Tests: SAML golden assertions (valid, bad signature, expired); LDAP
against a containerized OpenLDAP fixture; extgrpc against an in-process
fake server; each backend disabled-by-config returns 404 from
`/auth/v1/backends`.

## Phase 9 — SCIM Users slice

- `internal/scim/handler.go` + `internal/scim/mapping.go` — the six
  routes of [architecture.md](architecture.md) §6, scim+json
  (de)serialization, `userName eq` filter only, lifecycle mapping table
  from [findings.md](findings.md) §6; token auth via `scim_tokens`.
- `internal/httpapi/orgs.go` — `POST/DELETE /orgs/v1/scim-tokens`
  (`scim.manage`).

Tests: SCIM lifecycle test — POST(active)→PATCH(false)→PATCH(true)→
DELETE, asserting internal membership status, session death on suspend,
device-claim revocation on delete, and one audit row per operation;
idempotent re-POST of same externalId; wrong-org token isolation.

## Phase 10 — OSS thin surface (this repo) + dashboard wiring

All dormant without `ENTERPRISE_API_URL` (`AGENTS.md`; `docs/plans.md`
dormancy rule). Exact files:

1. **`brain4all/services/enterprise_auth.py`** (new) — session store:
   login via the enterprise `POST /auth/v1/login` (per
   `docs/contracts/enterprise-auth-v1.md`), tokens persisted atomically
   under `DATA_DIR/enterprise/session.json` (temp-file+fsync+rename
   idiom; never logged), refresh-before-expiry, logout, and
   `get_session_context()` returning `{user, org, roles, capabilities}`
   from `GET /auth/v1/session` (cached, TTL ≤ access TTL). Failures ⇒
   `None`, never an exception into local paths.
2. **`brain4all/handlers/api.py`** — extend the existing `"limits"` entry
   (currently the lambda at line ~97 returning
   `{"plan_id": "self-hosted", "local_features_unlimited": True, …}`):
   when a session context exists, merge
   `{"org": {"id", "name", "roles": […]}, "capabilities": {"rbac": …,
   "sso": …, "audit_export": …}, "plan_id": <from entitlements>}`.
   `local_features_unlimited` stays `True` unconditionally — local limits
   never change (`docs/plans.md` enforcement rule 1).
3. **`brain4all/routes/setup.py`** — local-only routes
   `POST /api/v1/enterprise/auth/login`, `POST
   /api/v1/enterprise/auth/logout`, `GET /api/v1/enterprise/auth/session`
   (tag `System`), thin passthroughs to `enterprise_auth.py` so the
   browser never talks to the enterprise host directly (token stays
   server-side in `DATA_DIR`).
4. **`src/src/features/system/EnterpriseSection.tsx`** (new — created
   HERE, extended later by E03; [findings.md](findings.md) risk 6) —
   login form (backend list from `GET /auth/v1/backends` via the local
   passthrough; local/LDAP credentials; OIDC/SAML shown as "open control
   plane to sign in" link in v1), signed-in card (user, org, roles,
   capability badges), sign-out. Follow the `SystemView` tab pattern: add
   `{ id: 'enterprise', label: 'Enterprise' }` to the tabs array in
   `src/src/features/system/SystemView.tsx` and the `'enterprise'` member
   on `SettingsSection` in `src/src/hooks/useRouter.ts` (identical wiring
   to E03 implementation.md step 10 — whichever merges first does it).
5. **`src/src/features/system/api.ts`** — the three auth passthrough
   calls + extended limits type.
6. **Env/docs** — `.env`/compose examples gain nothing new
   (`ENTERPRISE_API_URL` already specified by E02/E03 plans); README
   Enterprise paragraph gains one sign-in sentence.

Dashboard wiring (enterprise repo, closes the loop): the E02 dashboard
page (E02 architecture §7 "simple dashboard page … follows once the API
is accepted") is served org-scoped: manager sees org totals + per-member
breakdown + members list link; member sees the self view. This plan
delivers the **API gating + members list endpoint**; the dashboard UI
itself remains the E02 follow-up task, now unblocked with real auth.

OSS tests: `brain4all/tests/test_enterprise_auth.py` — dormancy (unset
env ⇒ no routes active work, no calls); session file atomicity; token
never in logs (capture + grep); limits merge shape; enterprise-down ⇒
limits fall back to the current static payload unchanged. Frontend:
`src/src/features/system/api.test.ts` extension + `cd src && npm run
build`.

## Joint validation

Run the full [validation.md](validation.md) checklist: the two-member
self-hosted scenario, the standalone cloud user, security evidence
(argon2id, hashes, reload, suspension, isolation, air-gapped egress
capture).
