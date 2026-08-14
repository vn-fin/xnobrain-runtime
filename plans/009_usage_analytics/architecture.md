# 009 — Architecture

How the feature fits the XNOBrain layering. Cross-links:
[README.md](README.md), [findings.md](findings.md),
[approaches.md](approaches.md), [implementation.md](implementation.md).

## Layering fit

Analytics is a **pure read** feature. It respects the standard boundaries
(`AGENTS.md`): handlers translate HTTP, services own rules, integrations adapt
Hermes/9router, models are Pydantic, and `routes/setup.py` is the only route
assembly point. It adds no persistence.

```
routes/setup.py           new Route(...) lines, tag "Analytics"
   |
handlers/api.py           new operations: analytics_* (HTTP <-> envelope only)
   |
services/analytics.py     NEW: shaping, day/week bucketing, cost basis,
   |                      budget evaluation, 9router overlay merge
   |---> integrations/analytics.py   NEW: read-only SUM/GROUP-BY over each
   |                                  agent's state.db (?mode=ro)
   |---> integrations/hermes.py      AgentManager: agent list + profile dirs +
   |                                  config.yaml read/write (budget) + snapshot
   |---> integrations/nine_router.py NineRouterManager.usage() overlay
   |
models/api.py             NEW request/response Pydantic models
```

Local layers call each other in-process (no internal HTTP), exactly as the
existing services do.

## Data flow

Read path for a cross-agent summary (`GET .../analytics/usage?days=30&bucket=day`):

1. Handler resolves the operation and calls `service.analytics.usage_summary(
   days, bucket)`.
2. Service asks `AgentManager.list_agents()` for the agent set, and for each
   agent resolves its `profile_dir` (reuse `AgentManager` accessors; do not build
   paths from raw slugs).
3. For each agent, the **integration** opens `profile_dir/state.db` with
   `_open_readonly_db(...)` (the `?mode=ro` helper) and runs three aggregate
   queries scoped by `started_at > cutoff`:
   - totals (one row of `SUM(...)`),
   - per-model (`GROUP BY model, billing_provider`),
   - per-bucket (`GROUP BY date(started_at,'unixepoch')` for day; week derived
     in Python or via `strftime('%Y-%W', ...)`).
   Missing columns or a locked/corrupt DB degrade that agent to zeroes (never a
   500), mirroring how `_sessions` swallows `sqlite3.Error`.
4. The service merges per-agent partials into: overall totals, per-agent rows,
   per-model rows (summed across agents), and a dense time-series (fill empty
   buckets with zero across the requested window).
5. The service computes `cost_basis` (`actual` when a final actual cost exists,
   else `estimated`) and attaches both cost figures.
6. Best-effort overlay: call `NineRouterManager.usage(default_model)` and attach
   the quota windows; on `NineRouterAPIError` attach an empty, `available:false`
   overlay.
7. For each agent, read its advisory budget from `config.yaml` and attach a
   computed `budget` block (spend this period vs cap -> status).
8. Handler wraps the dict in the standard `APIEnvelope` success shape.

The blocking sqlite work runs under `asyncio.to_thread(...)` (as the Hermes
endpoints do) so it never stalls the event loop.

### ASCII data-flow diagram

```
                         GET /api/brain/v1/analytics/usage?days=30&bucket=day
                                            |
                                     handlers/api.py
                                            |  service.analytics.usage_summary()
                                            v
                                   services/analytics.py
                                            |
             +------------------------------+-------------------------------+
             |                              |                               |
             v                              v                               v
   AgentManager.list_agents()   integrations/analytics.py         NineRouterManager
             |                   (per agent, in to_thread)              .usage()
             |                              |                               |
     [a1, a2, a3, ...]                      | for each profile_dir:         | quota
             |                              |   open state.db ?mode=ro      | windows
             +--------------> profile_dir --+   SUM/GROUP BY started_at>cut  |
                                            |   close()                     |
                                            v                               |
                                  per-agent partials                        |
                                            |                               |
                                            v                               v
                               merge -> totals / per-agent / per-model /
                                        time-series (+ cost_basis)
                                            |
                                            +--- read config.yaml budget per agent
                                            |    compute spend -> status (advisory)
                                            v
                                   APIEnvelope { success, data, ... }

  Writes to state.db: NONE (read-only URI). Budget writes: config.yaml only,
  atomic + snapshot.
```

## Where budget config lives

Per-agent, advisory, in the agent's own `config.yaml` under a dedicated
`budget` key (namespaced to avoid colliding with the `allowed` config fields
`update_config` validates). Shape:

```yaml
budget:
  monthly_usd: 25.0          # null or absent = no cap
  daily_usd: 2.0             # optional sub-cap; null = none
  warn_threshold_percent: 80 # soft-warning line before the cap
  cost_basis: estimated      # which cost figure the cap compares against
  currency: USD
  updated_at: "2026-07-24T00:00:00Z"
```

Rules:

- **Advisory only.** Reading/evaluating a budget never blocks a chat, run, or
  task. Hard enforcement is Enterprise (`docs/enterprise-extension.md`).
- **Snapshot before write.** A budget set is a config mutation: snapshot the
  existing `config.yaml` via `FileRepository.snapshot(agent_id, "config",
  "config", ...)` then write atomically (reuse `AgentManager`'s atomic YAML
  writer / the `update_agent_config` path in `platform.py`). This obeys the
  "snapshot rule applies to mutations" constraint.
- **Isolation.** Budget lives under the agent's profile; never in a shared or
  new store.
- **Status** is computed on read: `spend_this_period` (summed from `state.db`
  for the budget period, in the configured `cost_basis`) vs `monthly_usd` (and
  `daily_usd`) -> `ok` | `warning` (>= warn threshold) | `exceeded` (>= cap).

## New XNOBrain API contract

Versioned under the existing `/api/brain/v1` prefix, tag `Analytics`. All
reads; only the budget PUT mutates (config.yaml). Responses ride the standard
`APIEnvelope`.

All read endpoints share a **Grafana-style control set** as query parameters:

- **`agents`** — comma-separated agent IDs to include, e.g. `?agents=abc123,def456`.
  **Absent or empty = all agents** (the default selection). A subset both scopes
  the numbers *and reduces the read fan-out* — fewer `state.db` files are opened
  (see [Scaling to many agents](#scaling-to-many-agents)). Unknown IDs are ignored
  (not a 400) so a stale saved selection degrades gracefully.
- **Time range — relative or absolute.** Either `days` (relative, default `30`)
  **or** `from`/`to` (absolute; epoch seconds or `YYYY-MM-DD`, UTC). When `from`/`to`
  are present they override `days`. This maps to Grafana's "Last 7d / 30d / custom".
- **`bucket`** — time-series granularity: `hour` \| `day` \| `week` \| `month`
  (default `day`; the UI auto-suggests one from the range width but the user can
  override).

| Method | Path | Operation | Body | Purpose |
|--------|------|-----------|------|---------|
| GET | `/api/brain/v1/analytics/agents` | `analytics_agents` | — | Lightweight `[{agent_id, display_name}]` list to populate the multi-select — no `state.db` reads (reuses `AgentManager.list_agents()` names only). |
| GET | `/api/brain/v1/analytics/usage` | `analytics_usage` | — | Summary for the **selected agents + range**: totals, per-agent, per-model, time-series. Query: `agents`, `days`\|`from`/`to`, `bucket`. |
| GET | `/api/brain/v1/analytics/agents/{agent_id}/usage` | `analytics_agent_usage` | — | One agent's totals, per-model, time-series. Query: `days`\|`from`/`to`, `bucket`. |
| GET | `/api/brain/v1/analytics/models` | `analytics_models` | — | Per-model breakdown summed across the **selected** agents. Query: `agents`, `days`\|`from`/`to`. |
| GET | `/api/brain/v1/analytics/timeseries` | `analytics_timeseries` | — | Dense token+cost series across the **selected** agents. Query: `agents`, `days`\|`from`/`to`, `bucket`. |
| GET | `/api/brain/v1/analytics/agents/{agent_id}/budget` | `analytics_budget_get` | — | Current budget config + computed spend/status. |
| PUT | `/api/brain/v1/analytics/agents/{agent_id}/budget` | `analytics_budget_set` | `AgentBudgetPatch` | Set/clear advisory budget (snapshot + atomic write). |

`analytics/agents` exists so the dashboard can fill its agent dropdown cheaply
(the Grafana "variable" pattern) without triggering a full usage sweep.
`timeseries` overlaps the series inside `usage`; keep it as a light endpoint the
chart can poll without the per-model payload, or fold it into `usage` if the UI
does not need it separately (decide during implementation — keep the route list
minimal).

### Pydantic model names (`xnobrain/models/api.py`)

Request:

- `AgentBudgetPatch(BaseModel)`: `monthly_usd: float | None`, `daily_usd: float |
  None`, `warn_threshold_percent: int = 80`, `cost_basis:
  Literal["estimated","actual"] = "estimated"`, `currency: str = "USD"`. All
  optional so a PUT can clear a cap (`monthly_usd: null`). Validation:
  non-negative amounts, `0 < warn_threshold_percent <= 100`.

Response DTOs (used by the service to build/validate the payload; responses
themselves serialize through `APIEnvelope` as plain dicts):

- `UsageTotals`: `input_tokens, output_tokens, cache_read_tokens,
  cache_write_tokens, reasoning_tokens, total_tokens, estimated_cost_usd,
  actual_cost_usd, cost_usd, cost_basis, sessions, api_calls`.
- `ModelUsage`: `model, provider, input_tokens, output_tokens,
  estimated_cost_usd, actual_cost_usd, cost_usd, cost_basis, sessions`.
- `BucketUsage`: `bucket` (ISO date or `YYYY-Www`), `input_tokens,
  output_tokens, total_tokens, cost_usd, cost_basis, sessions`.
- `AgentUsage`: `agent_id, display_name, totals: UsageTotals, budget:
  BudgetStatus | None`.
- `BudgetStatus`: `monthly_usd, daily_usd, warn_threshold_percent, cost_basis,
  currency, period_start, spend_usd, daily_spend_usd, percent_used, status
  ("ok"|"warning"|"exceeded"|"unset"), advisory: true`.
- `QuotaOverlay`: mirrors `NineRouterManager.usage()` output (`available,
  provider, model, plan, quotas[]`).
- `UsageSummary`: `range_from, range_to` (epoch, resolved from `days` or the
  absolute `from`/`to` pair), `period_days` (kept for back-compat), `bucket`,
  `agents_selected: list[str]` (resolved IDs; **empty means "all"**),
  `agents_available: int`, `generated_at, timezone ("UTC")`, `totals:
  UsageTotals, agents: list[AgentUsage], by_model: list[ModelUsage], series:
  list[BucketUsage], quota: QuotaOverlay`.

## Integration adapter

Add `xnobrain/integrations/analytics.py` (new) rather than growing
`hermes.py`. It owns the aggregation SQL and returns plain dicts; it holds no
policy and does no HTTP. It reuses hermes.py's read-only primitive — either by
importing/calling `AgentManager._open_readonly_db` semantics or by re-declaring
the identical `file:...?mode=ro` open in one small helper. Public surface:

- `aggregate_profile(profile_dir, *, start_epoch, end_epoch, bucket) -> ProfilePartial`
  (totals, per-model rows, per-bucket `series`). Read-only, defensive; every query
  is bounded by the `(start, end]` window and buckets via a fixed `strftime` map
  (`hour|day|month`; the service folds `week`).
- `period_spend(profile_dir, *, since_epoch, cost_basis) -> float` for budget
  evaluation.

`AgentManager` gains only tiny read/write helpers if needed (e.g. a public
`profile_dir(agent_id)` accessor and `read_budget`/`write_budget` on
`config.yaml`); prefer routing budget writes through the existing
`platform.py` config-snapshot path.

## Service

`xnobrain/services/analytics.py` -> `AnalyticsService(agents: AgentManager,
router: NineRouterManager)`, constructed in `PlatformService.__init__` as
`self.analytics = AnalyticsService(self.agents, self.router)` (mirrors
`self.kanban = KanbanService(agents)`). It owns: iteration over agents, calling
the integration in `to_thread`, merge/dense-fill, `cost_basis` selection,
week bucketing, the 9router overlay (best-effort), budget read + evaluation, the
short TTL cache, and safe response shaping (numeric/dimension fields only).

Errors raise `ServiceError` (from `platform.py`) so the handler maps them to the
standard failure envelope.

## React UI — Analytics dashboard

New feature under `src/features/analytics/` with an API client
`src/api/analytics.ts` and a hook `src/hooks/useAnalytics.ts` (mirroring
`useKanban.ts`/`kanban.ts`). A route/nav entry lands under Settings or a
top-level Analytics destination (follow the current nav rules in
`plans/CHECKLIST.md` 002). Components:

- **Control bar (top, Grafana-style)** — the single source of the query the whole
  dashboard reacts to:
  - **Agent multi-select** (`AgentPicker`): a checkable dropdown populated from
    `GET /analytics/agents`. Defaults to **All** (nothing selected = every agent);
    the user can check a subset, "select all", or "clear". Show the count
    ("All 12 agents" / "3 of 12"). The selection is passed as `agents=` and is
    persisted (URL query string + `localStorage`) so a reload keeps it.
  - **Time-range picker** (`RangePicker`): quick presets (Last 24h, 7d, 30d, 90d)
    that set `days`, plus a **custom** from/to date pair that sets `from`/`to`.
  - **Bucket selector**: `hour`/`day`/`week`/`month`, auto-suggested from the range
    width, user-overridable.
  - A **refresh** control and a small "as of `generated_at`" stamp (data is
    cached ~15–30s).
  Changing any control re-issues the read requests with the new
  `agents`/range/`bucket`; the tiles, bars, and chart all rebind. All state lives
  in one `useAnalyticsControls` hook and flows into `useAnalytics(query)`.
- **Totals tiles**: total tokens (input/output), estimated cost (with an
  "estimated" tag), sessions, API calls, and the active quota window(s) — scoped
  to the current selection + range.
- **Per-model bars**: horizontal bar list ranked by tokens, cost label per bar,
  for the selected agents.
- **Time chart**: tokens (and/or cost) per bucket, driven by the control bar's
  range + `bucket` (no separate in-chart toggles — the control bar is the single
  control surface, Grafana-style). A tokens/cost metric toggle stays on the chart.
- **Per-agent budget bar**: for each agent, a progress bar of spend vs cap with
  the `status` color (ok/warning/exceeded) and an edit control that PUTs
  `AgentBudgetPatch`. A clear "advisory — not enforced" label.

**Charts**: no chart library is installed (`package.json` has none), and the
app must stay self-contained. Implement a **lightweight inline SVG** bar/line
chart component (`src/features/analytics/Chart.tsx`) rather than adding a
dependency. Keep it accessible (title/desc, keyboard focus, `aria-label` per
series) and theme-aware, matching existing component styling. States: loading,
empty ("no usage yet"), partial-failure (one agent's DB unreadable), and
offline/retry, consistent with the Kanban feature's state handling.

## Performance and how to bound it

- **Window filter first.** Every query is `WHERE started_at > cutoff`, hitting
  `idx_sessions_started`; unbounded scans are never issued.
- **Aggregate in SQL.** `SUM`/`GROUP BY` in SQLite returns a handful of rows per
  agent, not raw sessions.
- **Concurrent, mtime-skipped reads.** Only agents whose `state.db` changed
  since the last request are re-read (checked with a cheap `os.stat` mtime); the
  rest reuse a cached per-agent partial. Refreshed agents are read
  **concurrently** under a bounded semaphore, each in `asyncio.to_thread`; a slow
  or locked profile degrades to zeroes rather than blocking others.
- **Two cache layers.** A per-agent partial cache keyed `(agent_id, window,
  state.db mtime)` that survives across requests, plus a ~15–30s TTL cache of the
  merged result keyed `(days, bucket)` for bursty identical polls. Both are
  ephemeral process state, not persistence, so the "no new store" rule holds.
  Bust the merged cache after a budget write.
- **Cost scales with *active* agents, not *total*.** With many profiles the
  dominant cost is opening N `state.db` files — so mtime-skip is the primary
  lever: an idle profile costs a microsecond `stat`, not a file open + query. UI
  paging of the per-agent *list* is a secondary nicety; it does **not** shrink
  the totals sweep (totals must consider every agent), which is why skip +
  concurrency — not paging — is what keeps many-agent deployments fast.

### Scaling to many agents

The per-db-then-merge is **correct at any N**: every session belongs to exactly
one agent's `state.db`, so summing per db and adding across dbs is exact — there
is no double-count or missed row. Scaling, not correctness, is the concern, and
it is bounded as above: mtime-skip means a request re-reads only the handful of
profiles that ran since the last poll, and bounded concurrency caps simultaneous
file opens. That keeps dozens-to-low-hundreds of local profiles fast on a single
node. True multi-tenant scale (hundreds-plus of always-active agents, cross-tenant
rollups, long-history OLAP) is out of scope for the local reader and belongs to
the enterprise **Go + PostgreSQL** control plane (`docs/enterprise-extension.md`),
which owns cross-tenant aggregation. An embedded OLAP engine (DuckDB) is **not**
the fix here — it still opens N files, adds a dependency, and conflicts with the
"no application database" rule; see [approaches.md](approaches.md) Decision E.
- **No capability enrichment** (`models_dev`) or `InsightsEngine` in the hot
  path — those upstream extras are deferred (see findings.md sections 5, 7).
