# E02 — Approaches

Decision records for the five load-bearing choices. Decision A restates and
justifies the architecture the program README already fixed; B–E are decided
here. Each section: options, comparison, decision, consequences.

## A. Transport of truth: session-snapshot upserts vs event deltas vs OTLP metrics

**Chosen: session-snapshot upserts** (fixed by
[`plans/enterprise/README.md`](../README.md); this record shows the work).

### Option A1 — Session-snapshot upserts (chosen)

The reporter sends the *latest cumulative state* of each changed session; the
server UPSERTs per `(tenant_id, device_id, agent_hash, session_hash)` with a
monotonicity guard.

- Exactly-once accounting from at-least-once delivery: re-sending any batch
  any number of times converges to the same stored row. The idempotency
  mechanism is one PRIMARY KEY — no dedup ledger, no event-id memory.
- Matches the source: the `sessions` counters in `state.db` are already
  cumulative ([findings.md](findings.md) §1). The snapshot *is* the SELECT.
- Offline catch-up is "send current state"; a week of missed intermediate
  states costs nothing.
- Cost: per-day accrual is not in the payload; the server derives it as
  `new − old` inside the upsert transaction (architecture.md §6.1). That
  derivation is server-side, against the server's own authoritative prior
  state — it inherits the idempotency of the upsert (duplicate ⇒ delta 0).

### Option A2 — Event deltas (rejected)

The client computes "tokens since last send" per session and the server sums
events.

- Under at-least-once delivery **every retry double-counts** unless each
  event carries a globally unique id the server remembers indefinitely — a
  dedup table that grows with events, not sessions, and must be consulted on
  every insert forever (or garbage-collected, reintroducing the risk).
- The client must persist "what I already sent" *transactionally with the
  send* — a crash between send and cursor write silently double-bills. The
  snapshot design makes the same crash a no-op.
- Any loss (spool eviction, dead batch) permanently under-counts; snapshots
  self-heal because the next snapshot carries the full total.

### Option A3 — OTLP metrics through the telemetry pipeline (rejected)

Ride the already-designed `runtime → OTel → Enterprise API → ClickHouse`
path with counter metrics.

- Categorically disallowed as accounting:
  [`docs/contracts/entitlements-v1.md`](../../../docs/contracts/entitlements-v1.md)
  — **"Telemetry and headers never serve as accounting state"** — and
  [`docs/plans.md`](../../../docs/plans.md) — "Telemetry is never a billing
  source of truth."
- Mechanically unfit: `05-telemetry.md` *requires* bounded buffers with
  drop-oldest under pressure, and collectors sample/batch/re-export. Correct
  for observability; disqualifying for invoices.
- Wrong store: ClickHouse holds traces with plan-tiered retention; billing
  requires keep-forever Postgres (`docs/enterprise-extension.md`).
- Also: OTel counter semantics across process restarts (resets, cumulative
  vs delta temporality) reintroduce exactly the double/under-count problems
  A1 eliminates.

**Consequence of A1:** the ingest API and contract are custom
(`usage-ingest-v1`, architecture.md §4), small, and versioned — one endpoint,
one table, one guard.

## B. Identifier hashing vs plaintext, and the label registry

**Chosen: HMAC-SHA256 with a device-local salt by default; opt-in label
registry for admin readability.**

### Option B1 — Plaintext agent/session ids (rejected)

Readable dashboards for free, but agent ids are user-chosen names —
`05-telemetry.md` forbids "user-entered names" leaving the device — and
session ids are join keys into local content. Violates the program privacy
principle outright.

### Option B2 — Plain SHA-256 without salt (rejected)

Unsalted hashes of low-entropy names ("marketing", "kim-assistant") fall to
dictionary attack, and identical agents hash identically **across devices**,
enabling cross-device correlation the center has no need for.

### Option B3 — HMAC with device-local salt (chosen)

`agent_hash = HMAC_SHA256(salt, "agent:" + id)`; salt is 32 random bytes in
`DATA_DIR/enterprise/reporter_salt`, never transmitted (spec:
architecture.md §5, contract § Hashing rules).

- The server can group, upsert, and roll up (hash is stable per device) but
  cannot reverse or dictionary-attack (no salt) and cannot correlate the same
  agent name across devices (different salts).
- Tension, stated honestly: admins see `a3f9…` where they want "Marketing
  assistant". Resolution — **the user opts in** to publishing display labels:
  `XNOBRAIN_USAGE_SHARE_LABELS=1` enables `POST /ingest/v1/labels` with
  `{agent_hash, display_name}` pairs (agent display names only — session
  titles are content and are never labelable). Default is off because
  user-entered names are on the telemetry forbidden list; consent flips the
  default for exactly this narrow field. Per-user usage attribution never
  needs labels — the device→user claim provides it — so the un-opted
  dashboard is fully functional, just anonymous below the user level.

## C. Rollup strategy (and snapshot partitioning)

**Chosen: on-ingest incremental touched-pair deltas + nightly reconcile.**

### Option C1 — Cron batch recompute (rejected as primary)

Periodically recompute `usage_rollups_daily` from snapshots. Simple, but
snapshots hold only *latest cumulative* state — per-day accrual is not
reconstructible from them, so a pure recompute forces whole-session-to-one-day
attribution (lumpy days, sessions jumping between days on every update).
Also: dashboards lag by the cron period.

### Option C2 — Incremental on-ingest (chosen)

Inside the batch transaction, add the server-derived counter delta to the
`(tenant, user, device, day = server-receive-day, model)` rollup row
(architecture.md §6.1). Real-time dashboards, true per-day accrual, and
idempotent by construction (duplicate ⇒ delta 0). "Touched pairs" are exactly
the rollup rows the batch's snapshots map to — nothing else is recomputed.

### Plus: nightly reconcile (kept from C1)

Defense in depth against bugs/crash windows: verify the lifetime sum
invariant `Σ rollups == Σ snapshots` per (tenant, device, model), repair into
the current day with an audit record, and back-fill `user_id` for newly
claimed devices (architecture.md §6.2). Incremental for freshness, reconcile
for trust.

### Snapshot partitioning (sub-decision)

Monthly partitions on `usage_session_snapshots` were considered. **Decision:
start unpartitioned.** The table grows with *session count* (one row per
session, upserted in place), not per report — even 10k devices × 50 sessions/
day ≈ 15 M rows/month is comfortable for a PK-upserted table; partitioning by
a time column would conflict with the natural PK (Postgres requires the
partition key inside every unique constraint, which would weaken the
exactly-once key or force `(…, month)` keys and cross-partition duplicates for
long-lived sessions). Revisit at ~100 M rows with an archival table instead
(`usage_session_snapshots_archive`, moved by `last_received_at`), which
preserves the PK semantics. Rollups stay unpartitioned indefinitely (bounded
by days × devices × models).

## D. Outbox format: JSONL batch files vs SQLite queue

**Chosen: atomic JSONL batch files.**

### Option D1 — SQLite queue database (rejected)

A `DATA_DIR/enterprise/outbox.db` with a pending-batches table. Transactional
niceties, but: plan 009 and `AGENTS.md` establish the repo idiom as *atomic
files below `DATA_DIR`, no application database*; a writer database invites
lock contention and corruption modes the file scheme cannot have; and
"bounded, drop-oldest" is a `stat()+unlink()` on files versus DELETE+VACUUM
bookkeeping in SQLite. The repo's only SQLite files are Hermes-owned
`state.db`s that XNOBrain reads `?mode=ro` — introducing a XNOBrain-owned
writable SQLite file would be a new persistence category for no gain.

### Option D2 — Atomic JSONL batch files (chosen)

`outbox/batch-<epoch_ms>-<seq>.jsonl`, temp-file + fsync + rename, one local
header line + wire-ready snapshot lines (architecture.md §2.2).

- Matches the repository persistence idiom exactly (`AGENTS.md`: "atomic
  files below `DATA_DIR`"; kanban and repositories already work this way).
- A batch file is the unit of send, ack, retry, eviction, and dead-lettering
  — every spool operation is a whole-file operation, crash-safe by rename.
- Trivially inspectable/debuggable in the field (`cat`, `jq`).
- The wire body is precomputed at spool time, which is also what makes the
  redaction test airtight: the bytes on disk (lines 2..N) are the bytes sent.

## E. Reporting cadence and jitter

**Chosen: 60 s local scan / 300 s push, ±20% jitter, env-tunable.**

- **Scan (`XNOBRAIN_USAGE_REPORT_INTERVAL`, default 60 s).** Cheap by
  construction: per agent it is one `stat()` (mtime-skip, plan-009 pattern)
  and only on change one read-only indexed query. 60 s keeps `acked_watermark`
  and central freshness within a couple of minutes of reality without
  measurable local load.
- **Push (`XNOBRAIN_USAGE_PUSH_INTERVAL`, default 300 s).** Usage accounting
  does not need sub-minute freshness; 5-minute batching cuts fleet request
  volume 5× versus pushing every scan and produces fewer, larger, cheaper
  batches. A 10k-device fleet averages ~33 req/s at steady state.
- **Jitter ±20% on both timers + full-jitter exponential backoff (1 s → 15
  min cap) on failure + honoring `Retry-After` on 429.** Prevents fleet
  synchronization (thundering herd after a server restart —
  [findings.md](findings.md) risk 7). Deterministic-seeded jitter is
  explicitly *not* used; `random` per tick is correct here.
- Alternatives considered: push-per-scan (60 s) — 5× request volume for no
  accounting benefit; long cadences (30–60 min) — makes "dashboard matches
  local" demos and validation slow and widens the crash-loss window of
  *freshness* (never of correctness). Both remain reachable via the env vars
  for special fleets (e.g. metered links can set 3600 s).
- Rejected: server-driven cadence (config pushed via command envelope) —
  couples E02 to E01's command channel, which the program README deliberately
  avoids ("depends on E01's identity slice only"). Revisit under E03 fleet
  management if fleets need centrally tuned cadence.
