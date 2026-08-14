# 009 — Approaches

Options considered for each decision, then the chosen path. Cross-links:
[README.md](README.md), [findings.md](findings.md),
[architecture.md](architecture.md), [implementation.md](implementation.md).

## Decision A — Source of aggregation

### A1. Proxy the Hermes native `/api/analytics/*` endpoints

Call `_get_usage_analytics` / `_get_models_analytics` (in-process, or via HTTP
on `:8642`), once per agent by passing `profile=<agent-id>`, and stitch the
results.

- Pros: reuses upstream SQL and per-model capability enrichment; less new code.
- Cons (decisive):
  - **Single profile per call** — cross-agent totals and a unified time-series
    still have to be assembled in XNOBrain anyway, so the proxy saves little.
  - **Not read-only** — those endpoints use a writable `SessionDB._conn`, which
    violates the `?mode=ro` constraint.
  - **Extra upstream deps** — `agent.insights.InsightsEngine`, `models_dev`, and
    the `session_model_usage` table are not guaranteed on XNOBrain-created DBs
    and widen the pinned surface.
  - **Profile-resolution mismatch** — `_cron_profile_home(profile)` may not map
    to `profiles_root/<agent-id>` the way `AgentManager` does.
  - **Coupling to a large private function** we do not control.

### A2. Compute on read from `state.db` (chosen for the core)

Open each agent's `state.db` with the existing `?mode=ro` helper and run
`SUM/GROUP BY` over the accounting columns XNOBrain guarantees.

- Pros: cross-agent + per-agent + per-model + time-series from one pass;
  strictly read-only; depends only on columns our own schema creates; no new
  store; small, testable SQL; naturally attaches XNOBrain budgets and the
  9router overlay.
- Cons: reimplements aggregation SQL (small, and largely mirrors upstream);
  no model-capability metadata (not needed for this plan).

### A3. Hybrid

Compute core views on read (A2), and *optionally*, per agent, best-effort proxy
`/api/analytics/models?profile=<agent>` only for capability metadata.

- Pros: richest per-model page.
- Cons: reintroduces A1's coupling and profile-resolution risk for a
  nice-to-have.

## Decision B — Freshness vs cost of many reads

### B1. Live compute every request

- Pros: always current; simplest correctness story; no staleness.
- Cons: N file opens per request.

### B2. Cached snapshot file on disk

- Pros: cheap reads.
- Cons: **violates "no new persistent store"**; adds invalidation and staleness
  bugs; snapshot rule is for mutations, not a read cache.

### B3. Short in-memory TTL cache (chosen)

Live compute, memoized ~15–30s in `AnalyticsService`, busted on budget writes.

- Pros: bounds fan-out under bursty polling; ephemeral (not persistence, rule
  respected); trivially correct on cold cache.
- Cons: up-to-30s staleness (acceptable for spend dashboards).

## Decision C — Cost source (estimated vs actual)

### C1. Always estimated

- Pros: always present.
- Cons: ignores real billed amounts when the provider reports them.

### C2. Always actual

- Cons: mostly null in practice; would read as near-zero spend.

### C3. Prefer actual when final, else estimated, and label it (chosen)

Per row/total: use `actual_cost_usd` when present and `cost_status` marks it
final; otherwise `estimated_cost_usd`. Expose both figures plus a `cost_basis`
discriminator so the UI can tag estimates. `COALESCE(...,0)` throughout.

- Pros: truthful and transparent; never presents an estimate as billed.
- Cons: mixed-basis totals possible; disclosed via `cost_basis`.

## Decision D — Budget storage and enforcement

### D1. Advisory config in the agent's `config.yaml`, soft-warning only (chosen)

Snapshot-before-write; status computed on read; nothing blocked.

- Pros: obeys "no hard enforcement locally" and "no new DB"; reuses the existing
  config-mutation + snapshot discipline; isolated per agent.
- Cons: no enforcement — by design (Enterprise owns that per
  `docs/enterprise-extension.md`).

### D2. Hard local enforcement (rejected)

Blocking chats/runs at a cap. Rejected: contradicts the enterprise split and the
"local access is unlimited" principle.

## Chosen approach

- **Core aggregation: A2 (compute-on-read).** Read each agent's `state.db`
  read-only and aggregate in SQL. Do **not** proxy the native endpoints for the
  core; pin and smoke them only as an upstream-drift canary (findings.md §9).
- **Freshness: B3** short in-memory TTL cache over live compute.
- **Cost: C3** actual-when-final else estimated, always labelled.
- **Budgets: D1** advisory per-agent config in `config.yaml`, snapshot on write,
  soft warnings only.
- **Overlay:** 9router `usage()` best-effort; `account_usage` deferred.

Rationale: this is the only option that satisfies every mandatory constraint at
once — read-only access to Hermes state, no new database or API process, no fork
of Hermes internals, cross-agent aggregation the native endpoints cannot do,
advisory-only budgets, and honest cost reporting — while reusing helpers that
already exist in `xnobrain/integrations/hermes.py`.

## Decision E — Analytics engine: SQLite compute-on-read vs an embedded OLAP engine (DuckDB)

Recorded because the question "is per-request scanning of every agent's `state.db`
heavy — should we embed DuckDB (which can query SQLite files) instead?" is a
reasonable one to raise. It is not warranted for this plan; here is why, and the
trigger that would change the answer.

### Why the chosen approach (E1) is not heavy at local scale

- **The hot table is pre-aggregated.** Aggregation runs over the `sessions` table,
  which holds **one row per conversation** with token/cost columns already summed
  (`input_tokens`, `output_tokens`, `cache_*`, `estimated_cost_usd`,
  `actual_cost_usd`, `model`, `started_at`). The per-message table is **never
  scanned**. A month of heavy single-user use is *thousands* of rows, not millions.
- **Every query is bounded.** `WHERE started_at > cutoff` hits `idx_sessions_started`,
  then `SUM`/`GROUP BY` returns a handful of rows per agent — SQLite does this in
  milliseconds.
- **Fan-out is bounded.** Work scales as *(agents) × (sessions-in-window)*, one
  read-only open/close per agent in `asyncio.to_thread`, memoized 15–30s (Decision
  B3), so bursty dashboard polling does not repeat the scan.
- **Realistic ceiling.** The local path stays comfortably fast into the low millions
  of aggregated session-rows per (cold-cache) request — far beyond a self-hosted
  single-user deployment. Heaviness here would require an implausible local history.

### E1. Keep SQLite compute-on-read (chosen)

No new dependency; obeys the `AGENTS.md` "atomic files, no application database" rule;
strictly read-only (`?mode=ro`); sufficient at every plausible local scale.

### E2. Embed DuckDB as an in-process query engine (deferred, not chosen)

DuckDB can `ATTACH` each `state.db` and aggregate across all of them in **one**
vectorized query (via its sqlite scanner), with richer time-bucketing and
percentiles, replacing the N-queries-plus-Python-merge.

- **Its wins only materialize** with frequent, *uncached*, complex OLAP over tens of
  millions of rows or many files — an enterprise/cloud profile, not local.
- **Costs:** a heavyweight native dependency to package in the runtime image, the
  sqlite-scanner extension, and — decisively — it brushes against the `AGENTS.md`
  "no application database" rule. Used purely in-memory and ephemeral (no persistent
  `.duckdb` file), read-only, it is *defensible* as compute-not-storage, but it is a
  deliberate architectural addition the OSS philosophy resists and must be reconciled
  with maintainers before adoption.
- **Verdict: do not add for this plan.** Heavy analytics at scale belongs to the
  enterprise **Go + PostgreSQL** control plane (`docs/enterprise-extension.md`), which
  already owns cross-tenant aggregation — not the local single-node reader.

### Escalation ladder (act only on a MEASURED bottleneck, never speculatively)

1. Widen or precompute the in-memory TTL rollup on a timer — still no new dependency.
2. Add a covering index on `(started_at, model)` if the `sessions` table itself grows
   large on a given deployment.
3. Only if genuinely OLAP-heavy **and** local: introduce DuckDB behind a feature flag
   as an ephemeral, read-only, in-memory engine that `ATTACH`es the `state.db` files —
   after reconciling the "no application database" rule. **Trigger:** the analytics
   endpoint p95 on a cold cache exceeds an agreed budget (e.g. > 300–500 ms) at the
   deployment's real agent/session counts.

Do not add DuckDB to optimize a bottleneck this workload does not have at local scale.
