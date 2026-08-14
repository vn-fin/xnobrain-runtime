# E02 — Architecture

The end-to-end design of central usage collection. The shape is fixed by
[`plans/enterprise/README.md`](../README.md) § "The core architecture"; this
document supplies every detail an implementer needs. Verified source facts are
in [findings.md](findings.md); decisions and rejected alternatives in
[approaches.md](approaches.md); ordered steps in
[implementation.md](implementation.md).

## 1. End-to-end diagram

```
 per-user deployment (Incus container / user PC, behind NAT)
┌─────────────────────────────────────────────────────────────────────┐
│  OSS runtime — one FastAPI/Hermes process (:8642)                   │
│                                                                     │
│  profiles/<agent>/state.db  (sessions: cumulative token/cost/count  │
│        │                     columns — findings.md §1)              │
│        │ ?mode=ro, derived last_activity_at > watermark,            │
│        │ mtime-skip per agent (plan-009 pattern)                    │
│        ▼                                                            │
│  integrations/enterprise_usage.py     SNAPSHOT READER               │
│    allowlist serializer + HMAC(salt) hashing of agent/session ids   │
│        │ list[Snapshot]                                             │
│        ▼                                                            │
│  services/usage_reporter.py           OUTBOX + PUSHER               │
│    DATA_DIR/enterprise/                                             │
│      reporter_salt          (32 random bytes, never leaves device)  │
│      reporter_cursor.json   (per-agent watermarks, mtime cache,     │
│                              dropped_batches counter)               │
│      outbox/batch-*.jsonl   (atomic, self-contained, bounded total) │
│      outbox/dead/           (terminally rejected batches, bounded)  │
│        │ drain loop: POST batches, backoff+jitter,                  │
│        │ delete file + advance cursor ONLY on server ack            │
│        ▼                                                            │
│  device identity (E01, device-command-v1)                           │
│    DATA_DIR/device/  Ed25519 key → device_id + short-lived token    │
└──────────────┬──────────────────────────────────────────────────────┘
               │  HTTPS 443, OUTBOUND ONLY
               │  POST {ENTERPRISE_API_URL}/ingest/v1/usage
               │  Authorization: Bearer <device token>
               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  xnobrain-enterprise (Go)                                          │
│                                                                     │
│  internal/ingest  — auth device token → (device, user?, tenant?)    │
│    validate: schema, non-negative, size (413), rate (429),          │
│    stale-batch watermark (409), per-snapshot monotonicity           │
│        │ one transaction per batch                                  │
│        ▼                                                            │
│  PostgreSQL — THE ONE DATABASE (billing source of truth)            │
│    usage_session_snapshots   UPSERT latest per                      │
│                              (tenant, device, agent_hash,           │
│                               session_hash)                         │
│    usage_rollups_daily       incremental delta rollup per           │
│                              (tenant, user, device, day, model)     │
│    usage_ingest_audit        regressions, drops, skew flags         │
│    devices / users / tenants (E01)                                  │
│        │                                                            │
│        ▼                                                            │
│  GET /admin/v1/usage (+ /export.csv)  → admin dashboard / billing   │
└─────────────────────────────────────────────────────────────────────┘

  OUT OF SCOPE, separate path: runtime → OTel → ClickHouse traces
  (ops telemetry; never accounting state — entitlements-v1)
```

## 2. Reporter internals (OSS, Python)

### 2.1 Activation gate

The reporter task starts inside the existing lifespan wrapper in
[`xnobrain/app.py`](../../../xnobrain/app.py) (exactly like the kanban
dispatcher block, lines ~32–53) but immediately parks (returns / sleeps
forever re-checking cheaply) unless **all** of:

1. `ENTERPRISE_API_URL` is set and non-empty.
2. `XNOBRAIN_USAGE_DISABLE` is not `"1"` (operator escape hatch).
3. Device identity is present and enrolled (E01 seam:
   `load_device_identity()` finds `DATA_DIR/device/` credentials — seam
   definition in [implementation.md](implementation.md) Phase 1).

When dormant: zero network calls, zero files created, zero `state.db` opens.

### 2.2 Files under `DATA_DIR/enterprise/`

All writes use the repo's atomic idiom: write temp file in the same
directory, fsync, `os.replace`.

**`reporter_salt`** — 32 random bytes (`secrets.token_bytes(32)`), created
once on first activation, mode 0600. Never transmitted, never logged. Basis of
the hashing spec (§5).

**`reporter_cursor.json`** — the durable cursor:

```json
{
  "version": 1,
  "agents": {
    "<agent_id>": {
      "watermark": 1753372800.512,
      "mtime_ns": 1753372801123456789
    }
  },
  "dropped_batches": 0,
  "last_ack_at": "2026-07-25T09:12:03Z",
  "last_error": ""
}
```

- `watermark` — highest derived `last_activity_at` (epoch seconds, device
  clock) whose snapshot has been **acked by the server**. Scan selects
  sessions with `last_activity_at > watermark`.
- `mtime_ns` — `state.db` `st_mtime_ns` at last completed scan; if unchanged,
  the agent is skipped without opening SQLite (plan-009 mtime-skip made
  durable).
- `dropped_batches` — the single local counter required by `05-telemetry.md`
  when the bounded spool evicts (§2.4).
- Plaintext `agent_id` keys are fine **here**: this file never leaves the
  device.

**`outbox/batch-<epoch_ms>-<seq>.jsonl`** — one spooled batch per file,
self-contained. Line 1 is a local header (never transmitted); lines 2..N are
wire-ready snapshot objects (§4 contract), one per line:

```
{"kind":"header","version":1,"batch_id":"<hex sha256>","created_at":"2026-07-25T09:00:00Z","agent_watermarks":{"<agent_id>":1753372800.512},"agent_mtimes":{"<agent_id>":1753372801123456789},"snapshot_count":42}
{"agent_hash":"…","session_hash":"…","source":"api", …}
{"agent_hash":"…", …}
```

- `batch_id = sha256(device_id + "\n" + canonical_json(snapshot_lines))` hex
  — content-addressed, so a rebuilt identical batch has the same id
  (idempotency key, §4).
- `agent_watermarks` / `agent_mtimes` — what the cursor should advance to
  when **this** batch is acked (max-merged; §2.5).
- Header keeps plaintext `agent_id` locally so cursor advance never needs to
  reverse a hash; the pusher transmits **only** lines 2..N inside the
  envelope. The redaction test serializes the actual wire body, not the file.

**`outbox/dead/`** — batches the server terminally rejected (409 stale/400
malformed). Kept for diagnosis, bounded to 8 files, oldest deleted.

### 2.3 Scan loop (producer)

Runs every `XNOBRAIN_USAGE_REPORT_INTERVAL` seconds (default 60) with ±20%
jitter, entirely in `asyncio.to_thread` for the SQLite work, concurrency
bounded by a semaphore (plan-009 uses 8; reporter uses 2 — it is background
work and must never compete with interactive reads):

```
for each agent in agents.list_agents()["agents"]:
    db = profile_path / "state.db"
    stat = db.stat()                      # missing → skip
    if stat.st_mtime_ns == cursor.mtime_ns[agent]: continue   # mtime-skip
    rows = SELECT <allowlist columns>, derived last_activity_at
           FROM sessions s
           WHERE last_activity_at > :watermark
           ORDER BY last_activity_at ASC
           LIMIT XNOBRAIN_USAGE_BATCH_MAX_SNAPSHOTS          # ?mode=ro
    build Snapshot objects (allowlist serializer + HMAC hashing, §5)
    collect per-agent candidate watermark = max(last_activity_at of rows)
    note stat.st_mtime_ns taken BEFORE the read (safe: a write during the
    read makes next cycle re-scan; upsert makes the overlap harmless)
if any snapshots:
    append batch file atomically; if agent had more rows than LIMIT,
    loop again next cycle (watermark advances only on ack, but the batch
    header records the candidate watermark so catch-up converges)
enforce spool bound (§2.4)
```

Any `sqlite3.Error` / `OSError` on one agent skips that agent for the cycle
(plan-009 degrade posture) and records `last_error` in the cursor file.
Nothing propagates to the app.

### 2.4 Spool bound

Total bytes under `outbox/` (excluding `dead/`) capped by
`XNOBRAIN_USAGE_OUTBOX_MAX_BYTES` (default 64 MiB — years of snapshots at
realistic volume; a snapshot line is ~400 bytes). Before appending a new
batch: while `total + new > cap`, delete the **oldest** batch file and
increment `dropped_batches` (persisted in the cursor file). Per
`05-telemetry.md`: drop oldest, one local counter, never block, never grow
unbounded. Dropping old batches is usually lossless because a newer snapshot
of the same session supersedes the old one (cumulative counters); see
[findings.md](findings.md) risk 5.

### 2.5 Drain loop (pusher)

Runs every `XNOBRAIN_USAGE_PUSH_INTERVAL` seconds (default 300) with ±20%
jitter. State machine:

```
IDLE ──files exist──► SENDING ──2xx ack──► ACKED ──► IDLE
 ▲                        │                  │
 │                        │ network error /   └─ delete batch file;
 │                        │ 5xx / 429            cursor.agents[a].watermark =
 │                        ▼                        max(old, header.agent_watermarks[a]);
 │                    BACKOFF (full jitter,        cursor.agents[a].mtime_ns =
 │                     1s → 2s → … cap 15 min;       header.agent_mtimes[a];
 │                     429 honors Retry-After)     atomic cursor write
 │                        │
 └────────────────────────┘
SENDING ──400/409/413 (terminal)──► move file to outbox/dead/ (413: split
                                    and respool halves first, then dead only
                                    if a single snapshot is oversize)
SENDING ──401──► ask identity seam to refresh token once; if still 401
                 (revoked device), park the pusher: keep spooling under the
                 bound, retry auth hourly. Local operation unaffected.
```

Invariants:

- Files are sent **oldest first** (so server-side stale-batch detection is
  the exception, not the rule).
- A batch file is deleted and the cursor advanced **only after a 2xx ack**
  whose body parses. Crash between server-commit and local delete →
  re-send → upsert no-op → ack → delete. At-least-once + idempotent upsert =
  exactly-once accounting.
- Cursor advance is a **monotonic max-merge** per agent, so out-of-order or
  duplicate acks cannot move a watermark backwards.
- The HTTP client timeout is finite (10 s connect / 30 s total) and the whole
  loop is one background task; local request latency is untouched (proved in
  [validation.md](validation.md) §8).

## 3. Ingest sequence (server, Go) — including retry + dedup

```
reporter                        ingest handler                    PostgreSQL
   │  POST /ingest/v1/usage         │                                 │
   │  Bearer <device token>         │                                 │
   │  {version, device_id,          │                                 │
   │   batch_id, snapshots[]}       │                                 │
   ├───────────────────────────────►│                                 │
   │                                │ auth token → device row;        │
   │                                │ token.device_id == body.device_id else 401
   │                                │ revoked device → 401            │
   │                                │ body > 1 MiB or > 500 snaps → 413
   │                                │ rate limiter per device → 429   │
   │                                │ schema + non-negative check → 400
   │                                │ BEGIN                           │
   │                                ├────────────────────────────────►│
   │                                │ resolve tenant_id/user_id from  │
   │                                │ device claim (unclaimed: park,  │
   │                                │ findings.md §7.4)               │
   │                                │ stale replay guard: if          │
   │                                │ max(reporter_watermark) <       │
   │                                │ device.acked_watermark - 7d     │
   │                                │ → ROLLBACK, 409                 │
   │                                │ for each snapshot:              │
   │                                │   SELECT stored FOR UPDATE      │
   │                                │   all counters >= stored?       │
   │                                │     yes → UPSERT + rollup delta │
   │                                │     equal (no change) → count   │
   │                                │       as duplicate, no-op       │
   │                                │     any lower → skip row, write │
   │                                │       usage_ingest_audit, add   │
   │                                │       to rejected[]             │
   │                                │ update device.acked_watermark = │
   │                                │   greatest(old, max(batch))     │
   │                                │ COMMIT                          │
   │                                │◄────────────────────────────────┤
   │  200 {acked_watermark,         │                                 │
   │   accepted, duplicates,        │                                 │
   │   rejected[]}                  │                                 │
   │◄───────────────────────────────┤                                 │
   │  delete batch file,            │
   │  advance cursor                │
   │
   │  ...network dies before response arrives...
   │  RETRY same batch  ───────────►│ same flow; every UPSERT compares
   │                                │ equal → all duplicates → same
   │◄── 200 (identical totals) ─────┤ 200 body. No double counting.
```

Dedup requires no batch-id memory: idempotency is a property of the
snapshot-upsert itself. `batch_id` is stored in the audit log for tracing and
may be used for a short-window (24 h) fast-path skip, but correctness never
depends on it.

## 4. Contract draft — `usage-ingest-v1`

The following is the **complete draft** of the new public contract. Phase 0
of [implementation.md](implementation.md) copies it verbatim (minus this
sentence) to `docs/contracts/usage-ingest-v1.md`.

---

# Usage ingest protocol v1

Status: design contract for E02 (OSS usage reporter ↔ enterprise ingest API).
Any incompatible change creates `v2`; do not silently reinterpret fields.

## Purpose and boundary

A device (single-user OSS deployment) pushes **content-free session usage
snapshots** — token counters, cost figures, call/message counts, timestamps,
model and provider labels — to the enterprise control plane, which stores
them in PostgreSQL as the billing source of truth. This channel is
accounting, not telemetry: OTel traces/metrics and HTTP headers never serve
as accounting state (`entitlements-v1`). The `05-telemetry.md` forbidden list
applies to every field: no prompts, responses, titles, memories, skills, tool
arguments, file paths, credentials, or user-entered names may appear in any
request on this channel.

## Transport and authentication

Outbound-only HTTPS from the device (device-command-v1 transport rules; no
inbound listener). Endpoint:

```
POST {ENTERPRISE_API_URL}/ingest/v1/usage
Authorization: Bearer <short-lived device access token>
Content-Type: application/json
```

The token's device identity must match `device_id` in the body. Revoked
devices are rejected. TLS verification is never disabled in production.

## Request envelope

```json
{
  "version": 1,
  "device_id": "dev_01H…",
  "batch_id": "9f2c…64 hex sha256…",
  "created_at": "2026-07-25T09:00:00Z",
  "snapshots": [ { …snapshot… } ]
}
```

- `version` — integer, must be `1`.
- `batch_id` — hex SHA-256 of `device_id + "\n" +` the canonical JSON
  serialization of the snapshot array. Idempotency key for audit/tracing;
  correctness relies on snapshot upsert, not on batch dedup.
- `snapshots` — 1..500 items. Bodies over 1 MiB or over 500 snapshots are
  rejected with 413; the client must split.

## Snapshot object

| Field | Type | Required | Meaning |
|---|---|---|---|
| `agent_hash` | string, 64 hex | yes | HMAC-SHA256 of the agent id (Hashing rules) |
| `session_hash` | string, 64 hex | yes | HMAC-SHA256 of the session id |
| `source` | string ≤ 32 | yes | session origin tag (e.g. `api`); fixed vocabulary, never user text |
| `model` | string ≤ 128 | yes | model name; `""` allowed |
| `billing_provider` | string ≤ 64 | yes | provider/routing label (e.g. `anthropic`, `nine-router`); never a base URL or account identity |
| `started_at` | number (epoch s) | yes | client clock |
| `last_activity_at` | number (epoch s) | yes | client clock; derived max of session end / newest message timestamp |
| `input_tokens` | integer ≥ 0 | yes | cumulative for the session |
| `output_tokens` | integer ≥ 0 | yes | cumulative |
| `cache_read_tokens` | integer ≥ 0 | yes | cumulative |
| `cache_write_tokens` | integer ≥ 0 | yes | cumulative |
| `reasoning_tokens` | integer ≥ 0 | yes | cumulative |
| `estimated_cost_usd` | number ≥ 0 | yes | cumulative |
| `actual_cost_usd` | number ≥ 0 | yes | cumulative; `0` when unknown |
| `cost_status` | string ≤ 32 | yes | status tag from the runtime; `""` allowed |
| `api_call_count` | integer ≥ 0 | yes | cumulative |
| `message_count` | integer ≥ 0 | yes | cumulative |
| `reporter_watermark` | number (epoch s) | yes | the reporter's scan high-water mark when this snapshot was built |

**Field allowlist is closed.** Servers reject envelopes containing unknown
snapshot fields with 400 (`unknown_field`) — this is deliberate: an
accidental widening of the client serializer must fail loudly, not be stored.
Explicitly and permanently excluded even though they exist in the local
schema: `title`, `system_prompt`, `model_config`, `billing_base_url`,
`user_id`, and everything in the `messages` table except `timestamp`
(used only locally to derive `last_activity_at`).

## Hashing rules

- Each device generates a random 32-byte salt once
  (`DATA_DIR/enterprise/reporter_salt`), which never leaves the device.
- `agent_hash = hex(HMAC_SHA256(salt, "agent:" + agent_id))`
- `session_hash = hex(HMAC_SHA256(salt, "session:" + session_id))`
- Properties: stable per device (the server can group and upsert), unlinkable
  across devices, and irreversible by the server (no dictionary attack
  without the salt). The domain-separation prefixes prevent agent/session
  collisions.
- Consequence: the server cannot display real agent names. A device MAY
  opt in (explicit user action; `XNOBRAIN_USAGE_SHARE_LABELS=1`) to publish
  a label registry mapping `agent_hash → display_name` via
  `POST /ingest/v1/labels` (same envelope/auth rules; display names are the
  only permitted user-entered strings on this channel and are opt-in
  precisely because `05-telemetry.md` forbids user-entered names by default).

## Idempotency and watermark semantics

- Delivery is **at-least-once**; the server upserts the latest state per
  unique `(tenant_id, device_id, agent_hash, session_hash)`. Re-delivery of
  any batch, any number of times, in any order, must produce identical stored
  totals.
- The server maintains `acked_watermark` per device = the greatest
  `reporter_watermark` it has committed, and returns it in every success
  response. The client advances its durable cursor only from acked batches.
- The server stamps `received_at` on every upsert. Billing-day attribution
  uses server receive time; client timestamps are retained for display.

## Monotonicity rule

All counters and cost fields are cumulative per session and must never
decrease. On upsert the server compares field-by-field with the stored row:

- all incoming ≥ stored, at least one greater → update (accepted);
- all equal → duplicate (no-op, counted in `duplicates`);
- any incoming < stored → the snapshot is rejected as suspect
  (`counter_regression`), the stored row is untouched, and an audit record is
  written. Local session deletion or database restore does not retract
  centrally recorded usage.

## Success response

```json
{
  "version": 1,
  "acked_watermark": 1753372800.512,
  "accepted": 40,
  "duplicates": 2,
  "rejected": [
    {"session_hash": "…", "code": "counter_regression"}
  ]
}
```

`accepted + duplicates + len(rejected) == len(snapshots)`. A response with
rejections is still a success (2xx); the client must not resend rejected
snapshots at the same or lower counters.

## Errors

| HTTP | Code | Meaning | Client behavior |
|---|---|---|---|
| 400 | `invalid_envelope`, `unknown_field`, `negative_counter` | malformed request | move batch to dead spool; do not retry |
| 401 | `device_unauthorized` | missing/expired/revoked device token, or token/device mismatch | refresh token once; if still 401, park pushing, keep spooling bounded, retry auth periodically |
| 409 | `batch_stale` | entire batch's max `reporter_watermark` is older than the device's acked watermark by more than the staleness window (default 7 days) — a replay of long-superseded data | move batch to dead spool; audited server-side |
| 413 | `batch_too_large` | body > 1 MiB or > 500 snapshots | split batch and resend halves |
| 429 | `rate_limited` | per-device rate exceeded | back off per `Retry-After`, then resume |
| 5xx | — | server fault | exponential backoff with jitter, retry same batch unchanged |

Per-snapshot regressions inside an otherwise-valid batch are **not** an HTTP
error; they are reported in `rejected[]` (so one bad session cannot wedge a
device's spool).

## Versioning

`version: 1` in every envelope and response. Additive-compatible changes
(new optional response fields) stay v1. Any change to the snapshot allowlist,
hashing rules, key semantics, or error meanings is incompatible and creates
`POST /ingest/v2/usage` with `version: 2`; v1 remains served during a
deprecation window. Unknown *response* fields are ignored by older clients;
unknown *request* fields are rejected (see allowlist rationale).

## Security acceptance tests

- Redaction: sentinel values planted in `title`, `system_prompt`,
  `model_config`, `billing_base_url`, and message content never appear in any
  serialized request body.
- Same batch sent 10× yields identical stored totals and rollups.
- Wrong-device token, revoked device, oversize body, counter regression, and
  stale replay are rejected with the codes above.
- Logs and spans on both sides never contain the device token, salt, or any
  forbidden field.

---

*(end of contract draft)*

## 5. Hashing spec (implementation notes beyond the contract)

- Salt file: `DATA_DIR/enterprise/reporter_salt`, `secrets.token_bytes(32)`,
  written atomically, mode 0600, created lazily on first activation.
- Python: `hmac.new(salt, f"agent:{agent_id}".encode(), hashlib.sha256).hexdigest()`.
- The hash is computed at snapshot-build time; the outbox stores only hashed
  ids on wire lines (plaintext agent ids appear only in the local batch
  header and cursor file, which never leave the device — §2.2).
- Tension acknowledged: admin dashboards showing `a3f9…` are unfriendly. The
  resolution is the **opt-in label registry** (contract § Hashing rules;
  decision record in [approaches.md](approaches.md) §B). Hash-by-default is
  non-negotiable; labels are a user choice.

## 6. PostgreSQL schema (complete SQL)

Raw SQL migrations, no ORM (`docs/enterprise-extension.md`). `devices`,
`users`, `tenants` come from E01; column types below assume `devices.id
TEXT`, `tenants.id BIGINT`, `users.id BIGINT` — **verify in Phase 0 against
E01's actual migrations** and adjust FK types, not semantics.

```sql
-- The upsert target: latest known state per session.
CREATE TABLE usage_session_snapshots (
    tenant_id          BIGINT      NOT NULL REFERENCES tenants (id),
    device_id          TEXT        NOT NULL REFERENCES devices (id),
    user_id            BIGINT               REFERENCES users (id),  -- NULL until device claimed
    agent_hash         TEXT        NOT NULL CHECK (agent_hash   ~ '^[0-9a-f]{64}$'),
    session_hash       TEXT        NOT NULL CHECK (session_hash ~ '^[0-9a-f]{64}$'),
    source             TEXT        NOT NULL DEFAULT '',
    model              TEXT        NOT NULL DEFAULT '',
    billing_provider   TEXT        NOT NULL DEFAULT '',
    started_at         TIMESTAMPTZ NOT NULL,               -- client clock, display only
    last_activity_at   TIMESTAMPTZ NOT NULL,               -- client clock, display only
    input_tokens       BIGINT      NOT NULL CHECK (input_tokens       >= 0),
    output_tokens      BIGINT      NOT NULL CHECK (output_tokens      >= 0),
    cache_read_tokens  BIGINT      NOT NULL CHECK (cache_read_tokens  >= 0),
    cache_write_tokens BIGINT      NOT NULL CHECK (cache_write_tokens >= 0),
    reasoning_tokens   BIGINT      NOT NULL CHECK (reasoning_tokens   >= 0),
    estimated_cost_usd NUMERIC(14,6) NOT NULL DEFAULT 0 CHECK (estimated_cost_usd >= 0),
    actual_cost_usd    NUMERIC(14,6) NOT NULL DEFAULT 0 CHECK (actual_cost_usd    >= 0),
    cost_status        TEXT        NOT NULL DEFAULT '',
    api_call_count     BIGINT      NOT NULL CHECK (api_call_count >= 0),
    message_count      BIGINT      NOT NULL CHECK (message_count  >= 0),
    reporter_watermark TIMESTAMPTZ NOT NULL,
    first_received_at  TIMESTAMPTZ NOT NULL DEFAULT now(), -- server clock
    last_received_at   TIMESTAMPTZ NOT NULL DEFAULT now(), -- server clock
    -- Exactly-once accounting hangs on this key:
    CONSTRAINT usage_session_snapshots_pk
        PRIMARY KEY (tenant_id, device_id, agent_hash, session_hash)
);
CREATE INDEX usage_snapshots_user_time
    ON usage_session_snapshots (tenant_id, user_id, last_received_at);
CREATE INDEX usage_snapshots_model
    ON usage_session_snapshots (tenant_id, model, last_received_at);

-- Per-day accounting rows, maintained incrementally by the rollup step.
CREATE TABLE usage_rollups_daily (
    tenant_id          BIGINT  NOT NULL,
    user_id            BIGINT,                 -- NULL for unclaimed periods
    device_id          TEXT    NOT NULL,
    day                DATE    NOT NULL,       -- UTC date of SERVER receive time
    model              TEXT    NOT NULL DEFAULT '',
    input_tokens       BIGINT  NOT NULL DEFAULT 0,
    output_tokens      BIGINT  NOT NULL DEFAULT 0,
    cache_read_tokens  BIGINT  NOT NULL DEFAULT 0,
    cache_write_tokens BIGINT  NOT NULL DEFAULT 0,
    reasoning_tokens   BIGINT  NOT NULL DEFAULT 0,
    cost_est           NUMERIC(14,6) NOT NULL DEFAULT 0,
    cost_actual        NUMERIC(14,6) NOT NULL DEFAULT 0,
    sessions           BIGINT  NOT NULL DEFAULT 0,  -- sessions first seen this day
    api_calls          BIGINT  NOT NULL DEFAULT 0,
    messages           BIGINT  NOT NULL DEFAULT 0,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT usage_rollups_daily_pk
        PRIMARY KEY (tenant_id, device_id, day, model)
);
CREATE INDEX usage_rollups_user_day ON usage_rollups_daily (tenant_id, user_id, day);

-- Per-device ingest bookkeeping (acked watermark lives with the device).
ALTER TABLE devices ADD COLUMN acked_watermark TIMESTAMPTZ;

-- Suspect events: regressions, stale replays, gross clock skew.
CREATE TABLE usage_ingest_audit (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    occurred_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    tenant_id    BIGINT,
    device_id    TEXT NOT NULL,
    batch_id     TEXT NOT NULL,
    session_hash TEXT,
    code         TEXT NOT NULL,   -- counter_regression | batch_stale | clock_skew | batch_rejected
    detail       JSONB NOT NULL DEFAULT '{}'::jsonb  -- numeric before/after only; never content
);
CREATE INDEX usage_ingest_audit_device ON usage_ingest_audit (device_id, occurred_at);
```

Partitioning: start **unpartitioned** (decision + revisit threshold in
[approaches.md](approaches.md) §C — snapshots grow with session count, not
event count; rollups are tiny).

### 6.1 Upsert + incremental rollup (one transaction per batch, pseudocode)

```
BEGIN;
d := SELECT * FROM devices WHERE id = $device_id;         -- auth already done
(tenant, user) := resolve_claim(d);                        -- findings.md §7.4
IF batch_max_watermark < d.acked_watermark - interval '7 days' THEN
    ROLLBACK; RETURN 409 batch_stale;
END IF;
today := (now() AT TIME ZONE 'utc')::date;                 -- server receive day
FOR s IN snapshots LOOP
    old := SELECT * FROM usage_session_snapshots
           WHERE (tenant_id, device_id, agent_hash, session_hash) = key(s)
           FOR UPDATE;
    IF old IS NULL THEN
        INSERT snapshot row (first_received_at = now());
        delta := s counters; new_session := true;
    ELSIF any_counter(s) < same_counter(old) THEN
        INSERT usage_ingest_audit(code := 'counter_regression',
                                  detail := {field, old, new});
        rejected += s; CONTINUE;
    ELSIF all_counters_equal(s, old) THEN
        duplicates += 1; CONTINUE;                         -- retry no-op
    ELSE
        UPDATE snapshot row SET counters := s, last_received_at := now();
        delta := s counters - old counters;                -- >= 0 by the guard
        new_session := false;
    END IF;
    INSERT INTO usage_rollups_daily (tenant, user, device, today, s.model, delta,
                                     sessions := new_session ? 1 : 0)
    ON CONFLICT (tenant_id, device_id, day, model)
    DO UPDATE SET each counter = counter + EXCLUDED.counter,
                  updated_at = now();
    accepted += 1;
END LOOP;
UPDATE devices SET acked_watermark = GREATEST(coalesce(acked_watermark,'-infinity'),
                                              batch_max_watermark)
WHERE id = $device_id;
COMMIT;
RETURN 200 {acked_watermark, accepted, duplicates, rejected};
```

Why this is exactly-once for rollups too: the rollup increment is the
**server-derived delta** (new stored counters minus old stored counters),
computed inside the same transaction as the guarded upsert. A duplicate batch
produces `delta = 0` rows and touches nothing. This is not client-side delta
bookkeeping (rejected in [approaches.md](approaches.md) §A) — the client
still sends plain snapshots; the server derives accrual from its own
authoritative previous state.

Day semantics: usage accrues to the UTC day the server *received* the
progress (clock-skew rule, [findings.md](findings.md) risk 1). A session
active across several days accrues to each day's report. `sessions` counts a
session on its first-seen day only, so `SUM(sessions)` over days equals
distinct sessions.

### 6.2 Nightly reconcile job

Because per-day accrual exists only in the rollups (snapshots keep only the
latest cumulative state), reconciliation checks the **sum invariant**, not
per-day equality:

```sql
-- For every (tenant, device, model): lifetime rollup totals must equal
-- current snapshot totals (regressions excluded by construction).
SELECT r.tenant_id, r.device_id, r.model,
       r.sum_input, s.sum_input, …
FROM (SELECT tenant_id, device_id, model,
             SUM(input_tokens) sum_input, SUM(output_tokens) sum_output,
             SUM(cost_est) sum_est, SUM(cost_actual) sum_actual,
             SUM(sessions) sum_sessions, SUM(api_calls) sum_calls,
             SUM(messages) sum_messages
      FROM usage_rollups_daily GROUP BY 1,2,3) r
FULL JOIN (SELECT tenant_id, device_id, model,
             SUM(input_tokens) sum_input, SUM(output_tokens) sum_output,
             SUM(estimated_cost_usd) sum_est, SUM(actual_cost_usd) sum_actual,
             COUNT(*) sum_sessions, SUM(api_call_count) sum_calls,
             SUM(message_count) sum_messages
      FROM usage_session_snapshots GROUP BY 1,2,3) s
  USING (tenant_id, device_id, model)
WHERE r IS DISTINCT FROM s;   -- expand per column in the real job
```

Any mismatch is repaired by adjusting the **current day's** rollup row by the
difference and writing an audit record (`code = 'reconcile_adjust'`). The job
also back-fills `user_id` on both tables for devices claimed since the last
run.

## 7. Admin usage views

Read API mirroring plan 009's Grafana-style parameters
([`xnobrain/services/analytics.py`](../../../xnobrain/services/analytics.py)
uses `agent_ids/from/to/bucket`; the central version adds fleet dimensions):

```
GET /admin/v1/usage?tenant=…&user=…&device=…&model=…&from=…&to=…&bucket=day
    → { totals, by_user[], by_device[], by_model[], series[] }   (from rollups)
GET /admin/v1/usage/export.csv?tenant=…&from=…&to=…
    → one row per (user, device, day, model) — the billing export
```

`bucket ∈ hour|day|week|month`; `hour` is served from snapshots'
`last_received_at` if needed later — v1 serves day and coarser from rollups
only (hour is a fast-follow; do not block on it). Admin auth comes from E01's
principal resolution (external auth service over gRPC,
`docs/enterprise-extension.md`); this plan only defines the routes and
queries. A simple dashboard page (tiles + bars + series, visually mirroring
the local plan-009 dashboard) follows once the API is accepted — kept out of
the acceptance gate except for the numbers matching
([validation.md](validation.md) §3).

## 8. Failure modes

| Failure | Detection | Behavior | Data outcome |
|---|---|---|---|
| Enterprise server down / unreachable | connect/read timeout, 5xx | pusher backs off (full jitter, cap 15 min); scanner keeps spooling under the byte bound; local API unaffected | zero loss until spool cap; then oldest batches dropped with counter — superseded by newer snapshots on reconnect |
| Device offline 7 days | same as above | same; on reconnect drains oldest-first | complete catch-up; validated in [validation.md](validation.md) §2 |
| Crash after server commit, before local delete | batch file still present at restart | re-send; every row compares equal → `duplicates`; ack; delete | exactly-once (no change) |
| Local disk full | `OSError` on spool write | skip cycle, record `last_error` in cursor (best-effort), never raise into app | snapshots re-generated next cycle from `state.db` (source of truth is local DB, not the spool) |
| `state.db` locked/corrupt/missing | `sqlite3.Error` | skip that agent this cycle (plan-009 posture) | retried next cycle |
| Regression detected (restore-from-backup, tampering) | server counter comparison | snapshot rejected, audit row, rest of batch processed | stored totals never decrease; visible in audit |
| Stale batch replay (> 7 days behind acked watermark) | server watermark guard | 409; client moves batch to dead spool | no stale overwrite; audited |
| Revoked device | 401 on push and on token refresh | pusher parks, retries auth hourly; spool bounded; **local features unaffected** (`AGENTS.md`) | no further central data from that device |
| Oversize batch | 413 | client splits and resends halves | delivered |
| Rate limited | 429 + `Retry-After` | honor header, resume | delivered late |
| Rollup drift (bug, crash between guards) | nightly reconcile sum invariant | current-day adjustment + audit | rollups converge to snapshots |
| Device re-enrolled with wiped `DATA_DIR` | new device_id appears for same user | succession marking per [findings.md](findings.md) risk 2 | operational remediation; documented |
