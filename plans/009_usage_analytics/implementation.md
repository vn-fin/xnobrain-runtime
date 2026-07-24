# 009 — Implementation

Ordered, phased, file-by-file. Follow the chosen approach in
[approaches.md](approaches.md) and the contract in
[architecture.md](architecture.md). Every `state.db` access is **read-only**
(`file:...?mode=ro`); the analytics path never writes `state.db`.

## Phase 0 — Pin and compatibility test

File: `brain4all/tests/test_analytics.py` (new). Reuse the temp-`HERMES_HOME`
pattern from `brain4all/tests/test_kanban.py` (skip when Hermes is unavailable).

1. Confirm the Hermes pin from plan 001 is in place; no version bump here.
2. Assert the accounting schema: create an `AgentManager` against a temp home,
   create an agent (or call `_initialize_state_db`), then
   `PRAGMA table_info(sessions)` and assert the column set from
   [findings.md](findings.md) §1 is a subset — specifically
   `started_at, model, billing_provider, input_tokens, output_tokens,
   cache_read_tokens, cache_write_tokens, reasoning_tokens, estimated_cost_usd,
   actual_cost_usd, cost_status, api_call_count`.
3. Assert `_open_readonly_db(path)` returns a connection that raises on
   `INSERT`/`CREATE` (read-only enforced).
4. Assert `NineRouterManager.usage("auto")` returns a dict with keys
   `object, available, quotas` (using the test double or a monkeypatched
   `_request`).
5. (Optional canary) import `_get_usage_analytics`/`_get_models_analytics` from
   `hermes_cli.web_server` and call them against a temp profile home, asserting a
   dict comes back — flags upstream drift without making them a runtime dep.

## Phase 1 — Models

File: `brain4all/models/api.py`. Add (see architecture.md for fields):

- `AgentBudgetPatch(BaseModel)` — the PUT body. Validators: amounts `>= 0` or
  `None`; `0 < warn_threshold_percent <= 100`; `cost_basis in {"estimated",
  "actual"}`.
- Response DTOs `UsageTotals`, `ModelUsage`, `BucketUsage`, `AgentUsage`,
  `BudgetStatus`, `QuotaOverlay`, `UsageSummary` (used internally to build/
  validate dicts; the envelope serializes plain dicts).

Export the new names in `brain4all/models/__init__.py` (add to the existing
`from .api import (...)` block and `__all__`), following the Kanban model export
style.

## Phase 2 — Integration + service

### 2a. `brain4all/integrations/analytics.py` (new)

Read-only aggregation. No policy, no HTTP. Sketch:

```python
from __future__ import annotations
import sqlite3
from pathlib import Path
from typing import Any

# Columns we SUM. Keep in sync with the compatibility test.
_TOKEN_COLS = ("input_tokens", "output_tokens", "cache_read_tokens",
               "cache_write_tokens", "reasoning_tokens")

def _open_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=1.0)
    conn.row_factory = sqlite3.Row
    return conn

def aggregate_profile(profile_dir: Path, *, cutoff_epoch: float) -> dict[str, Any]:
    db = profile_dir / "state.db"
    empty = {"totals": {}, "by_model": [], "by_day": []}
    if not db.is_file():
        return empty
    conn = _open_ro(db)
    try:
        totals = dict(conn.execute(f"""
            SELECT COALESCE(SUM(input_tokens),0) AS input_tokens,
                   COALESCE(SUM(output_tokens),0) AS output_tokens,
                   COALESCE(SUM(cache_read_tokens),0) AS cache_read_tokens,
                   COALESCE(SUM(cache_write_tokens),0) AS cache_write_tokens,
                   COALESCE(SUM(reasoning_tokens),0) AS reasoning_tokens,
                   COALESCE(SUM(estimated_cost_usd),0) AS estimated_cost_usd,
                   COALESCE(SUM(actual_cost_usd),0) AS actual_cost_usd,
                   COUNT(*) AS sessions,
                   COALESCE(SUM(api_call_count),0) AS api_calls
            FROM sessions WHERE started_at > ?""", (cutoff_epoch,)).fetchone())
        by_model = [dict(r) for r in conn.execute(f"""
            SELECT COALESCE(model,'unknown') AS model,
                   COALESCE(billing_provider,'') AS provider,
                   COALESCE(SUM(input_tokens),0) AS input_tokens,
                   COALESCE(SUM(output_tokens),0) AS output_tokens,
                   COALESCE(SUM(estimated_cost_usd),0) AS estimated_cost_usd,
                   COALESCE(SUM(actual_cost_usd),0) AS actual_cost_usd,
                   COUNT(*) AS sessions
            FROM sessions WHERE started_at > ?
            GROUP BY model, billing_provider""", (cutoff_epoch,)).fetchall()]
        by_day = [dict(r) for r in conn.execute(f"""
            SELECT date(started_at,'unixepoch') AS bucket,
                   COALESCE(SUM(input_tokens),0) AS input_tokens,
                   COALESCE(SUM(output_tokens),0) AS output_tokens,
                   COALESCE(SUM(estimated_cost_usd),0) AS estimated_cost_usd,
                   COALESCE(SUM(actual_cost_usd),0) AS actual_cost_usd,
                   COUNT(*) AS sessions
            FROM sessions WHERE started_at > ?
            GROUP BY bucket ORDER BY bucket""", (cutoff_epoch,)).fetchall()]
        return {"totals": totals, "by_model": by_model, "by_day": by_day}
    except sqlite3.Error:
        return empty          # missing column / locked / corrupt -> zeroes
    finally:
        conn.close()

def period_spend(profile_dir: Path, *, since_epoch: float, cost_basis: str) -> float:
    ...  # single SUM of the chosen cost column since a period start; 0 on error
```

Notes: never `SELECT *`; select only numeric/dimension columns (no
`system_prompt`, `title`, or message content). If a column is missing on an old
DB the whole `try` degrades to zeroes.

### 2b. `brain4all/services/analytics.py` (new)

`AnalyticsService(agents: AgentManager, router: NineRouterManager)`. Duties:

1. `usage_summary(days, bucket)`:
   - `cutoff = time.time() - days*86400`.
   - `agents_list = self.agents.list_agents()["agents"]`.
   - For each agent, resolve `profile_dir` (use an `AgentManager` accessor;
     do not build from raw slug) and call
     `await asyncio.to_thread(integ.aggregate_profile, profile_dir,
     cutoff_epoch=cutoff)`.
   - Merge: overall totals, `agents: [AgentUsage]`, `by_model` (sum across
     agents by `(model, provider)`), and a **dense** `series` (fill every
     day/week bucket in the window with zeroes so the chart has no gaps).
   - Week bucketing: fold `by_day` into ISO weeks (`YYYY-Www`, UTC) in Python.
   - `cost_basis`/`cost_usd`: for each totals/model/bucket, pick `actual` when
     `actual_cost_usd > 0` (final), else `estimated`; keep both figures.
   - Overlay: `quota = await self._quota_overlay()` — best-effort
     `self.router.usage(default_model)`, `except NineRouterAPIError -> {available:
     False, quotas: []}`.
   - Attach each agent's `budget` block via `self._budget_status(agent_id)`.
   - Wrap in a ~15–30s TTL cache keyed `(days, bucket)`; expose `invalidate()`.
2. `agent_usage(agent_id, days, bucket)`: same as above for one agent (404 via
   `ServiceError` if the agent does not exist).
3. `models_breakdown(days)`: the merged `by_model` alone.
4. `timeseries(days, bucket)`: the dense `series` alone.
5. `get_budget(agent_id)` / `set_budget(agent_id, patch)` — Phase 4.

Raise `ServiceError` (from `platform.py`) for unknown agents / bad params so the
handler maps them to the failure envelope. Shape responses with numeric/
dimension fields only.

### 2c. Wire into `PlatformService`

File: `brain4all/services/platform.py`, in `__init__` next to
`self.kanban = KanbanService(agents)`:

```python
from .analytics import AnalyticsService
self.analytics = AnalyticsService(self.agents, self.router)
```

Export `AnalyticsService` from `brain4all/services/__init__.py` (add to the
`from .analytics import ...` line and `__all__`).

## Phase 3 — Handlers + routes

### 3a. Handlers

File: `brain4all/handlers/api.py`, add to the `operations` dict in `_operation`
(these mirror the async kanban entries; the dispatcher already awaits awaitables):

```python
"analytics_usage": (lambda: s.analytics.usage_summary(
    _int(q.get("days"), 30), str(q.get("bucket") or "day")),
    "usage analytics retrieved successfully", 200),
"analytics_agent_usage": (lambda: s.analytics.agent_usage(
    p["agent_id"], _int(q.get("days"), 30), str(q.get("bucket") or "day")),
    "agent usage retrieved successfully", 200),
"analytics_models": (lambda: s.analytics.models_breakdown(_int(q.get("days"), 30)),
    "model usage retrieved successfully", 200),
"analytics_timeseries": (lambda: s.analytics.timeseries(
    _int(q.get("days"), 30), str(q.get("bucket") or "day")),
    "usage timeseries retrieved successfully", 200),
"analytics_budget_get": (lambda: s.analytics.get_budget(p["agent_id"]),
    "budget retrieved successfully", 200),
"analytics_budget_set": (lambda: s.analytics.set_budget(p["agent_id"], body),
    "budget updated successfully", 200),
```

Add a small `_int(value, default)` clamp helper (bound `days` to e.g. 1..365) if
one is not already present.

Optionally upgrade the existing `conversations_usage` stub to compute one
session's real totals via the read-only helper (small, same discipline) — nice
to have, not required.

### 3b. Routes

File: `brain4all/routes/setup.py`. Add an Analytics group to the `ROUTES` tuple
(place near the Cron/Kanban blocks):

```python
Route("GET", "/agent-gateway/v1/analytics/usage", "analytics_usage", tags=("Analytics",)),
Route("GET", "/agent-gateway/v1/analytics/models", "analytics_models", tags=("Analytics",)),
Route("GET", "/agent-gateway/v1/analytics/timeseries", "analytics_timeseries", tags=("Analytics",)),
Route("GET", "/agent-gateway/v1/analytics/agents/{agent_id}/usage", "analytics_agent_usage", tags=("Analytics",)),
Route("GET", "/agent-gateway/v1/analytics/agents/{agent_id}/budget", "analytics_budget_get", tags=("Analytics",)),
Route("PUT", "/agent-gateway/v1/analytics/agents/{agent_id}/budget", "analytics_budget_set", AgentBudgetPatch, tags=("Analytics",)),
```

Import `AgentBudgetPatch` in the `from ..models import (...)` block at the top of
`setup.py`. GET routes carry no body and flow through the default
`handlers.dispatch(request, {})` path; the PUT with `AgentBudgetPatch` flows
through the body path already handled by `_endpoint`. No `special` handling
needed. All ride `APIEnvelope` (they are not raw-response routes).

## Phase 4 — Budgets (advisory, config.yaml, snapshot)

In `AnalyticsService`:

- `get_budget(agent_id)`: read the agent's `config.yaml` `budget` block
  (via an `AgentManager` read helper or `platform`'s config read), compute
  `spend_usd` for the current period (calendar month, UTC) and `daily_spend_usd`
  via `integ.period_spend(...)`, and return a `BudgetStatus`: `percent_used`,
  and `status` = `unset` (no cap) | `ok` | `warning` (>= warn threshold) |
  `exceeded` (>= cap). Always `advisory: true`.
- `set_budget(agent_id, patch)`: validate via `AgentBudgetPatch`;
  **snapshot then write**:
  1. `snapshot(agent_id, "config", "config", <current config.yaml bytes>)` via
     `FileRepository` (reuse the exact path `platform.update_agent_config` uses).
  2. Merge the `budget` key into the config and write atomically (reuse
     `AgentManager`'s atomic YAML writer / the config-write path). `monthly_usd:
     null` clears the cap.
  3. `self.invalidate()` the TTL cache.
  4. Return `get_budget(agent_id)`.

The budget path writes only `config.yaml` — never `state.db`. Evaluation is
read-only and never blocks execution.

## Phase 5 — Frontend

- `src/src/api/analytics.ts` (new): typed client for the six routes, mirroring
  `src/src/api/kanban.ts` and using `src/src/api/client.ts`.
- `src/src/hooks/useAnalytics.ts` (new): fetch summary + budgets; expose
  `days`/`bucket` controls and a `setBudget` mutation; mirror `useKanban.ts`
  patterns (loading/empty/partial-failure/offline states).
- `src/src/features/analytics/` (new):
  - `AnalyticsDashboard.tsx` — layout + states.
  - `TotalsTiles.tsx` — token/cost/session tiles + quota window(s), estimate tag.
  - `ModelBars.tsx` — per-model horizontal bars.
  - `UsageChart.tsx` / `Chart.tsx` — **inline SVG** line/bar (no new dependency),
    accessible + theme-aware, with day/week + window toggles.
  - `BudgetBar.tsx` — per-agent spend-vs-cap bar with status color and an edit
    dialog PUTting `AgentBudgetPatch`; label it "advisory — not enforced".
- Navigation: add an Analytics destination consistent with the current nav rules
  (Settings section or top-level), per `plans/CHECKLIST.md` 002. Translate new
  strings in every shipped locale (`src/src/locales`).
- Type/build check with `cd src && npm run build`.

## Phase 6 — Tests and docs

See [validation.md](validation.md) for the full test matrix. Add:

- `brain4all/tests/test_analytics.py` — compatibility (Phase 0) + aggregation
  math on **real** session rows written into a temp `HERMES_HOME` (no mocks of
  the DB), including per-agent isolation, per-model, day/week bucketing,
  estimated-vs-actual `cost_basis`, empty-profile zeroes, and a read-only-safety
  assertion (state.db mtime unchanged after a summary call).
- Handler/route tests via `httpx.ASGITransport` (as `test_kanban.py` /
  `test_fastapi.py` do) for success, validation, and 404.
- A budget test: set a low cap, run enough real sessions to exceed it, assert
  status flips to `warning`/`exceeded`, assert no chat/run was blocked, and
  assert `config.yaml` got a snapshot.
- Frontend: component/hook tests next to the files (mirror `kanban`/`sandbox`
  test style).
- Docs: update `docs/api.md` (routes + query params + response shapes),
  `docs/architecture.md` (compute-on-read, read-only, advisory budgets,
  estimated-vs-actual caveat). Do not add a static `docs/openapi.yaml`.

## Ordering summary

Phase 0 -> 1 -> 2 -> 3 -> 4 -> 5 -> 6. Backend (0–4) can land and be tested
before the frontend (5). Keep each change minimal and within the existing
layer boundaries.
