# E02 — Findings

Verified facts about the code and contracts this plan builds on, and the risks
that shaped the design in [architecture.md](architecture.md). Every claim below
carries a file reference; anything not directly verifiable today is marked
**verify in Phase 0**.

## 1. The local source of truth: `sessions` in each profile's `state.db`

XNOBrain itself guarantees the accounting schema — it is created by
`_ensure_session_schema` in
[`xnobrain/integrations/hermes.py`](../../../xnobrain/integrations/hermes.py)
(around line 1514; the method is invoked from both the profile-provisioning
path near line 1188 and the conversation-create path near line 1461). The
`sessions` table columns, verbatim from the `CREATE TABLE`:

| Column | Type | Reported? | Notes |
|---|---|---|---|
| `id` | TEXT PK | **hashed** → `session_hash` | never sent in plaintext |
| `source` | TEXT NOT NULL | yes | e.g. `api`; a fixed enum-ish tag, not user text |
| `user_id` | TEXT | **no** | local free-text identity; not the enterprise user |
| `model` | TEXT | yes | model name; allowed by the program README ("model names") |
| `model_config` | TEXT | **NEVER** | serialized config, may embed endpoints/params |
| `system_prompt` | TEXT | **NEVER** | prompt content — forbidden by 05-telemetry |
| `parent_session_id` | TEXT FK | no (v1) | delegation link; revisit in v2 if needed |
| `started_at` | REAL NOT NULL | yes | epoch seconds, client clock |
| `ended_at` | REAL | folded into derived `last_activity_at` | |
| `end_reason` | TEXT | no (v1) | not needed for accounting |
| `message_count` | INTEGER DEFAULT 0 | yes | the "chat count" number |
| `tool_call_count` | INTEGER DEFAULT 0 | no (v1) | droppable; add in v2 if wanted |
| `input_tokens` | INTEGER DEFAULT 0 | yes | **monotonically cumulative** |
| `output_tokens` | INTEGER DEFAULT 0 | yes | cumulative |
| `cache_read_tokens` | INTEGER DEFAULT 0 | yes | cumulative |
| `cache_write_tokens` | INTEGER DEFAULT 0 | yes | cumulative |
| `reasoning_tokens` | INTEGER DEFAULT 0 | yes | cumulative |
| `billing_provider` | TEXT | yes | provider name only; see §5 caveat |
| `billing_base_url` | TEXT | **NEVER** | can identify private endpoints/credentialed hosts |
| `billing_mode` | TEXT | no (v1) | |
| `estimated_cost_usd` | REAL | yes | |
| `actual_cost_usd` | REAL | yes | |
| `cost_status` | TEXT | yes | e.g. estimated/actual status tag |
| `cost_source` | TEXT | no (v1) | |
| `pricing_version` | TEXT | no (v1) | |
| `title` | TEXT | **NEVER** | user/LLM-generated conversation title = content |
| `api_call_count` | INTEGER DEFAULT 0 | yes | cumulative |

Three columns present in the schema are on the absolute never-serialize list
and must be called out in every review of the reporter: **`title`**,
**`system_prompt`**, **`model_config`** (plus `billing_base_url` and the local
`user_id`). The redaction test in [validation.md](validation.md) §4 plants
sentinels in exactly these.

The sibling `messages` table (same `_ensure_session_schema`) holds full
message `content`, `tool_calls`, `reasoning` — pure content. The reporter may
read **only** its `timestamp` column (see §2 on deriving activity) and nothing
else; the test suite enforces this by sentinel scan, since column-level access
control does not exist in SQLite.

**There is no `last_activity_at` column.** The design calls for a per-agent
watermark on last activity; it must be **derived**:

```sql
last_activity_at = MAX(
    COALESCE(s.ended_at, s.started_at),
    COALESCE((SELECT MAX(m.timestamp) FROM messages m WHERE m.session_id = s.id), 0)
)
```

`messages.timestamp REAL NOT NULL` exists in the schema above. An in-flight
session (no `ended_at` yet) advances `last_activity_at` through its newest
message row. **Verify in Phase 0**: that token-counter updates to a session
row are always accompanied (or closely followed) by a `messages` row, so
`last_activity_at > watermark` catches every counter change. If Phase 0 finds
counter updates without message writes (e.g. a final usage flush after the
last message), widen the scan with a fixed overlap window (rescan sessions
with `ended_at IS NULL` or `last_activity_at > watermark - 3600`); the
idempotent upsert makes over-scanning free, so the fallback is safe by
construction.

Useful index that already exists: `idx_sessions_started ON
sessions(started_at DESC)`. The watermark query adds a correlated subquery on
`messages(session_id)` — Phase 0 measures it on a realistic DB; if slow, the
mtime-skip (§2) already prevents scanning quiet agents at all.

## 2. What plan 009 already built that the reporter reuses

Plan 009 (implemented) established every local mechanism the reporter needs:

- **Read-only opening.**
  [`xnobrain/integrations/analytics.py`](../../../xnobrain/integrations/analytics.py)
  `_open_ro()` opens `file:{path}?mode=ro` with `uri=True, timeout=1.0`. The
  reporter uses the same helper (import, not copy — see
  [implementation.md](implementation.md) Phase 1). The module's docstring
  discipline applies verbatim: "This adapter never writes Hermes state."
- **Degrade-to-empty error handling.** Every `sqlite3.Error` in
  `aggregate_profile` / `period_spend` returns zeroes instead of raising. The
  reporter adopts the same posture: a locked/corrupt/missing `state.db` yields
  no snapshots this cycle, never an exception that could disturb the app.
- **mtime-skip partial cache.**
  [`xnobrain/services/analytics.py`](../../../xnobrain/services/analytics.py)
  `_collect_partials` stats `state.db` (`st_mtime_ns`) and skips re-reading an
  unchanged file. The reporter persists the same idea durably: the cursor file
  stores `{agent_id: mtime_ns}` and an unchanged file is skipped without
  opening SQLite at all. On a fleet where most agents are idle most of the
  time, a scan cycle is a handful of `stat()` calls.
- **Agent enumeration + profile paths.** `AnalyticsService._agents()` /
  `_profile_dir()` show how to list agents (`agents.list_agents()["agents"]`,
  each item carrying `name` and `profile_path`). The reporter enumerates the
  same way.
- **The comparison target.** Plan 009's local views are the numbers the
  central dashboard must match (README definition of done). The validation
  query pair is in [validation.md](validation.md) §3.
- **Test scaffolding.**
  [`xnobrain/tests/test_analytics.py`](../../../xnobrain/tests/test_analytics.py)
  builds a temporary `HERMES_HOME`, creates agents through the real API, and
  inserts real `sessions` rows (`_SESSION_COLUMNS` insert). The reporter tests
  reuse this scaffolding wholesale.

## 3. Contracts being reused

### Device identity — `docs/contracts/device-command-v1.md`

Verbatim anchors this plan relies on:

- "The local runtime initiates outbound TLS over port 443 … No inbound
  listener is required."
- "On first connection the runtime generates an Ed25519 key pair, registers
  the public key, proves possession through a challenge, and receives a device
  ID plus short-lived access token. Anonymous Free devices are possession
  identities and may later be claimed by an account without changing the
  device key."
- "Private keys never leave `DATA_DIR/device/`."
- "Delivery is at-least-once. `command_id` is the idempotency key."
- Revocation "stops new commands" and "rejects token refresh".

E02 reuses the identity and the delivery philosophy (at-least-once +
idempotency key), not the command envelope. The claim step is what maps
device → user → tenant, which is how usage rows become attributable to a user
without the device ever sending user identity.

### Telemetry boundary — `docs/plans.md` and `docs/implementation/05-telemetry.md`

`docs/plans.md` § Telemetry boundary: the pipeline "FastAPI/runtime → OTel
Collector → Enterprise API → … → ClickHouse" stores "token/character counts,
costs, latency, errors" and "does not duplicate Hermes chat logs, prompts,
responses, memories, skill contents, tool arguments, credentials, or profile
files." And under Enforcement rules: **"Telemetry is never a billing source of
truth."**

`05-telemetry.md`: allowed attributes include "hashed tenant/device/agent
identifiers"; forbidden content includes "prompts, responses, memories,
skills, file content/path outside normalized category, provider names tied to
credentials, tokens, headers, tool arguments, command plaintext, and
user-entered names." Buffering rules: "Local buffer exhaustion drops oldest
telemetry and increments one local metric" and "Telemetry loss never blocks
agent or cron execution." The outbox bound and drop-oldest-with-counter
behavior in [architecture.md](architecture.md) are this rule applied to usage
snapshots.

### Accounting authority — `docs/contracts/entitlements-v1.md` and `docs/enterprise-extension.md`

`entitlements-v1.md`, final line: **"Telemetry and headers never serve as
accounting state."** `docs/enterprise-extension.md`: "The control plane is the
billing source of truth; HTTP headers and traces describe decisions but are
not accounting records," and both repos: no ORM.

Consequence: usage accounting cannot ride the OTel/ClickHouse pipeline. It
needs its own authenticated, validated, idempotent path into PostgreSQL —
which is exactly what E02 builds.

### AGENTS.md guardrails

"An Enterprise API outage must not restrict local features." "Never return,
log, or trace credentials, authorization headers, request bodies, prompts,
provider keys, tool arguments, or tool output." Both are acceptance criteria,
not aspirations ([validation.md](validation.md) §4, §8).

## 4. Why session snapshots beat the alternatives

The program README fixes this choice; the reasoning, spelled out:

- **The counters are already cumulative.** `input_tokens`, `output_tokens`,
  cache/reasoning tokens, `api_call_count`, `message_count` in a `sessions`
  row only grow over the session's life (they are running totals maintained by
  the runtime). A snapshot of the row **is** the total — no client-side delta
  ledger, no "what did I already send" arithmetic that can drift.
- **Snapshots + UPSERT give exactly-once from at-least-once.** Send the same
  snapshot 1× or 10×: the stored row per `(tenant, device, agent_hash,
  session_hash)` ends identical. Event-deltas under at-least-once delivery
  double-count on every retry unless every event carries a unique ID the
  server dedups forever — a much larger surface than one UNIQUE constraint.
- **Offline catch-up is trivial.** A laptop offline for a week just sends its
  current snapshots on reconnect; intermediate states that were never sent
  never need reconstruction. A delta scheme has to persist and replay every
  intermediate delta in order.
- **OTel metrics are not billing-grade.** Collectors sample, batch, drop on
  backpressure, and re-export; `05-telemetry.md` *requires* drop-oldest under
  pressure. That is correct for observability and disqualifying for invoices.
  `entitlements-v1.md` states it flatly: "Telemetry and headers never serve as
  accounting state." Full comparison in [approaches.md](approaches.md) §A.

## 5. Constraints and environmental facts

- **NAT / no inbound.** Fleet devices are Incus containers and user PCs behind
  NAT. Only outbound HTTPS 443 is guaranteed (device-command-v1). Pull is
  structurally impossible; push is the only option.
- **Offline is normal, not exceptional.** Laptops sleep, containers migrate,
  links flap. The design must tolerate arbitrary offline windows (target: a
  7-day gap, [validation.md](validation.md) §2) within the outbox byte bound.
- **Dormancy.** The reporter must be a no-op unless `ENTERPRISE_API_URL` is
  configured (env already recognized — see
  [`xnobrain/tests/test_fastapi.py`](../../../xnobrain/tests/test_fastapi.py)
  line ~70 and `docs/plans.md`) **and** the device is enrolled. Default
  Compose self-hosted mode must show zero network calls and zero new files.
- **Lifespan pattern exists.** [`xnobrain/app.py`](../../../xnobrain/app.py)
  lines ~32–53 wrap the upstream lifespan and start/cancel the kanban
  `dispatcher_loop` task with `suppress(Exception)` on startup and
  `cancel()`/`CancelledError` suppression on shutdown. The reporter wires in
  identically (see [implementation.md](implementation.md) Phase 2).
- **`billing_provider` caveat.** `05-telemetry.md` forbids "provider names
  tied to credentials." The program README explicitly allows "model names" and
  the snapshot design includes `billing_provider` — the distinction is that
  `billing_provider` is a routing/product label (`anthropic`, `openai`,
  `nine-router`), not a credentialed account identity; `billing_base_url`
  (which *can* identify a private credentialed endpoint) is excluded. State
  this in the contract so reviewers don't re-litigate it.
- **`model` freshness caveat.** `sessions.model` is written at session insert
  (see the conversation-create `INSERT` near hermes.py line 1464). If a
  session's effective model changes mid-session, per-model attribution follows
  the recorded column. Same caveat plan 009 already lives with; central and
  local views agree because both read the same column.

## 6. Risks

1. **Clock skew.** Client `started_at`/`last_activity_at` come from device
   clocks, which can be wrong by minutes or years. **Rule:** rollup **day
   boundaries use server receive time** (`received_at`, stamped by the ingest
   handler); client timestamps are retained on the snapshot row for display
   and cross-checking, and grossly-skewed client timestamps are flagged in
   audit but do not affect billing-day attribution. Watermarks are compared
   only against other timestamps from the *same* device, so skew does not
   break watermark logic.
2. **Device re-enrollment / identity discontinuity.** If `DATA_DIR/device/`
   (key) or `DATA_DIR/enterprise/` (salt + cursor) is wiped while the agent
   profiles under `HERMES_PROFILES_ROOT` survive, the device re-enrolls with a
   **new `device_id`**, a **new HMAC salt**, and an empty watermark — every
   historical session is re-reported under new `(device_id, session_hash)`
   keys and would double-count at the user level. **Identity continuity
   rule:** (a) enrollment key, reporter salt, and cursor live side by side
   under `DATA_DIR` and are backed up / wiped together; (b) on claim, the
   control plane records device succession — when a user claims a new device,
   an admin (or an automated heuristic on overlapping `started_at` ranges) can
   mark the old device `superseded_by`, and rollup queries exclude superseded
   devices from user totals for overlapping periods; (c) the contract states
   that device identity, not machine identity, is the accounting key.
   Documented as an operational runbook item; not solvable purely in code
   without content-derived fingerprints, which the privacy boundary forbids.
3. **Multi-batch partial failure.** The pusher may have batches B1, B2, B3
   spooled; B1 acks, B2 times out after the server processed it, B3 never
   sends. Handled structurally: each batch file is self-contained, carries its
   own `batch_id` and per-agent watermark map, is deleted only on ack, and
   re-sending a processed batch is a no-op (upsert). Cursor advance is
   per-batch-ack, monotonic max-merge, so out-of-order acks are safe. The
   failure-modes table in [architecture.md](architecture.md) enumerates each
   case.
4. **Counter regression.** A restored-from-backup `state.db`, a deleted
   session (rows are `DELETE`d by `_delete_session_sqlite`, hermes.py ~1503),
   or a tampered device can present counters lower than previously reported.
   The server treats regressions as suspect: it **never lowers** stored
   counters, audits the event, and rejects the offending snapshot
   (contract §Errors, `counter_regression`). Note: session deletion locally
   does *not* retract centrally reported usage — billing keeps the recorded
   totals. State this in the contract.
5. **Outbox growth under long outage.** Bounded bytes, oldest batch dropped
   with a persistent `dropped_batches` counter (05-telemetry buffering rule).
   Because snapshots are cumulative, dropping an *old* batch for a session
   that later gets a newer snapshot loses nothing; loss is only possible for a
   session whose final snapshot itself is evicted — the counter makes that
   visible and [validation.md](validation.md) §9 measures it.
6. **Watermark query cost on `messages`.** The derived `last_activity_at`
   subquery scans `messages` per candidate session. Mitigated by mtime-skip
   (quiet DBs are never opened) and by bounding candidates with
   `idx_sessions_started`. **Verify in Phase 0** with a seeded 100k-message
   profile; contingency: candidate selection on `COALESCE(ended_at,
   started_at)` plus open-session rescan only.
7. **Fleet thundering herd.** Thousands of devices with the same push
   interval synchronize after a server outage. Mitigated by per-device random
   jitter (±20%) and full-jitter exponential backoff; server rate limit (429 +
   `Retry-After`) is the backstop.

## 7. Open questions

1. **Exact E01 identity seam.** E01 is planned but not yet implemented; the
   function surface the reporter calls for `(device_id, access_token)` — and
   token refresh on 401 — is defined in
   [implementation.md](implementation.md) Phase 1 as a seam and must be
   reconciled with E01's actual connector module when it lands. **Verify in
   Phase 0** (stub acceptable for OSS-side tests; the fake ingest server
   validates a static bearer).
2. **Do counter updates always co-occur with message writes?** (§1 above.)
   Affects only scan completeness, not correctness; fallback documented.
   **Verify in Phase 0.**
3. **`source` value inventory.** Only `"api"` is visible in this repo's insert
   path (hermes.py ~1464); Hermes-native session creation may use other tags.
   Enumerate observed values in Phase 0 and confirm none are user-entered
   text. **Verify in Phase 0.**
4. **Tenant resolution for unclaimed devices.** An enrolled-but-unclaimed
   (anonymous) device has no user/tenant yet. Decision needed with E01: either
   ingest accepts and parks rows under a per-device pseudo-tenant until claim
   back-fills `user_id`/`tenant_id`, or ingest rejects unclaimed devices with
   403. Recommended: **accept and back-fill on claim** (usage before claim is
   still real usage; the UPDATE on claim is one statement). Flagged for E01
   review.
5. **Retention/pruning of `usage_session_snapshots`.** Billing-grade data;
   default keep-forever with optional archival after N months. Partitioning
   decision in [approaches.md](approaches.md) §C makes late archival cheap
   either way. Product decision, not blocking.
