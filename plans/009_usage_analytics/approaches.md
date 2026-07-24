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
    still have to be assembled in Brain4All anyway, so the proxy saves little.
  - **Not read-only** — those endpoints use a writable `SessionDB._conn`, which
    violates the `?mode=ro` constraint.
  - **Extra upstream deps** — `agent.insights.InsightsEngine`, `models_dev`, and
    the `session_model_usage` table are not guaranteed on Brain4All-created DBs
    and widen the pinned surface.
  - **Profile-resolution mismatch** — `_cron_profile_home(profile)` may not map
    to `profiles_root/<agent-id>` the way `AgentManager` does.
  - **Coupling to a large private function** we do not control.

### A2. Compute on read from `state.db` (chosen for the core)

Open each agent's `state.db` with the existing `?mode=ro` helper and run
`SUM/GROUP BY` over the accounting columns Brain4All guarantees.

- Pros: cross-agent + per-agent + per-model + time-series from one pass;
  strictly read-only; depends only on columns our own schema creates; no new
  store; small, testable SQL; naturally attaches Brain4All budgets and the
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
already exist in `brain4all/integrations/hermes.py`.
