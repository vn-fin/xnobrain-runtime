# E02 — Validation

How completion is proven. Each section names the mechanism, the exact
procedure, and the evidence to record. Automated tests live in
`xnobrain/tests/test_usage_reporter.py` (OSS) and `internal/ingest/*_test.go`
+ `internal/adminapi/usage_test.go` (enterprise) — see
[implementation.md](implementation.md). Sections 2, 3, 8 additionally require
a live two-device run (README definition of done).

## 1. Exactly-once under retries (10× resend = same totals)

- **Go property test** (`internal/ingest/dedup_property_test.go`): random
  monotone batch generations, each applied 10× in shuffled interleaved order
  against real Postgres; final `usage_session_snapshots` rows AND
  `usage_rollups_daily` totals byte-identical to a single ordered
  application.
- **OSS mirror**: capture one real serialized batch from the reporter, POST
  it 10× to the fake ingest server; totals identical; response shows
  `duplicates == len(snapshots)` from the second send on.
- **Crash-window replay**: fake server scripted to commit then drop the
  connection; pusher retries the same batch file; totals unchanged; batch
  file deleted exactly once; cursor advanced exactly once.
- Evidence: test names + green run output; paste the before/after totals
  query from the property test log.

```sql
-- totals fingerprint used by the dedup assertions
SELECT COUNT(*), SUM(input_tokens), SUM(output_tokens),
       SUM(estimated_cost_usd), SUM(actual_cost_usd), SUM(api_call_count),
       SUM(message_count)
FROM usage_session_snapshots WHERE device_id = $1;
```

## 2. Offline catch-up (7-day gap)

- Automated: fake server down while the scanner runs many cycles over
  sessions inserted with `started_at`/message timestamps spread across 7
  simulated days; server up; drain completes; totals equal hand-computed
  sums; `duplicates` ≈ 0 (nothing was double-spooled); all batch files gone.
- Live: take device B offline (stop container networking) for the soak
  window while generating chats; reconnect; within two push intervals the
  central totals for B equal B's local analytics (query pair in §3).
- Evidence: test output + live-run timestamps showing gap start/end and the
  converged totals.

## 3. Per-user totals equal local plan-009 analytics

The flagship correctness demo. For each device/user, over the same UTC
window, the central numbers must equal the local numbers.

**Local side** (what plan 009 computes, per
[`xnobrain/integrations/analytics.py`](../../../xnobrain/integrations/analytics.py)
— run against each profile's `state.db`, summed across the user's agents):

```sql
SELECT COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0),
       COALESCE(SUM(estimated_cost_usd),0), COALESCE(SUM(actual_cost_usd),0),
       COUNT(*), COALESCE(SUM(api_call_count),0)
FROM sessions WHERE started_at > :from AND started_at <= :to;
```

**Central side** (snapshots, same window keyed on the client-clock
`started_at` the snapshot retains — this is the apples-to-apples comparison;
rollup days use server time and are validated separately in §7):

```sql
SELECT COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0),
       COALESCE(SUM(estimated_cost_usd),0), COALESCE(SUM(actual_cost_usd),0),
       COUNT(*), COALESCE(SUM(api_call_count),0)
FROM usage_session_snapshots
WHERE device_id = :device
  AND started_at > to_timestamp(:from) AND started_at <= to_timestamp(:to);
```

Equality must be exact (token counts and call/session/message counts are
integers; costs compare to 1e-6, matching plan 009's rounding). Also compare
against the local HTTP view (`GET` analytics summary routes from plan 009)
and `GET /admin/v1/usage` for the same window.

- Caveat to record, not excuse: sessions still receiving activity at
  measurement time can differ by the not-yet-pushed tail — quiesce chats for
  one push interval before comparing.
- Evidence: paired query outputs for ≥2 users/devices, including the
  formerly offline device.

## 4. Forbidden-field scan on the wire

- Plant sentinels in every locally-present forbidden column and in message
  content: `title`, `system_prompt`, `model_config`, `billing_base_url`,
  `user_id`, `messages.content`, `messages.tool_calls` (Phase 2t test 5).
- Capture **every serialized request body** at the fake server (and, in the
  live run, via a logging reverse proxy in front of the real ingest).
- Assert: no sentinel substring appears anywhere in any body, header, or
  reporter log line; every snapshot object's key set equals
  `SNAPSHOT_FIELDS` **exactly** (closed allowlist — an extra key fails even
  if harmless).
- Server side: Go strict decoding test proves an envelope with an unknown
  field is rejected 400 (`unknown_field`), so a future client widening fails
  loudly.
- Evidence: test output + one captured batch body pasted into the run log
  showing only allowlisted fields.

## 5. Revoked device rejected (401)

- Go test: revoke the device row; POST with its (still unexpired) token ⇒
  401 `device_unauthorized`; also wrong-device token (token A, body
  `device_id` B) ⇒ 401.
- OSS test: fake server returns 401 twice (initial + after refresh) ⇒ pusher
  parks, spool keeps obeying the byte bound, **local API routes unaffected**,
  and no crash-loop (auth retried on the hourly park timer, observable via
  the loop's state).
- Evidence: both test outputs; per device-command-v1, "Revocation … rejects
  token refresh" is exercised, not assumed.

## 6. Regression rejected and audited (409 / rejected[])

- Go store test: apply a snapshot, then re-apply with `output_tokens`
  lowered ⇒ stored row untouched, response `rejected:[{session_hash, code:
  "counter_regression"}]`, and a `usage_ingest_audit` row exists with
  numeric before/after detail (and **no** content fields in `detail`).
- Whole-batch staleness: batch whose max `reporter_watermark` is 8 days
  behind `devices.acked_watermark` ⇒ HTTP 409 `batch_stale` + audit row; OSS
  pusher moves the file to `outbox/dead/` and continues with newer batches
  (no wedge).
- Evidence: test outputs + the audit rows.

## 7. Rollups match snapshots (reconciliation)

- After the property test and the live soak: run the architecture.md §6.2
  sum-invariant query — zero mismatched rows.
- Fault injection: manually corrupt one `usage_rollups_daily` row; run
  `ReconcileNightly`; invariant restored; adjustment landed on the current
  day; `reconcile_adjust` audit row present.
- Cross-day accrual test (store_test): snapshots applied under a frozen
  clock on day 1 and day 2 produce two rollup rows whose sum equals the
  final snapshot; `SUM(sessions)` over days equals distinct sessions.
- Evidence: invariant query output (0 rows) before and after fault
  injection + repair.

## 8. Enterprise-down = zero impact on local API (measured)

- Benchmark harness (script or test): measure p50/p95 latency of a fixed
  local request mix (health, agent list, plan-009 analytics summary, one
  chat round) for (a) reporter dormant, (b) reporter active with the
  enterprise endpoint **blackholed** (connect timeout path — the worst
  case), (c) reporter active with the endpoint returning 500s.
- Acceptance: p95 deltas within noise (< 5%); no event-loop stalls (the
  SQLite work is in `to_thread`, HTTP timeouts are finite, and the pusher is
  one background task); no error escapes to any local route.
- Also assert: functional parity — every local route returns identical
  payloads in all three modes (`AGENTS.md`: an Enterprise API outage must
  not restrict local features).
- Evidence: the three latency tables in the run log.

## 9. Spool bounded under sustained outage

- Automated (Phase 2t test 6): tiny cap (e.g. 64 KiB), endless outage,
  high-rate synthetic sessions; at every cycle `du(outbox/) ≤ cap`; oldest
  files evicted first; `dropped_batches` counter in `reporter_cursor.json`
  increments; `outbox/dead/` never exceeds 8 files.
- Recovery semantics: after the server returns, totals for sessions whose
  *latest* snapshot survived are exact; any wholly-evicted final snapshots
  are visible as `dropped_batches > 0` (05-telemetry drop-oldest-with-counter
  rule, [findings.md](findings.md) risk 5).
- Evidence: test output including the byte-bound assertion trace.

## 10. Suite, dormancy, and docs

- `make check` green in this repo; `go test ./...` green in
  `xnobrain-enterprise`.
- Dormancy proof (Phase 2t test 7): default self-hosted mode creates no
  `DATA_DIR/enterprise/` files and makes zero network calls.
- Read-only proof (Phase 2t test 8): `state.db` bytes untouched by a full
  cycle.
- `docs/contracts/usage-ingest-v1.md` exists and matches both
  implementations (spot-check: field list, error codes, hashing prefixes).

## Acceptance checklist

- [ ] Phase 0 pins recorded: sessions/messages columns asserted; counter/message
      co-write question answered; `source` inventory clean; watermark query timed
- [ ] Contract published at `docs/contracts/usage-ingest-v1.md`
- [ ] §1 exactly-once: property test + 10× resend + crash replay green
- [ ] §2 offline 7-day catch-up green (automated + live device)
- [ ] §3 per-user central totals == local plan-009 totals (≥2 devices, incl.
      the offline-then-reconnected one), paired query outputs recorded
- [ ] §4 redaction: sentinel scan green; closed allowlist enforced both sides
- [ ] §5 revoked device 401 both sides; pusher parks without local impact
- [ ] §6 regression rejected + audited; stale batch 409 + dead-spool, no wedge
- [ ] §7 rollup sum invariant holds; reconcile repairs injected fault
- [ ] §8 latency tables show no local impact in blackhole/500 modes
- [ ] §9 spool byte bound held under sustained outage; drop counter visible
- [ ] §10 `make check` + Go suite green; dormancy and read-only proofs green
- [ ] Program checklist updated in `plans/enterprise/README.md` (E02 items)
