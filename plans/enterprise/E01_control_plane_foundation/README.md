# E01 — Control-plane foundation

Priority: **P0 — everything in the enterprise program depends on it.** E02
(usage collection) needs device identity and the ingest seam; E03 (fleet) needs
devices + tenancy; E04 (voice) needs entitlements. Nothing else can start until
the skeleton, schema, and device-identity slice exist.

This plan creates a **new private repository** `xnobrain-enterprise`
(Go + PostgreSQL, raw SQL, no ORM) and implements the minimal control plane:
service skeleton, schema v1, the **device-identity slice** of
`docs/contracts/device-command-v1.md`, the **read slice** of
`docs/contracts/entitlements-v1.md`, the `pkg/edition.Policy` contract stub,
and a token-authenticated minimal admin surface.

Read the sibling documents in order:

- [findings.md](findings.md) — what the existing contracts already fix, the
  retired-spec caveat, and the open questions.
- [architecture.md](architecture.md) — repo layout, full SQL schema v1,
  endpoint table with JSON examples, Ed25519 challenge flow, token lifecycle,
  tenancy model.
- [approaches.md](approaches.md) — the decisions (token format, migration
  tool, binary shape, admin auth) with options and rationale.
- [implementation.md](implementation.md) — ordered phases, file-by-file for
  the new repo, test plan per phase.
- [validation.md](validation.md) — acceptance checklist with evidence lines.

Also read before starting (public repo, mandatory grounding):
[`plans/enterprise/README.md`](../README.md) (program frame),
[`docs/enterprise-extension.md`](../../../docs/enterprise-extension.md),
[`docs/plans.md`](../../../docs/plans.md),
[`docs/contracts/device-command-v1.md`](../../../docs/contracts/device-command-v1.md),
[`docs/contracts/entitlements-v1.md`](../../../docs/contracts/entitlements-v1.md),
[`docs/repository-ownership.md`](../../../docs/repository-ownership.md),
[`AGENTS.md`](../../../AGENTS.md).

## Goal

Stand up the minimal Go control plane that the rest of the program builds on:

1. **Repo/service skeleton** — one Go binary (`cmd/enterprise-api`), config
   from environment variables, structured logging, `/healthz` + `/readyz`, a
   migrations runner over plain SQL files (golang-migrate style — **no ORM**),
   a Docker image, and a `docker-compose.yaml` with PostgreSQL 16.
2. **PostgreSQL schema v1** — plain SQL migrations for `tenants`, `users`
   (auth-subject reference only; principal resolution calls the existing
   external auth service over gRPC — this plan defines the **seam**, not the
   auth server), `devices`, `device_tokens` (short-lived, hashed at rest),
   `plans` + `plan_assignments` (stable IDs `free|pro|promax|enterprise` per
   `docs/plans.md`), and append-only `audit_events`.
3. **Device identity endpoints** — the identity slice of `device-command-v1`
   **only** (not the command channel): `POST /device/v1/register` (Ed25519
   pubkey → challenge), `POST /device/v1/prove` (challenge signature →
   device_id + short-lived token), `POST /device/v1/refresh`
   (proof-of-possession), `POST /device/v1/claim` (authenticated user claims
   an anonymous device; device key unchanged), `POST /device/v1/revoke`.
   Anonymous Free devices are possession identities, per the contract.
4. **Entitlements read slice** — `GET /entitlements/v1/current` returning the
   entitlement document of `entitlements-v1` (version, edition, plan ID,
   subject/tenant, limits with `-1` = unlimited, capability flags,
   revision/ETag), served from static per-plan values in the `plans` table.
5. **`pkg/edition.Policy` stub** — the Go home of the policy contract the OSS
   docs already reference (`docs/enterprise-extension.md`).
6. **Minimal admin surface** — `GET /admin/v1/devices` (status + last_seen)
   and `GET /admin/v1/tenants`, authenticated by a static admin token. UI
   deferred.

## Non-goals

- **No usage ingest.** `POST /ingest/v1/usage`, snapshots, rollups, and the
  OSS usage reporter are **E02**.
- **No Incus orchestration, no fleet lifecycle.** That is **E03**.
- **No command channel.** No WebSocket/long-poll stream, no command envelope,
  no cron dispatch, no missed-occurrence handling — only the *identity*
  portion of `device-command-v1`. The command channel arrives with E03.
- **No quota Reserve/Commit/Release.** `entitlements-v1` quota operations
  (idempotent reservations, leases, denial codes) are deferred to a later
  plan ("Entitlements & billing enforcement", program function 5). E01 serves
  the **static entitlement document only**.
- **No SSO/RBAC/orgs beyond the auth seam.** Principal resolution is an
  interface plus a gRPC client skeleton; the auth server itself is external
  and pre-existing. No SAML/OIDC, no roles, no multi-member orgs.
- **No billing, no subscription integration.** Plan assignments are manual
  rows (newly authenticated accounts default to `free`, per `docs/plans.md`).
- **No admin UI.** JSON endpoints only; dashboards come later.
- **No changes to OSS runtime behavior.** The public repo gains at most doc
  cross-references. `ENTERPRISE_API_URL` stays optional; an enterprise outage
  must never restrict local features (`AGENTS.md`).

## Constraints (restated, binding)

- Two-repo split per `docs/repository-ownership.md`: control plane lives in
  the **new private** `xnobrain-enterprise` repo; the OSS Python repo never
  requires it.
- Go + PostgreSQL, **no ORM** — raw SQL and plain-file migrations
  (`docs/enterprise-extension.md`: "Neither project may introduce an ORM").
- Cross-repo protocols are the versioned contracts in the public repo's
  `docs/contracts/`; incompatible changes create `v2`.
- Prefer HTTP contracts over Go imports. The OSS repo is Python, so there are
  no OSS internal Go packages to import; the module path is therefore free to
  be `github.com/vn-fin/xnobrain-enterprise` ("verify" the GitHub org — see
  [approaches.md](approaches.md) decision E). The nesting rule from
  `docs/enterprise-extension.md` applies only if a Go import ever appears.
- Numeric `-1` = unlimited; `0` = unavailable (`docs/plans.md`,
  `entitlements-v1`).
- The numbered `docs/implementation/*` files are **retired Go-era specs**:
  their contracts and concepts stand; their package paths do not.

## Phase overview

- **Phase 0 — Repo bootstrap.** New repo, `go.mod`, Makefile, config, logging,
  health endpoints, Dockerfile, `docker-compose.yaml` with PostgreSQL 16,
  migrations runner wired to `internal/store/migrations/`.
- **Phase 1 — Schema v1.** `0001_init.up.sql` / `0001_init.down.sql`: all
  seven tables plus challenge storage, seeded `plans` rows from the
  `docs/plans.md` matrix, append-only guard on `audit_events`.
- **Phase 2 — Store layer.** Raw-SQL repositories (`internal/store`) with
  table-driven tests against a real Postgres 16 (dockerized).
- **Phase 3 — Device identity.** `internal/deviceauth` (Ed25519 challenge,
  token mint/hash/verify) + the five `/device/v1/*` endpoints + audit events.
- **Phase 4 — Entitlements + edition policy.** `pkg/edition` stub,
  `internal/entitlements` document builder, `GET /entitlements/v1/current`
  with revision/ETag and `304` support.
- **Phase 5 — Auth seam + claim.** `internal/authseam.PrincipalResolver`
  interface, gRPC client skeleton, dev-mode static resolver, `/device/v1/claim`
  end-to-end (personal tenant auto-created on first principal).
- **Phase 6 — Admin slice + hardening.** Static-token admin middleware,
  `/admin/v1/devices`, `/admin/v1/tenants`, secret-redaction sweep, the full
  fake-device flow test, docs.

File-by-file steps are in [implementation.md](implementation.md).

## Definition of done

- A device on a laptop (simulated by the httptest fake-device client) can
  **enroll anonymously** (`register` → `prove`), receive a `device_id` and a
  short-lived token, **refresh** the token by proof of key possession, be
  **claimed** by an authenticated user **without changing its device key**,
  and then **appear in `GET /admin/v1/devices`** with correct status and
  `last_seen_at`.
- `POST /device/v1/revoke` flips the device to `revoked`; a subsequent
  refresh (and any token use) fails; the admin list shows `revoked`.
- `GET /entitlements/v1/current` serves a document matching the
  `docs/plans.md` matrix for **all four plans** (`free`, `pro`, `promax`,
  `enterprise`), with `-1` = unlimited semantics for enterprise custom limits,
  correct `revision`/`ETag`, and `304 Not Modified` on `If-None-Match`.
- An anonymous (unclaimed) device resolves plan `free`.
- Tokens are stored **only as hashes**; no token, key material, or challenge
  nonce ever appears in logs (verified by test, not by inspection).
- `migrate up` from empty → v1 and `migrate down` → empty both succeed on
  PostgreSQL 16; the binary refuses to serve if migrations are behind.
- All evidence lines in [validation.md](validation.md) are green.
