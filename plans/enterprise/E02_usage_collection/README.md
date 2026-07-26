# E02 — Central usage collection

Enterprise program flagship. Elaborates — does not re-decide — the architecture
fixed in [`plans/enterprise/README.md`](../README.md) § "The core architecture:
distributed usage → one database": **outbound-only push with device identity,
session-snapshot upserts, and an offline outbox.**

Read the sibling documents in order:

- [findings.md](findings.md) — the local source of truth, what plan 009 already
  built, the contracts reused, why snapshots, risks, open questions.
- [architecture.md](architecture.md) — end-to-end design, reporter internals,
  ingest sequence, complete SQL, the full `usage-ingest-v1` contract draft,
  hashing spec, failure-modes table.
- [approaches.md](approaches.md) — the five decisions and their alternatives.
- [implementation.md](implementation.md) — two tracks (OSS Python + enterprise
  Go), ordered file-by-file steps.
- [validation.md](validation.md) — acceptance checklist with evidence.

Also read before starting: [`AGENTS.md`](../../../AGENTS.md),
[`docs/plans.md`](../../../docs/plans.md) (telemetry boundary),
[`docs/contracts/device-command-v1.md`](../../../docs/contracts/device-command-v1.md),
[`docs/contracts/entitlements-v1.md`](../../../docs/contracts/entitlements-v1.md),
[`docs/implementation/05-telemetry.md`](../../../docs/implementation/05-telemetry.md),
[`docs/enterprise-extension.md`](../../../docs/enterprise-extension.md), and the
implemented plan-009 code:
[`brain4all/integrations/analytics.py`](../../../brain4all/integrations/analytics.py),
[`brain4all/services/analytics.py`](../../../brain4all/services/analytics.py),
[`brain4all/integrations/hermes.py`](../../../brain4all/integrations/hermes.py)
(`_ensure_session_schema`).

## Goal

**Every user's chats and tokens in one PostgreSQL database.**

A fleet of single-user deployments — each user running the OSS runtime in an
Incus container or on their own PC, usually behind NAT — reports per-session
usage (session counts, message counts, token counters, cost) into the single
`brain4all-enterprise` PostgreSQL control-plane database, keyed by
tenant → user → device → agent → session. Admins get per-user / per-device /
per-model / per-day usage views and a CSV export for billing.

"Collect all chats" means chat **counts and token totals**, never chat
**content**. The number that appears on an admin dashboard for a user must
equal the number that user's own local plan-009 Analytics dashboard shows.

Two deliverables:

1. **OSS usage reporter** (this repo, Python) — a dormant periodic task in the
   existing FastAPI lifespan that reads each agent profile's `state.db`
   read-only, builds content-free session snapshots, spools them to a bounded
   local outbox, and pushes them outbound-only to the enterprise ingest API.
   Active only when `ENTERPRISE_API_URL` is configured **and** the device is
   enrolled (device-command-v1 identity from E01).
2. **Ingest + storage + admin views** (`brain4all-enterprise`, Go + PostgreSQL) —
   `POST /ingest/v1/usage` with device-token auth and idempotent UPSERT,
   `usage_session_snapshots` + `usage_rollups_daily` tables, an incremental
   rollup job, `GET /admin/v1/usage` and a CSV export.

Plus one new public contract, [`docs/contracts/usage-ingest-v1.md`]
(drafted in full inside [architecture.md](architecture.md), copied into
`docs/contracts/` during implementation Phase 0).

## Non-goals

- **No content collection. EVER.** No prompts, responses, session **titles**,
  memories, skills, tool arguments, file paths, user-entered names, provider
  keys, or credentials cross the wire. The `sessions` table contains `title`,
  `system_prompt`, and `model_config` columns — they are explicitly on the
  never-serialize list (see [findings.md](findings.md) §1 and the redaction
  tests in [validation.md](validation.md)). This is the `05-telemetry.md`
  boundary applied to every byte that leaves the machine.
- **No ClickHouse here.** The `runtime → OTel → Enterprise API → ClickHouse`
  trace pipeline from `docs/plans.md` is ops telemetry, not accounting, and is
  out of scope (program README build-map item 7). Per
  `docs/contracts/entitlements-v1.md`: "Telemetry and headers never serve as
  accounting state." This plan's data path is a separate, billing-grade path
  into PostgreSQL.
- **No billing enforcement.** No quota checks, reservations, denials, or plan
  gating on the reporting path. Entitlements/billing enforcement is program
  item 5, built later on top of this data. Local budgets stay advisory
  (plan 009).
- **No pull/scrape.** The enterprise server never connects into, polls, or
  scrapes a user machine. There is no inbound listener (device-command-v1).
  Push only.
- **No local behavior change.** Per `AGENTS.md`, an Enterprise API outage must
  not restrict local features. The reporter is read-only over `state.db`,
  bounded in disk and memory, and its total failure is invisible to local use.
- **No new local database.** The outbox is atomic JSONL files under
  `DATA_DIR/enterprise/`, matching the repo's file-persistence idiom.

## Dependency

**E01 identity slice only** — device enrollment/claim/revoke and the
short-lived device token from
[`docs/contracts/device-command-v1.md`](../../../docs/contracts/device-command-v1.md)
(Ed25519 key in `DATA_DIR/device/`, anonymous enroll, later account claim
mapping device → user → tenant). E02 needs nothing else from E01: no command
envelope, no cron, no fleet orchestration. The program README states E02 "is
deliberately buildable with only the *device enrollment* slice of E01."

The reporter consumes the identity through a narrow seam
(`load_device_identity()` / token provider — see
[implementation.md](implementation.md) Phase 1); if E01's connector module is
not yet merged, the seam is stubbed and the integration point is verified in
Phase 0.

## Phases

- **Phase 0 — Pin and prove.** Compatibility test asserting the `sessions`
  accounting columns and `messages.timestamp` exist (same pattern as plan 009);
  copy the `usage-ingest-v1` contract draft from
  [architecture.md](architecture.md) into `docs/contracts/usage-ingest-v1.md`;
  verify the E01 identity seam.
- **Phase 1 — OSS snapshot reader.** `brain4all/integrations/enterprise_usage.py`:
  read-only, watermarked snapshot extraction with the allowlist serializer and
  HMAC hashing.
- **Phase 2 — OSS outbox + pusher.** `brain4all/services/usage_reporter.py`:
  spool, bounds, drain loop, backoff, cursor advance on ack; lifespan wiring in
  `brain4all/app.py` next to the kanban dispatcher.
- **Phase 3 — Enterprise ingest.** Go handler, migrations, monotonicity
  validation, UPSERT, ack watermark, rate limits.
- **Phase 4 — Rollups.** Incremental touched-pair rollup on ingest + nightly
  reconcile job.
- **Phase 5 — Admin views.** `GET /admin/v1/usage` (+ CSV export); dashboard
  page later.
- **Phase 6 — End-to-end validation.** Multi-device, offline catch-up, retry
  storms, redaction proof ([validation.md](validation.md)).

## Definition of done

- **≥ 2 devices, one Postgres.** At least two real deployments (e.g. two Incus
  containers, or a container plus a laptop behind NAT), including **one that
  goes offline for a sustained period and then reconnects**, report into one
  PostgreSQL instance, with rows attributable per tenant/user/device.
- **Numbers match local analytics exactly.** For every user, the central
  per-user dashboard token and cost totals equal the totals that user's own
  device shows in the plan-009 local Analytics views, for the same UTC window
  (query pair in [validation.md](validation.md) §3).
- **No double counting under retry storms.** Resending the same batch 10×
  (network flap simulation, reporter restart mid-drain, server 500 mid-batch)
  yields byte-identical totals. Proven by the Go property-style dedup test and
  the OSS pusher test.
- **Redaction proven, not asserted.** A test captures every serialized batch
  on the wire, checks each snapshot against a strict field allowlist, and
  plants sentinel values in `title`, `system_prompt`, `model_config`, and
  message content to prove no forbidden field ever leaves the device.
- **Failure isolation.** With the enterprise endpoint down for the whole test
  run, local API behavior and latency are unchanged and the outbox stays under
  its byte bound (oldest-dropped with a counter).
- Contract published: `docs/contracts/usage-ingest-v1.md` exists and matches
  the implementation on both sides.
