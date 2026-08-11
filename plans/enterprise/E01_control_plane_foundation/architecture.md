# E01 — Architecture

Service layout, schema v1, endpoint contract, challenge flow, token
lifecycle, and tenancy for the new `brain4all-enterprise` repo. Cross-links:
[README.md](README.md), [findings.md](findings.md),
[approaches.md](approaches.md), [implementation.md](implementation.md),
[validation.md](validation.md).

## Service layout

One Go module, one binary, modular internal packages (decision C in
[approaches.md](approaches.md)). Module path
`github.com/vn-fin/brain4all-enterprise` ("verify" org — findings.md §8.4).

```
brain4all-enterprise/
├── go.mod                          # module github.com/vn-fin/brain4all-enterprise
├── Makefile                        # run / test / migrate / build / check
├── Dockerfile                      # multi-stage: build → distroless/static
├── docker-compose.yaml             # api + postgres:16 (dev/eval stack)
├── .env.example                    # every env var, documented, no secrets
├── cmd/
│   └── enterprise-api/
│       └── main.go                 # flag-free: env config → wire → serve
├── internal/
│   ├── config/                     # env parsing + validation (fail fast)
│   ├── logging/                    # slog JSON setup; redaction helpers
│   ├── httpapi/                    # HTTP layer only: routing, middleware,
│   │   │                           #   request/response DTOs, error mapping
│   │   ├── server.go               # http.Server lifecycle, graceful stop
│   │   ├── router.go               # mux wiring (net/http + chi or stdlib)
│   │   ├── middleware.go           # request id, logging, auth extraction
│   │   ├── health.go               # GET /healthz, GET /readyz
│   │   ├── device.go               # /device/v1/* handlers
│   │   ├── entitlements.go         # /entitlements/v1/current handler
│   │   └── admin.go                # /admin/v1/* handlers
│   ├── store/                      # raw SQL only — the ONLY package that
│   │   │                           #   touches *sql.DB / pgx; no ORM
│   │   ├── store.go                # Store struct, tx helper, error mapping
│   │   ├── migrate.go              # embedded golang-migrate runner
│   │   ├── migrations/
│   │   │   ├── 0001_init.up.sql
│   │   │   └── 0001_init.down.sql
│   │   ├── tenants.go  users.go  devices.go  tokens.go
│   │   ├── challenges.go  plans.go  audit.go
│   ├── deviceauth/                 # pure logic: Ed25519 verify, nonce gen,
│   │   │                           #   token mint/hash, TTL policy
│   │   ├── challenge.go  token.go
│   ├── entitlements/               # entitlement document builder
│   │   └── document.go             # plans row + principal → JSON doc + ETag
│   ├── authseam/                   # principal resolution seam
│   │   ├── resolver.go             # PrincipalResolver interface + Principal
│   │   ├── grpc.go                 # gRPC client skeleton ("verify" proto)
│   │   └── static.go               # dev-mode resolver (env-configured)
│   └── ids/                        # ULID-ish prefixed ids: dev_, tok_, chl_,
│                                   #   ten_, usr_, aud_
└── pkg/
    └── edition/
        └── policy.go               # the public Policy contract stub
```

Layering rule (mirrors the OSS repo's discipline): `httpapi` owns HTTP
translation only; `deviceauth`/`entitlements` own rules; `store` owns SQL;
packages never reach around each other. `pkg/edition` depends on nothing
internal — it is the published contract surface.

### `pkg/edition.Policy` stub

The Go home of the contract `docs/enterprise-extension.md` expects
("implements `pkg/edition.Policy`… returns limits for the authenticated
principal. Numeric `-1` means unlimited."):

```go
package edition

// Subject identifies who limits apply to.
type Subject struct {
    TenantID string // empty for anonymous devices
    UserID   string // empty for anonymous devices
    DeviceID string
    PlanID   string // free | pro | promax | enterprise
}

// Document is the entitlements-v1 entitlement document.
type Document struct {
    Version      int               `json:"version"` // 1
    Edition      string            `json:"edition"`
    PlanID       string            `json:"plan_id"`
    Subject      Subject           `json:"subject"`
    IssuedAt     time.Time         `json:"issued_at"`
    ExpiresAt    time.Time         `json:"expires_at"`
    Revision     int64             `json:"revision"`
    Limits       map[string]int64  `json:"limits"`       // -1 unlimited, 0 unavailable
    Capabilities map[string]bool   `json:"capabilities"`
    HardwareClass string           `json:"hardware_class"`
}

// Policy returns limits for an authenticated principal.
// E01 ships the static per-plan implementation; Reserve/Commit/Release
// (entitlements-v1 quota ops) extend this interface in a later plan.
type Policy interface {
    Entitlements(ctx context.Context, s Subject) (Document, error)
}
```

## Tenancy model

```
tenant (ten_…)  1 ──── n  user (usr_…)      ← v1: exactly 1 (personal tenant)
      │                        │
      │ 1                      │ 1
      │                        │
      n                        n
   device (dev_…)  ← claimed devices point at BOTH user_id and tenant_id
```

- **Anonymous device**: `tenant_id NULL`, `user_id NULL`, `status='enrolled'`.
  A possession identity (`device-command-v1`). Resolves plan `free`.
- **Claim** sets `user_id` + `tenant_id` (the user's personal tenant) and
  `status='claimed'`. The Ed25519 key is untouched.
- **Personal tenant** is auto-created the first time a principal (auth
  subject from the gRPC seam) is seen: one tenant, one member
  (`docs/enterprise-extension.md`). Org tenants (`kind='organization'`) are
  schema-ready but have no E01 surface.
- **Plan resolution order**: device → owning user's `plan_assignments` row →
  tenant's `plan_assignments` row → default `free`. Anonymous device → `free`.

## PostgreSQL schema v1 (migration `0001_init`)

Plain SQL, PostgreSQL 16, no ORM. IDs are text with type prefixes
(`ten_`, `usr_`, `dev_`, `tok_`, `chl_`, ULID payload) generated in Go
(`internal/ids`) so they are sortable and greppable in logs.

```sql
-- 0001_init.up.sql

CREATE TABLE tenants (
    id           text PRIMARY KEY,                  -- ten_01…
    kind         text NOT NULL DEFAULT 'personal'
                 CHECK (kind IN ('personal', 'organization')),
    display_name text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

-- Reference rows only: the external auth service (gRPC seam) owns identity.
-- A row is created lazily the first time a subject authenticates.
CREATE TABLE users (
    id            text PRIMARY KEY,                 -- usr_01…
    auth_subject  text NOT NULL UNIQUE,             -- opaque subject from auth svc
    tenant_id     text NOT NULL REFERENCES tenants(id),
    display_name  text NOT NULL DEFAULT '',         -- cached, non-authoritative
    email         text NOT NULL DEFAULT '',         -- cached, non-authoritative
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE devices (
    id              text PRIMARY KEY,               -- dev_01…
    tenant_id       text REFERENCES tenants(id),    -- NULL until claimed
    user_id         text REFERENCES users(id),      -- NULL until claimed
    public_key      bytea NOT NULL UNIQUE,          -- 32-byte Ed25519 public key
    name            text NOT NULL DEFAULT '',
    status          text NOT NULL DEFAULT 'enrolled'
                    CHECK (status IN ('enrolled', 'claimed', 'revoked')),
    runtime_version text NOT NULL DEFAULT '',
    last_seen_at    timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    claimed_at      timestamptz,
    revoked_at      timestamptz,
    CHECK ((status <> 'claimed') OR (user_id IS NOT NULL AND tenant_id IS NOT NULL))
);
CREATE INDEX idx_devices_tenant ON devices (tenant_id) WHERE tenant_id IS NOT NULL;
CREATE INDEX idx_devices_user   ON devices (user_id)   WHERE user_id   IS NOT NULL;
CREATE INDEX idx_devices_status ON devices (status);

-- Single-use signature challenges (enroll + refresh). Short TTL, garbage-
-- collected opportunistically; consumed_at enforces single use.
CREATE TABLE device_challenges (
    id          text PRIMARY KEY,                   -- chl_01…
    device_id   text NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    nonce       bytea NOT NULL,                     -- 32 random bytes
    purpose     text NOT NULL CHECK (purpose IN ('enroll', 'refresh')),
    expires_at  timestamptz NOT NULL,
    consumed_at timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_challenges_device ON device_challenges (device_id);
CREATE INDEX idx_challenges_expiry ON device_challenges (expires_at);

-- Short-lived bearer tokens; ONLY the SHA-256 hash is stored.
CREATE TABLE device_tokens (
    id           text PRIMARY KEY,                  -- tok_01…
    device_id    text NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    token_hash   bytea NOT NULL UNIQUE,             -- sha256(raw token)
    expires_at   timestamptz NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    last_used_at timestamptz,
    revoked_at   timestamptz
);
CREATE INDEX idx_tokens_device ON device_tokens (device_id);
CREATE INDEX idx_tokens_expiry ON device_tokens (expires_at);

-- Stable plan IDs per docs/plans.md. limits/capabilities are JSONB so the
-- entitlement document is data-driven; revision bumps on every UPDATE and
-- feeds the entitlement ETag.
CREATE TABLE plans (
    id           text PRIMARY KEY
                 CHECK (id IN ('free', 'pro', 'promax', 'enterprise')),
    display_name text NOT NULL,                     -- 'Basic' for free
    limits       jsonb NOT NULL,                    -- {"agents": 4, …} -1 = unlimited
    capabilities jsonb NOT NULL,                    -- {"managed_telemetry": true, …}
    revision     bigint NOT NULL DEFAULT 1,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

-- Exactly one of user_id / tenant_id per assignment. v1 assigns per user
-- (personal tenants); tenant-level rows are ready for org/contract plans.
CREATE TABLE plan_assignments (
    id          text PRIMARY KEY,                   -- pas_01…
    plan_id     text NOT NULL REFERENCES plans(id),
    user_id     text REFERENCES users(id),
    tenant_id   text REFERENCES tenants(id),
    assigned_at timestamptz NOT NULL DEFAULT now(),
    assigned_by text NOT NULL DEFAULT 'system',     -- 'system' | 'admin' | later: billing
    note        text NOT NULL DEFAULT '',
    CHECK (num_nonnulls(user_id, tenant_id) = 1)
);
CREATE UNIQUE INDEX idx_assign_user   ON plan_assignments (user_id)   WHERE user_id   IS NOT NULL;
CREATE UNIQUE INDEX idx_assign_tenant ON plan_assignments (tenant_id) WHERE tenant_id IS NOT NULL;

-- Append-only. UPDATE/DELETE are blocked by trigger, not convention.
CREATE TABLE audit_events (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    occurred_at  timestamptz NOT NULL DEFAULT now(),
    actor_type   text NOT NULL CHECK (actor_type IN ('device','user','admin','system')),
    actor_id     text NOT NULL DEFAULT '',
    action       text NOT NULL,                     -- e.g. device.register, device.claim
    subject_type text NOT NULL,                     -- device | tenant | user | plan
    subject_id   text NOT NULL,
    tenant_id    text,                              -- NULL for anonymous-device events
    details      jsonb NOT NULL DEFAULT '{}'::jsonb -- NEVER tokens/keys/nonces
);
CREATE INDEX idx_audit_time    ON audit_events (occurred_at);
CREATE INDEX idx_audit_subject ON audit_events (subject_type, subject_id);

CREATE FUNCTION audit_events_append_only() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'audit_events is append-only'; END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_audit_append_only
    BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION audit_events_append_only();

-- Seed the four plans from the docs/plans.md matrix (values in
-- findings.md §3). Example row:
INSERT INTO plans (id, display_name, limits, capabilities) VALUES
('free', 'Basic',
 '{"agents":4,"sandboxes":1,"teams":1,"agents_per_team":2,"mcp_servers":5,
   "provider_connections_per_type":1,"cron_definitions":4,
   "cron_concurrent_runs":1,"cron_runs_per_day":10,"cron_runs_per_month":200,
   "telemetry_events_per_day":100000,"telemetry_retention_days":7}',
 '{"managed_telemetry":true,"trace_explorer":true,"usage_dashboards":true,
   "rbac":false,"sso":false,"audit_export":false}');
-- …plus pro / promax / enterprise rows with the matrix values
-- (enterprise numeric limits seed to -1 except telemetry_retention_days=365).
```

`0001_init.down.sql` drops everything in reverse order (trigger, function,
tables). Down migrations exist for dev hygiene only; production only rolls
forward.

Deliberately **absent** from v1 (deferred, additive later): the
`quota_reservations` table (`docs/enterprise-extension.md` reservation
fields), usage tables (E02: `usage_session_snapshots`, `usage_rollups_daily`),
command journal (E03). Nothing in v1 blocks them.

## Endpoints

All bodies are JSON. Errors use one envelope:
`{"error": {"code": "invalid_signature", "message": "…"}}` with stable codes.
Auth: `Authorization: Bearer <device token>` for device-authenticated calls;
`Authorization: Bearer <user token>` (resolved via the gRPC auth seam) for
user calls; `Authorization: Bearer <admin token>` for `/admin/v1/*`.

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET  | `/healthz` | none | process liveness |
| GET  | `/readyz` | none | DB reachable + migrations current |
| POST | `/device/v1/register` | none | Ed25519 pubkey → enroll challenge |
| POST | `/device/v1/prove` | none | challenge signature → device_id + token |
| POST | `/device/v1/refresh` | none (proof-of-possession) | new challenge, then new token via `prove` |
| POST | `/device/v1/claim` | user bearer + device token | claim anonymous device; key unchanged |
| POST | `/device/v1/revoke` | admin bearer, or owning user bearer, or device token (self-unpair) | revoke device |
| GET  | `/entitlements/v1/current` | device token or user bearer | entitlement document (ETag/304) |
| GET  | `/admin/v1/devices` | admin bearer | fleet list: status + last_seen |
| GET  | `/admin/v1/tenants` | admin bearer | tenant list |

### `POST /device/v1/register`

Request (public key base64, raw 32 bytes; optional metadata):

```json
{"public_key": "hSDeu0…base64…=", "name": "kim-laptop", "runtime_version": "1.4.2"}
```

Response `200` (idempotent per pubkey: re-registering an existing key issues
a fresh challenge for the same device row; a **revoked** key gets `403
device_revoked`):

```json
{"challenge_id": "chl_01J…", "nonce": "qk3v…base64…=", "expires_at": "2026-07-25T10:00:30Z"}
```

### `POST /device/v1/prove`

Request — `signature` = Ed25519 over the exact decoded nonce bytes:

```json
{"challenge_id": "chl_01J…", "signature": "Mm9x…base64…="}
```

Response `200` (single-use challenge; second call → `409 challenge_consumed`;
expired → `410 challenge_expired`; bad sig → `401 invalid_signature`):

```json
{
  "device_id": "dev_01J…",
  "token": "b4e_dt_9f2…opaque…",
  "expires_at": "2026-07-25T10:30:00Z",
  "status": "enrolled"
}
```

### `POST /device/v1/refresh`

Two-phase, same proof machinery (see token lifecycle below). Phase 1 — no
signature, just the device id:

```json
{"device_id": "dev_01J…"}
```

→ `200 {"challenge_id": "chl_01K…", "nonce": "…", "expires_at": "…"}` with
`purpose='refresh'`. Phase 2 is a normal `POST /device/v1/prove` with that
challenge → new token; the previous token is revoked on success (rotation).
A revoked device gets `403 device_revoked` at phase 1 **and** phase 2.

### `POST /device/v1/claim`

Headers: `Authorization: Bearer <user token>` (principal resolved through
`authseam`), `X-Device-Token: <live device token>` — presenting both proves
the caller is a signed-in user operating the device's own runtime
(findings.md §8.5 tracks the out-of-band claim-code variant). Body:

```json
{"device_id": "dev_01J…", "name": "kim-laptop"}
```

Response `200` — key unchanged, ownership mapped:

```json
{"device_id": "dev_01J…", "status": "claimed", "tenant_id": "ten_01J…", "user_id": "usr_01J…"}
```

Errors: `409 already_claimed` (by a different user; same-user re-claim is a
no-op `200`), `403 device_revoked`, `401` on either credential.

### `POST /device/v1/revoke`

```json
{"device_id": "dev_01J…", "reason": "laptop lost"}
```

→ `200 {"device_id": "dev_01J…", "status": "revoked"}`. Effects: status →
`revoked`, all `device_tokens` rows for the device get `revoked_at`, open
challenges consumed, audit event written. Refresh and prove now fail
permanently (`device-command-v1`: "Revocation … rejects token refresh").
Re-registering the same public key stays `403` — a revoked key is dead.

### `GET /entitlements/v1/current`

Auth: device token (subject = device; plan via owner, or `free` if
anonymous) or user bearer (subject = user). Supports `If-None-Match` → `304`.
Response `200`, headers `ETag: "free-7"` (plan id + plans.revision):

```json
{
  "version": 1,
  "edition": "cloud",
  "plan_id": "free",
  "subject": {"device_id": "dev_01J…", "user_id": "", "tenant_id": ""},
  "issued_at": "2026-07-25T10:00:00Z",
  "expires_at": "2026-07-25T11:00:00Z",
  "revision": 7,
  "limits": {
    "agents": 4, "sandboxes": 1, "teams": 1, "agents_per_team": 2,
    "mcp_servers": 5, "provider_connections_per_type": 1,
    "cron_definitions": 4, "cron_concurrent_runs": 1,
    "cron_runs_per_day": 10, "cron_runs_per_month": 200,
    "telemetry_events_per_day": 100000, "telemetry_retention_days": 7
  },
  "capabilities": {
    "managed_telemetry": true, "trace_explorer": true,
    "usage_dashboards": true, "rbac": false, "sso": false, "audit_export": false
  },
  "hardware_class": "standard"
}
```

`edition` is `"cloud"` for control-plane-issued documents ("verify" — the
`entitlements-v1` contract names the field but not its vocabulary; keep the
value stable once chosen). For an `enterprise`-plan subject the numeric
limits serialize as `-1` (unlimited) except `telemetry_retention_days: 365`.
`expires_at` = issued + 1h: clients re-fetch, and the OSS side treats a
missing/expired document as "no enterprise features", never as a local
restriction (`AGENTS.md`).

### `GET /admin/v1/devices`

Query: `status=` filter, `limit`/`offset` paging. Response:

```json
{
  "devices": [
    {"device_id": "dev_01J…", "name": "kim-laptop", "status": "claimed",
     "tenant_id": "ten_01J…", "user_id": "usr_01J…",
     "runtime_version": "1.4.2", "last_seen_at": "2026-07-25T09:59:12Z",
     "created_at": "2026-07-20T08:00:00Z"}
  ],
  "total": 1
}
```

`last_seen_at` is updated (throttled, ≥60s between writes) on every
authenticated device call. Public keys and token data are **never** returned.

### `GET /admin/v1/tenants`

```json
{"tenants": [{"tenant_id": "ten_01J…", "kind": "personal", "display_name": "duykhanh…",
              "users": 1, "devices": 2, "plan_id": "free",
              "created_at": "2026-07-20T08:00:00Z"}], "total": 1}
```

## Ed25519 challenge flow (sequence)

```
 device (OSS runtime, Python)                 enterprise-api (Go)            PostgreSQL
──────────────────────────────                ───────────────────            ──────────
 keypair := ed25519.generate()
 (private key stays in DATA_DIR/device/)
        │
        │ POST /device/v1/register {public_key}
        ├────────────────────────────────────────►│
        │                                         │ upsert devices row (status=enrolled)
        │                                         ├──────────────────────────►│
        │                                         │ nonce := rand(32)
        │                                         │ insert device_challenges  │
        │                                         │  (purpose=enroll, ttl 60s)│
        │◄────────────────────────────────────────┤
        │   {challenge_id, nonce, expires_at}     │
        │
 sig := ed25519.sign(private_key, nonce)
        │
        │ POST /device/v1/prove {challenge_id, signature}
        ├────────────────────────────────────────►│
        │                                         │ load challenge (unexpired,
        │                                         │  unconsumed) + device pubkey
        │                                         │ ed25519.Verify(pubkey, nonce, sig)?
        │                                         │ consume challenge (single use)
        │                                         │ raw := rand(32); store sha256(raw)
        │                                         │  in device_tokens (ttl 30m)
        │                                         │ audit: device.enroll
        │◄────────────────────────────────────────┤
        │   {device_id, token, expires_at}        │   (raw token returned once,
        │                                         │    never stored, never logged)
        │
        │ …later, before expiry…
        │ POST /device/v1/refresh {device_id}     │
        ├────────────────────────────────────────►│ insert challenge (purpose=refresh)
        │◄────────────────────────────────────────┤ {challenge_id, nonce}
        │ POST /device/v1/prove {challenge_id, signature}
        ├────────────────────────────────────────►│ verify → mint new token,
        │◄────────────────────────────────────────┤ revoke previous token (rotation)
        │
        │ …user signs in inside their runtime…
        │ POST /device/v1/claim  Bearer:user  X-Device-Token:dt
        ├────────────────────────────────────────►│ authseam.Resolve(bearer) ──gRPC──► auth svc
        │                                         │ ensure user row + personal tenant
        │                                         │ device.status=claimed (key UNCHANGED)
        │◄────────────────────────────────────────┤ audit: device.claim
        │
        │ …admin or owner…
        │ POST /device/v1/revoke {device_id}      │ status=revoked, tokens revoked
        │                                         │ audit: device.revoke
        │ POST /device/v1/refresh ────────────────► 403 device_revoked   ✗ forever
```

## Token lifecycle

- **Format**: opaque, `b4e_dt_` prefix + 32 random bytes base64url (decision
  A in [approaches.md](approaches.md)). Not a JWT; carries no claims; all
  state is server-side.
- **At rest**: only `sha256(raw)` in `device_tokens.token_hash`. Lookup is
  by hash (unique index); constant-time compare is inherent (hash lookup).
  The raw token exists exactly once, in the `prove` response body.
- **TTLs** (env-tunable, defaults): challenge **60 s**; device token
  **30 min** (`DEVICE_TOKEN_TTL`); entitlement document 1 h.
- **Rotation**: every successful refresh mints a new token and sets
  `revoked_at` on the token it replaces. At most one live token per device
  in steady state (a short overlap window is acceptable; enforcement is
  "revoke previous on mint", not a uniqueness constraint).
- **Revocation**: device revoke marks all its tokens `revoked_at`; token
  validation checks `revoked_at IS NULL AND expires_at > now()` **and**
  `devices.status <> 'revoked'` in one join — a revoked device's tokens die
  even if a token row was missed.
- **Cleanup**: expired challenges and tokens are deleted opportunistically
  (piggybacked on writes) — no background scheduler needed in E01.
- **Never logged**: raw tokens, token hashes, nonces, signatures, public
  keys. The logging package exposes `logging.Redacted(...)` helpers and the
  test suite greps captured log output for token prefixes
  (see [validation.md](validation.md)).

## Config (env)

| Var | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | — (required) | `postgres://…` DSN, PostgreSQL 16 |
| `LISTEN_ADDR` | `:8080` | HTTP bind (compose publishes `8710`, "verify") |
| `ADMIN_TOKEN` | — (required in prod) | static admin bearer (decision D) |
| `AUTH_GRPC_ADDR` | empty | auth service address; empty ⇒ resolver disabled |
| `AUTH_MODE` | `grpc` | `grpc` \| `static` (dev only; static subject map) |
| `DEVICE_TOKEN_TTL` | `30m` | device token lifetime |
| `CHALLENGE_TTL` | `60s` | challenge lifetime |
| `LOG_LEVEL` | `info` | slog level |

Missing required config fails startup with a clear error. The binary runs
migrations on start (`MIGRATE_ON_START=true` default) or via
`enterprise-api migrate up|down|status` subcommands.

## What the OSS repo sees

Nothing new in E01. The OSS runtime keeps working with `ENTERPRISE_API_URL`
unset; when a later plan (E02) adds the Python device connector it will call
exactly the endpoints above. An enterprise outage never restricts local
features — every endpoint here is additive, and no OSS code path in this
plan depends on it.
