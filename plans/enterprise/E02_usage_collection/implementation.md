# E02 — Implementation

Ordered, file-by-file steps in two tracks. Design rationale in
[architecture.md](architecture.md); verified facts in
[findings.md](findings.md). OSS-track phases keep `make check` green and
commit independently; enterprise-track phases do the same in
`brain4all-enterprise`. Cross-repo coupling happens only through the
`usage-ingest-v1` contract, published in Phase 0.

Track order: OSS Phase 0 → (OSS 1–2 in parallel with ENT 3–5) → joint Phase 6
validation. The OSS fake-server tests make the OSS track fully testable
before the Go side exists, and vice versa.

## Phase 0 — Pin, prove, publish the contract (OSS repo)

Nothing later may be built on an unverified schema or an unpublished contract.

### 0a. `docs/contracts/usage-ingest-v1.md` (new)

Copy the contract draft from [architecture.md](architecture.md) §4 verbatim
(everything between the horizontal rules, starting at "# Usage ingest
protocol v1", dropping the "Phase 0 copies it" sentence). Add the file to the
contracts table in `plans/enterprise/README.md`'s grounding section if the
program index is being maintained, and tick the program checklist item
"`usage-ingest-v1` contract published in `docs/contracts/`".

### 0b. Compatibility test — `brain4all/tests/test_usage_reporter.py` (new file, first test class)

Same pattern plan 009 used for schema pinning (see
`plans/009_usage_analytics/` Phase 0 and
[`brain4all/tests/test_analytics.py`](../../../brain4all/tests/test_analytics.py)
scaffolding): create an agent through the real API in a temporary
`HERMES_HOME`, then assert the pinned columns exist.

```python
class SessionSchemaCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    REQUIRED_SESSION_COLUMNS = {
        "id", "source", "model", "billing_provider", "started_at", "ended_at",
        "input_tokens", "output_tokens", "cache_read_tokens",
        "cache_write_tokens", "reasoning_tokens", "estimated_cost_usd",
        "actual_cost_usd", "cost_status", "api_call_count", "message_count",
    }
    FORBIDDEN_PRESENT_COLUMNS = {"title", "system_prompt", "model_config",
                                 "billing_base_url", "user_id"}

    async def test_sessions_schema_pins(self):
        # create agent via POST /agent-gateway/v1/agents (test_analytics.py idiom)
        cols = {row[1] for row in sqlite3.connect(db).execute(
            "PRAGMA table_info(sessions)")}
        self.assertLessEqual(self.REQUIRED_SESSION_COLUMNS, cols)
        # the forbidden columns EXIST locally — the allowlist must exclude them;
        # assert presence so a rename cannot silently invalidate the redaction test
        self.assertLessEqual(self.FORBIDDEN_PRESENT_COLUMNS, cols)
        msg_cols = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
        self.assertIn("timestamp", msg_cols)   # basis of derived last_activity_at
```

Also in 0b, resolve the three **verify in Phase 0** items from
[findings.md](findings.md) §7 and §1:

- Run a real chat and check whether session counter updates co-occur with
  `messages` rows (record the answer as a comment in the test file; if not,
  enable the overlap-window fallback in the reader, findings §1).
- Enumerate observed `source` values; assert none are user-entered text.
- Measure the derived-`last_activity_at` query on a seeded profile
  (~100k messages); note the timing in the test file comment.

### 0c. E01 identity seam check

Confirm what E01 provides (module, function names) for
`(device_id, access_token)` + refresh. If E01's connector is not yet merged,
define the seam here as `brain4all/integrations/enterprise_usage.py::
load_device_identity(data_dir) -> DeviceIdentity | None` reading
`DATA_DIR/device/` (per device-command-v1: keys live there), returning `None`
when unenrolled, and file a note in E01's plan to implement/own that surface.
The reporter treats `None` as "stay dormant".

## OSS track (this repo, Python)

### Phase 1 — Snapshot reader: `brain4all/integrations/enterprise_usage.py` (new)

Integration-layer module in the style of
[`brain4all/integrations/analytics.py`](../../../brain4all/integrations/analytics.py):
no policy, no HTTP, read-only, degrade-to-empty.

Contents:

- `DeviceIdentity` dataclass + `load_device_identity()` (Phase 0c seam;
  replace body with E01's connector call when it lands).
- `load_or_create_salt(enterprise_dir: Path) -> bytes` —
  `secrets.token_bytes(32)`, atomic write, 0600
  (architecture.md §5).
- `hash_id(salt: bytes, kind: str, raw: str) -> str` — HMAC-SHA256 with
  `f"{kind}:{raw}"` domain separation, hex digest.
- `read_session_snapshots(profile_dir: Path, *, watermark: float, salt: bytes,
  limit: int) -> tuple[list[dict], float]` — opens `state.db` via the shared
  `_open_ro` (import it from `.analytics`; if the leading underscore matters
  stylistically, rename it there to `open_ro` in the same commit rather than
  copying). Executes the allowlist SELECT with derived `last_activity_at`
  ([findings.md](findings.md) §1) `WHERE last_activity_at > :watermark ORDER
  BY last_activity_at LIMIT :limit`; returns wire-ready snapshot dicts (per
  contract §Snapshot object — hashed ids, allowlist fields only,
  `reporter_watermark` filled by the caller) plus the max
  `last_activity_at` seen. **The SELECT lists columns explicitly; never
  `SELECT *`** — the allowlist is enforced at the query, the dict builder,
  and the tests.
- `SNAPSHOT_FIELDS` — a frozen tuple of the 17 contract field names, exported
  so tests and the serializer share one source of truth.

Any `sqlite3.Error`/`OSError` returns `([], watermark)`.

### Phase 2 — Outbox + pusher: `brain4all/services/usage_reporter.py` (new)

Service-layer module owning policy and the loops (architecture.md §2):

- `ReporterConfig.from_env()` — reads the env vars (below), resolves
  `DATA_DIR/enterprise/` paths.
- `Cursor` — load/save `reporter_cursor.json` atomically (temp+fsync+rename,
  the repository idiom; use `FileRepository` helpers if a suitable one exists,
  else a local `_atomic_write_json`).
- `Outbox` — `append(snapshots, agent_watermarks, agent_mtimes)` writing
  `batch-<epoch_ms>-<seq>.jsonl` with the header line
  (architecture.md §2.2); `enforce_bound()` (drop oldest + increment
  `dropped_batches`); `oldest()`, `delete(path)`, `dead(path)` (move to
  `outbox/dead/`, cap 8 files).
- `scan_once(agents, cursor, salt, config)` — the §2.3 producer, SQLite work
  via `asyncio.to_thread`, semaphore(2).
- `push_once(client, identity, outbox, cursor)` — send oldest batch, handle
  the response matrix exactly per contract §Errors (2xx → delete + max-merge
  cursor advance; 401 → one token refresh then park; 409/400 → dead; 413 →
  split file in half and respool; 429 → sleep `Retry-After`; 5xx/network →
  raise to the loop's backoff).
- `reporter_loop()` — the composed background task: activation gate
  (architecture.md §2.1), two timers with ±20% jitter, full-jitter backoff
  1 s → 15 min. Every exception is caught and recorded; the loop never dies
  and never propagates.

**Lifespan wiring — `brain4all/app.py`.** Extend the existing lifespan
exactly like the kanban dispatcher block (lines ~32–53): start
`asyncio.create_task(reporter_loop(...), name="brain4all-usage-reporter")`
inside `try/except Exception: reporter = None`, and in the `finally` block
`cancel()` + `suppress(asyncio.CancelledError)` await, alongside the
dispatcher's teardown. No other app change.

**Config envs** (document in the deployment/env reference where
`ENTERPRISE_API_URL` is documented):

| Env | Default | Meaning |
|---|---|---|
| `ENTERPRISE_API_URL` | unset | existing; unset ⇒ reporter fully dormant |
| `BRAIN4ALL_USAGE_DISABLE` | `0` | `1` ⇒ dormant even when configured |
| `BRAIN4ALL_USAGE_REPORT_INTERVAL` | `60` | scan seconds |
| `BRAIN4ALL_USAGE_PUSH_INTERVAL` | `300` | push seconds |
| `BRAIN4ALL_USAGE_OUTBOX_MAX_BYTES` | `67108864` | spool cap |
| `BRAIN4ALL_USAGE_BATCH_MAX_SNAPSHOTS` | `500` | per contract 413 limit |
| `BRAIN4ALL_USAGE_SHARE_LABELS` | `0` | opt-in label registry (approaches §B) |

### Phase 2t — OSS tests: `brain4all/tests/test_usage_reporter.py` (extend the Phase 0 file)

Reuse the `test_analytics.py` scaffolding (temp `HERMES_HOME`, real agent
creation, `_SESSION_COLUMNS`-style inserts of real session rows). Add a
**fake ingest server** — an in-process `aiohttp`/`asgi` app implementing the
contract (auth check on a static bearer, upsert into a dict keyed by
`(device, agent_hash, session_hash)` with the monotonic guard, ack watermark,
scriptable failures: 500 N times, drop-connection-after-commit, 401, 413,
429):

1. **Snapshot correctness** — insert known session rows; `scan_once` +
   drain; fake-server totals equal hand-computed sums; watermark advances
   only after ack.
2. **Offline spool** — fake server down for many cycles; batches accumulate;
   server up; drain oldest-first; totals correct; batch files deleted.
3. **Ack watermark / crash replay** — script "commit then drop connection";
   pusher retries the same file; server `duplicates == count`; totals
   unchanged; cursor advances once.
4. **Retry-storm dedup** — send the same captured batch body 10× directly at
   the fake server; identical totals (client-side mirror of the Go property
   test).
5. **Redaction scan** — plant sentinels (`title="SENTINEL_TITLE"`,
   `system_prompt="SENTINEL_PROMPT"`, `model_config`, `billing_base_url`,
   `user_id`, and a `messages.content="SENTINEL_CONTENT"` row); capture
   every request body the fake server receives; assert (a) no sentinel
   substring anywhere, (b) every snapshot's key set `==
   set(SNAPSHOT_FIELDS)` exactly — allowlist, not blocklist.
6. **Bounded spool eviction** — tiny `BRAIN4ALL_USAGE_OUTBOX_MAX_BYTES`;
   sustained outage; oldest files evicted; `dropped_batches` incremented;
   directory bytes ≤ cap at all times.
7. **Dormancy** — no `ENTERPRISE_API_URL` ⇒ no `DATA_DIR/enterprise/`
   creation, no requests; `BRAIN4ALL_USAGE_DISABLE=1` same; unenrolled
   (identity `None`) same.
8. **Read-only proof** — `state.db` file bytes/mtime unchanged by a full
   scan+push cycle (plan-009 §read-only-safety pattern).
9. **Local isolation** — with the fake server returning only errors, local
   API routes (health, agents, plan-009 analytics) respond normally
   (latency assertion lives in [validation.md](validation.md) §8).

Run: `python -m unittest brain4all.tests.test_usage_reporter -v`, then
`make check`.

## Enterprise track (`brain4all-enterprise`, Go + PostgreSQL, no ORM)

Paths follow the repo's `internal/` layout (`docs/enterprise-extension.md`;
align names with E01's skeleton when it exists — **verify in Phase 0** of the
enterprise repo).

### Phase 3 — Migrations + ingest

- `migrations/00XX_usage_collection.sql` (next free number after E01's) — the
  complete SQL from [architecture.md](architecture.md) §6: 
  `usage_session_snapshots`, `usage_rollups_daily`,
  `ALTER TABLE devices ADD COLUMN acked_watermark`, `usage_ingest_audit`,
  indexes. Extends, never rewrites, existing migration history.
- `internal/ingest/types.go` — envelope/snapshot/response structs mirroring
  the contract exactly; strict JSON decoding (`DisallowUnknownFields`) to
  enforce the closed allowlist (400 `unknown_field`).
- `internal/ingest/handler.go` — `POST /ingest/v1/usage`: device-token auth
  via E01's auth middleware (token device == body device, revoked ⇒ 401);
  1 MiB `http.MaxBytesReader` + 500-snapshot cap (413); per-device token
  bucket (e.g. 10 req/min burst 30) returning 429 + `Retry-After`; schema and
  non-negative validation (400); then `store.ApplyBatch`.
- `internal/ingest/store.go` — `ApplyBatch(ctx, tx, device, batch)`
  implementing architecture.md §6.1 verbatim: one transaction, stale-batch
  409 guard, `SELECT … FOR UPDATE`, field-wise monotonic compare, UPSERT,
  server-derived delta into `usage_rollups_daily`
  (`INSERT … ON CONFLICT … DO UPDATE SET x = usage_rollups_daily.x +
  EXCLUDED.x`), audit rows for regressions, `acked_watermark` max-update.
  Raw SQL via `database/sql`/pgx.
- `internal/ingest/labels.go` — `POST /ingest/v1/labels` (opt-in registry,
  approaches §B): upsert `(tenant_id, device_id, agent_hash) → display_name`
  into a small `usage_agent_labels` table (add to the migration).

### Phase 4 — Rollup reconcile job

- `internal/ingest/rollup.go` — `ReconcileNightly(ctx, db)`: the sum-invariant
  query from architecture.md §6.2, current-day adjustment, `reconcile_adjust`
  audit rows, and `user_id` back-fill for devices claimed since last run.
  Scheduled by the repo's existing job runner (or a `time.Ticker` in main
  until E01 grows one).

### Phase 5 — Admin usage API

- `internal/adminapi/usage.go` — `GET /admin/v1/usage`
  (`tenant,user,device,model,from,to,bucket` per architecture.md §7; rollup
  queries grouped by the bucket; params mirror plan 009's) and
  `GET /admin/v1/usage/export.csv` (one row per user/device/day/model).
  Admin principal via E01's auth seam. Dashboard page is a follow-up UI task,
  not part of this plan's acceptance beyond numbers matching.

### Phase 3–5 tests (Go)

- `internal/ingest/handler_test.go` — auth matrix (valid, wrong device,
  revoked ⇒ 401), 413, 429, 400 unknown-field/negative-counter, 409 stale
  batch.
- `internal/ingest/dedup_property_test.go` — **property-style dedup**:
  generate random valid batches (randomized snapshot subsets, orders,
  monotone counter growth across generations); apply each batch **10× in
  shuffled order interleaved across generations**; assert final snapshot
  rows, rollup totals, and `accepted/duplicates` bookkeeping are identical to
  a single ordered application. Run against a real Postgres
  (dockertest/testcontainers per repo convention).
- `internal/ingest/store_test.go` — regression rejection: lower one counter ⇒
  row untouched, `rejected[]` entry, `usage_ingest_audit` row with
  before/after numerics; equal snapshot ⇒ `duplicates`; delta rollup math
  including first-seen `sessions` counting and cross-day accrual (freeze the
  clock, apply snapshots "on" two days, assert per-day rows).
- `internal/ingest/rollup_test.go` — corrupt a rollup row, run reconcile,
  assert repair lands on current day + audit row; claim a device mid-test,
  assert `user_id` back-fill.
- `internal/adminapi/usage_test.go` — seeded rollups; bucket grouping; CSV
  shape; tenant isolation (tenant A cannot read tenant B).

## Phase 6 — Joint validation

Execute [validation.md](validation.md) end-to-end with ≥2 real devices
against a real Postgres, and record evidence in that file's checklist.
