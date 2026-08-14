# E01 — Implementation

Ordered, file-by-file steps. All new code lands in the **new private repo**
`xnobrain-enterprise`; the public repo gets doc touch-ups only. Cross-links:
[README.md](README.md), [findings.md](findings.md),
[architecture.md](architecture.md), [approaches.md](approaches.md),
[validation.md](validation.md).

Conventions for every phase:

- Go ≥ 1.23 ("verify" current stable at repo creation), `gofmt` + `go vet`
  clean, table-driven tests, no ORM, no SQL outside `internal/store`.
- Store tests run against a real PostgreSQL 16 container (compose service or
  `testcontainers-go` — pick one in Phase 2 and stick to it; "verify"
  testcontainers licensing fits a private repo, else use compose +
  `TEST_DATABASE_URL`).
- Never log or fixture-print tokens, hashes, nonces, signatures, or keys.

## Phase 0 — Repo bootstrap

Create the private repository (org per findings.md §8.4, "verify").

| File | Content |
|---|---|
| `xnobrain-enterprise/go.mod` | `module github.com/vn-fin/xnobrain-enterprise`; deps: pgx (`github.com/jackc/pgx/v5`), golang-migrate, chi router (or stdlib `net/http` mux — decide here, stdlib is acceptable), `github.com/oklog/ulid/v2` |
| `xnobrain-enterprise/Makefile` | `run`, `test`, `check` (fmt+vet+test), `migrate-up`, `migrate-down`, `build`, `docker-build`, `compose-up` |
| `xnobrain-enterprise/.env.example` | every var from [architecture.md](architecture.md) § Config, with comments, no real secrets |
| `xnobrain-enterprise/cmd/enterprise-api/main.go` | load config → open store → run migrations (if `MIGRATE_ON_START`) → build router → serve with graceful shutdown; subcommands `migrate up\|down\|status` |
| `xnobrain-enterprise/internal/config/config.go` | typed struct from env; `Validate()` fails fast on missing `DATABASE_URL`, missing `ADMIN_TOKEN` outside dev mode |
| `xnobrain-enterprise/internal/config/config_test.go` | table tests: defaults, required-var failures, TTL parsing |
| `xnobrain-enterprise/internal/logging/logging.go` | `slog` JSON handler; helper `Redact(s string) string` (keeps prefix, masks rest) used anywhere an identifier *might* be sensitive |
| `xnobrain-enterprise/internal/httpapi/server.go` | `http.Server` lifecycle, timeouts (read/write/idle), graceful stop |
| `xnobrain-enterprise/internal/httpapi/router.go` | route table; only health endpoints wired in this phase |
| `xnobrain-enterprise/internal/httpapi/middleware.go` | request-id, access log (method, route template, status, latency — never bodies or auth headers), panic recovery |
| `xnobrain-enterprise/internal/httpapi/health.go` | `GET /healthz` (always 200 if process up), `GET /readyz` (DB ping + migration version current) |
| `xnobrain-enterprise/Dockerfile` | multi-stage: `golang` builder → static binary → distroless/base; non-root |
| `xnobrain-enterprise/docker-compose.yaml` | services: `enterprise-api` (build, env from `.env`, publishes `8710:8080`) + `postgres` (`postgres:16`, volume, healthcheck); `.yaml` extension |
| `xnobrain-enterprise/AGENTS.md` | short agent guide mirroring the public repo's rules: no ORM, raw SQL, contracts live in the public repo's `docs/contracts/`, never log secrets |

Tests (Phase 0): config table tests; `httptest` for `/healthz`; compose
boots and `/readyz` flips to 200 once Postgres is healthy.

## Phase 1 — Schema v1

| File | Content |
|---|---|
| `xnobrain-enterprise/internal/store/migrations/0001_init.up.sql` | full DDL from [architecture.md](architecture.md) § schema: `tenants`, `users`, `devices`, `device_challenges`, `device_tokens`, `plans`, `plan_assignments`, `audit_events`, append-only trigger, all indexes, and the four seeded `plans` rows with the exact `docs/plans.md` matrix values (findings.md §3; enterprise numerics `-1`, `telemetry_retention_days` 365) |
| `xnobrain-enterprise/internal/store/migrations/0001_init.down.sql` | reverse-order drops (trigger, function, tables) |
| `xnobrain-enterprise/internal/store/migrate.go` | `embed.FS` of `migrations/`, golang-migrate library wiring, `Up/Down/Status` |
| `xnobrain-enterprise/internal/store/migrate_test.go` | against real PG16: up from empty; down to empty; up→down→up idempotent; seeded plans present with correct values |

## Phase 2 — Store layer (raw SQL)

One file per aggregate; every function takes `context.Context` and either
`*pgxpool.Pool` or the tx helper. Errors map to typed store errors
(`ErrNotFound`, `ErrConflict`) — no SQL error strings escape the package.

| File | Surface (signatures indicative) |
|---|---|
| `xnobrain-enterprise/internal/store/store.go` | `New(dsn)`, pool config, `WithTx(ctx, fn)` helper, typed errors |
| `xnobrain-enterprise/internal/store/tenants.go` | `CreateTenant`, `GetTenant`, `ListTenants(limit, offset)` (with user/device counts + plan for the admin view) |
| `xnobrain-enterprise/internal/store/users.go` | `EnsureUserByAuthSubject(subject, displayName, email) (User, created bool)` — creates personal tenant + user + default `free` assignment in one tx |
| `xnobrain-enterprise/internal/store/devices.go` | `UpsertDeviceByPublicKey`, `GetDevice`, `ClaimDevice(deviceID, userID, tenantID)`, `RevokeDevice`, `TouchLastSeen` (throttled), `ListDevices(statusFilter, limit, offset)` |
| `xnobrain-enterprise/internal/store/challenges.go` | `CreateChallenge(deviceID, purpose, ttl)`, `ConsumeChallenge(id)` (single-use, atomic `UPDATE … WHERE consumed_at IS NULL RETURNING`), opportunistic `DeleteExpired` |
| `xnobrain-enterprise/internal/store/tokens.go` | `InsertToken(deviceID, hash, ttl)`, `LookupLiveToken(hash)` (joins `devices.status <> 'revoked'`), `RevokeToken(id)`, `RevokeAllForDevice(deviceID)` |
| `xnobrain-enterprise/internal/store/plans.go` | `GetPlan(id)`, `ResolvePlanForDevice(deviceID)` / `ResolvePlanForUser(userID)` (assignment → default `free`) |
| `xnobrain-enterprise/internal/store/audit.go` | `AppendAudit(event)`; **no** update/delete functions exist |
| `…/store/*_test.go` | table tests per file against PG16: claim constraint (`status='claimed'` requires user+tenant), `plan_assignments` one-of check, challenge single-use race (two concurrent consumes → exactly one wins), token hash uniqueness, audit append-only trigger fires on UPDATE/DELETE |

## Phase 3 — Device identity

| File | Content |
|---|---|
| `xnobrain-enterprise/internal/ids/ids.go` (+test) | prefixed ULIDs: `NewDevice() "dev_…"`, `NewTenant`, `NewUser`, `NewToken`, `NewChallenge`, `NewAssignment` |
| `xnobrain-enterprise/internal/deviceauth/challenge.go` | nonce generation (crypto/rand 32B), Ed25519 verify (`crypto/ed25519`), pubkey validation (length 32, not all-zero) |
| `xnobrain-enterprise/internal/deviceauth/token.go` | `Mint() (raw string, hash []byte)` — `b4e_dt_` + 32B base64url; `Hash(raw)`; TTL policy from config |
| `xnobrain-enterprise/internal/deviceauth/*_test.go` | sign/verify round-trip, tampered nonce fails, wrong key fails, mint/hash stability, raw token never equals stored form |
| `xnobrain-enterprise/internal/httpapi/device.go` | handlers for `register`, `prove`, `refresh` (phase 1 of two-phase), `claim`, `revoke` per [architecture.md](architecture.md) § endpoints; DTO structs with strict JSON decoding; stable error codes (`invalid_signature`, `challenge_consumed`, `challenge_expired`, `device_revoked`, `already_claimed`) |
| `xnobrain-enterprise/internal/httpapi/deviceauth_middleware.go` | extracts device bearer, `LookupLiveToken`, attaches device to context, bumps `last_seen_at` |
| `xnobrain-enterprise/internal/httpapi/device_test.go` | httptest table tests: happy paths + every error row; audit rows asserted for register/enroll, claim, revoke |

Audit actions written in this phase: `device.register`, `device.enroll`
(successful prove), `device.token_refresh`, `device.claim`, `device.revoke`.
`details` JSONB carries only ids and reasons — asserted secret-free in tests.

## Phase 4 — Entitlements + edition policy

| File | Content |
|---|---|
| `xnobrain-enterprise/pkg/edition/policy.go` | the contract stub from [architecture.md](architecture.md): `Subject`, `Document`, `Policy` interface; doc comment pointing at the public repo's `docs/contracts/entitlements-v1.md` |
| `xnobrain-enterprise/internal/entitlements/document.go` | `Build(plan store.Plan, subj edition.Subject, now time.Time) edition.Document` + `ETag(doc)` (`"<plan_id>-<revision>"`); implements `edition.Policy` backed by `store` |
| `xnobrain-enterprise/internal/entitlements/document_test.go` | golden tests: all four plans → documents matching the findings.md §3 matrix exactly; `-1` serialization; capability flags; ETag stability across identical inputs, change on revision bump |
| `xnobrain-enterprise/internal/httpapi/entitlements.go` | `GET /entitlements/v1/current`: auth via device token **or** user bearer; `If-None-Match` → `304`; anonymous device → `free` |
| `xnobrain-enterprise/internal/httpapi/entitlements_test.go` | httptest: device-auth free doc, claimed-device doc follows owner's plan, 304 flow, 401 without auth |

## Phase 5 — Auth seam + claim end-to-end

| File | Content |
|---|---|
| `xnobrain-enterprise/internal/authseam/resolver.go` | `type Principal struct { AuthSubject, DisplayName, Email string }`; `type PrincipalResolver interface { Resolve(ctx, bearerToken string) (Principal, error) }`; sentinel `ErrUnauthenticated` |
| `xnobrain-enterprise/internal/authseam/grpc.go` | gRPC client skeleton: dial `AUTH_GRPC_ADDR`, call the external auth service ("verify" proto/method with auth-service owners — findings.md §8.1); compiles behind the interface, integration-tested only when the proto is confirmed |
| `xnobrain-enterprise/internal/authseam/static.go` | dev/test resolver: env or literal map `token → Principal`; enabled only when `AUTH_MODE=static` |
| `xnobrain-enterprise/internal/authseam/static_test.go` | resolves known token, rejects unknown |
| `xnobrain-enterprise/internal/httpapi/userauth_middleware.go` | bearer → `PrincipalResolver.Resolve` → `store.EnsureUserByAuthSubject` (personal tenant + default `free` on first sight) → principal on context |
| update `xnobrain-enterprise/internal/httpapi/device.go` | wire `claim` fully: user principal + `X-Device-Token` both required; same-user re-claim idempotent; cross-user claim `409` |
| `xnobrain-enterprise/internal/httpapi/claim_test.go` | httptest with static resolver: full claim matrix (anonymous→claimed, key unchanged — assert `public_key` row identical before/after; revoked device 403; second user 409) |

## Phase 6 — Admin slice, hardening, full-flow test

| File | Content |
|---|---|
| `xnobrain-enterprise/internal/httpapi/adminauth_middleware.go` | static `ADMIN_TOKEN` bearer, `crypto/subtle.ConstantTimeCompare`; 401 on mismatch; every admin request audited (`actor_type='admin'`) |
| `xnobrain-enterprise/internal/httpapi/admin.go` | `GET /admin/v1/devices` (status filter, paging, `last_seen_at`), `GET /admin/v1/tenants` (counts + plan) per [architecture.md](architecture.md) examples; response structs exclude `public_key` and all token fields by construction |
| `xnobrain-enterprise/internal/httpapi/admin_test.go` | httptest: 401 without/with-wrong token, list contents after seeding, no key/token material in serialized JSON (assert on raw body) |
| `xnobrain-enterprise/internal/httpapi/fakedevice_test.go` | **the flagship test** — an in-process fake device (own Ed25519 keypair) drives, against a full httptest server + real PG16: register → prove → entitlements(free) → refresh(rotate) → old token dead → claim (static-resolver user) → entitlements follows owner plan → admin list shows `claimed` + `last_seen_at` → revoke → refresh fails `device_revoked` → token dead → admin list shows `revoked`. Log output captured and grepped for `b4e_dt_`, nonces, and key bytes — zero hits |
| `xnobrain-enterprise/README.md` | quickstart: compose up, env vars, endpoint summary, link back to the public repo's `docs/contracts/` as the source of truth |

## Public-repo deliverables (this repo, `/home/kim/Documents/xno/xnobrain-dev/xnobrain`)

Deliberately tiny — E01 implements existing contracts and adds **no new
contract** (`usage-ingest-v1` belongs to E02):

1. This plan package: `plans/enterprise/E01_control_plane_foundation/` (the
   six files, already linked from `plans/enterprise/README.md` — verify the
   link resolves; no README edit expected).
2. Optional one-line status notes: tick nothing in `plans/enterprise/README.md`'s
   checklist until [validation.md](validation.md) evidence exists.
3. **No** changes to `docs/contracts/*` unless implementation uncovers a
   genuine ambiguity in `device-command-v1`/`entitlements-v1`; if it does,
   the fix is a clarifying (compatible) edit to the contract in this repo,
   reviewed against the "incompatible change ⇒ v2" rule — never a silent
   reinterpretation.
4. **No** OSS runtime code. The Python device connector is E02/E03 scope.

## Test plan summary (per phase gates)

| Phase | Gate |
|---|---|
| 0 | `make check` green; compose up; `/readyz` 200 |
| 1 | migrate up/down/up clean on PG16; plans seeded exactly per matrix |
| 2 | store table tests green incl. constraint and race tests |
| 3 | device endpoint tests green; audit rows present and secret-free |
| 4 | entitlement golden docs match `docs/plans.md` for all four plans; ETag/304 |
| 5 | claim matrix green; key-unchanged assertion; first-principal bootstrap creates tenant+user+free |
| 6 | fake-device full-flow test green; admin tests green; log-grep secret sweep zero hits |

Final handoff: all of [validation.md](validation.md) checked with evidence
lines filled in.
