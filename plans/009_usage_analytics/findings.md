# 009 — Findings

What data exists, where it lives, what XNOBrain already does with it, and the
exact gap this plan closes. Cross-links: [README.md](README.md),
[architecture.md](architecture.md), [approaches.md](approaches.md).

## 1. Per-agent session accounting lives in each profile's `state.db`

Every agent is one Hermes profile directory. `AgentManager`
([`xnobrain/integrations/hermes.py`](../../xnobrain/integrations/hermes.py))
places native profiles under `profiles_root/<agent-id>/` (default
`~/.hermes/profiles/<agent-id>/`) plus the root profile `~/.hermes/` and any
legacy profiles under `legacy-agents/<name>/.profile/`. Each profile owns a
SQLite file `state.db`.

The `sessions` table is created by `AgentManager._ensure_session_schema`
(hermes.py, ~line 1514). Every row is one conversation/run. The **exact column
list** XNOBrain guarantees:

```
id, source, user_id, model, model_config, system_prompt, parent_session_id,
started_at, ended_at, end_reason, message_count, tool_call_count,
input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
reasoning_tokens, billing_provider, billing_base_url, billing_mode,
estimated_cost_usd, actual_cost_usd, cost_status, cost_source,
pricing_version, title, api_call_count
```

The token/cost columns that matter for analytics:
`input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
reasoning_tokens` (INTEGER, default 0); `estimated_cost_usd, actual_cost_usd`
(REAL, nullable); `cost_status, cost_source, pricing_version` (cost provenance);
`model` and `billing_provider` (grouping dimensions); `api_call_count`,
`tool_call_count`, `message_count`; and `started_at` (REAL unix epoch, indexed
`idx_sessions_started ON sessions(started_at DESC)`), which is the time axis.

The real Hermes runtime (invoked as a subprocess with
`HERMES_HOME=<profile_dir>`) writes these counters as chats run. XNOBrain's own
schema only creates `sessions` and `messages`; richer Hermes tables such as
`session_model_usage` (auxiliary per-model/per-task usage) may or may not exist
depending on the runtime version, so analytics must not depend on them.

## 2. XNOBrain already reads `state.db` read-only — reuse those helpers

hermes.py already has exactly the read primitives analytics needs, and they are
strictly read-only:

- `_open_readonly_db(path)` (~line 1755) opens
  `sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=1.0)`
  with `row_factory = sqlite3.Row`. This is the **only** way analytics should
  touch `state.db`.
- `_sessions(profile_dir, limit=...)` and `_session(profile_dir, session_id)`
  read rows through that connection, swallow `sqlite3.Error`, and always
  `close()`.
- `_row_dict(row)` normalizes a row to a dict (JSON-decoding `*_config`).
- `_preferred_order_column` tolerates schema drift.

These prove the pattern: open `?mode=ro`, `execute` a `SELECT`, `close()`. The
analytics integration follows the same shape but issues aggregate SQL
(`SUM(...) GROUP BY ...`) instead of `SELECT *`.

## 3. What XNOBrain exposes today — and what it lacks

Present: `conversations_usage`
([`xnobrain/handlers/api.py`](../../xnobrain/handlers/api.py) ~line 86) is a
**stub** wired at
`GET /api/brain/v1/conversations/{conversation_id}/usage`
([`routes/setup.py`](../../xnobrain/routes/setup.py) ~line 77). It returns a
hard-coded zero payload:

```python
"conversations_usage": (lambda: {"conversation_id": p["conversation_id"],
    "tokens": {"total": 0}, "cost": {"total_usd": 0}}, ...)
```

It does not read `state.db` and is per-conversation only.

Lacking entirely:

- Aggregation across sessions (no totals).
- Per-agent attribution across many `state.db` files.
- Per-model breakdown.
- Time-series (day/week).
- Any budget concept.

**The exact gap:** there is no code path that opens each agent's `state.db`,
sums the accounting columns within a window, and returns per-agent / per-model /
over-time views, and there is no advisory budget. This plan adds one integration
+ one service + routes + a dashboard to do exactly that. (Optionally, the
`conversations_usage` stub can be upgraded to compute a single session's real
totals with the same read helper; noted in implementation.md as a small extra.)

## 4. 9router usage overlay

`NineRouterManager.usage(model)`
([`xnobrain/integrations/nine_router.py`](../../xnobrain/integrations/nine_router.py)
~line 274) returns filtered provider **quota windows** for the provider behind a
model: `{ object, available, provider, model, plan, message, quotas: [{name,
used, total, remaining_percent, reset_at, unlimited}] }`. This is provider-side
remaining allowance, not historical token spend. It is the correct overlay for
"how much of my plan is left", complementary to the computed spend from
`state.db`. It is already filtered and safe to surface (no keys). Analytics
calls it best-effort; a 9router outage must degrade gracefully (empty overlay),
never fail the whole response.

## 5. Hermes native analytics endpoints (reference, not the core source)

[`.tools/hermes-agent/hermes_cli/web_server.py`](../../.tools/hermes-agent/hermes_cli/web_server.py):

- `GET /api/analytics/usage?days&profile` -> `_get_usage_analytics` (~16490):
  returns `daily` (grouped by `date(started_at,'unixepoch')`), `by_model`,
  `by_task`, `totals`, plus `skills`/`tools`. SQL sums the same `sessions`
  columns XNOBrain guarantees.
- `GET /api/analytics/models?days&profile` -> `_get_models_analytics` (~16576):
  per-model token/cost/session rows enriched with `agent/models_dev`
  capabilities.

Important limits that make these unsuitable as XNOBrain's *primary* source:

1. **Single profile only.** Both call `_open_session_db_for_profile(profile)`
   (~11434), which resolves one profile home via `_cron_profile_home(profile)`
   and opens that one `state.db`. There is no cross-agent aggregation, which is
   the central thing XNOBrain needs.
2. **Extra dependencies.** `_get_usage_analytics` imports `agent.insights.
   InsightsEngine`; `by_task` reads `session_model_usage`; `_get_models_analytics`
   imports `agent.models_dev`. None are guaranteed for XNOBrain-created DBs and
   they broaden the surface we must pin/test.
3. **Not `?mode=ro`.** They use `SessionDB(...)._conn`, a writable connection.
   XNOBrain's constraint is read-only URI access.
4. **Profile resolution mismatch.** `_cron_profile_home` may not resolve
   XNOBrain's `profiles_root/<agent-id>` identically to `AgentManager`.

Conclusion: pin and smoke these for compatibility/reference, but compute the
XNOBrain views on read from the guaranteed columns. See
[approaches.md](approaches.md).

## 6. Budget config anchor — corrected

`.tools/hermes-agent/tools/budget_config.py` was listed as a budget anchor. On
inspection it is **not** a spend-budget module: it defines `BudgetConfig`
(`turn_budget`, `resolve_threshold`) governing **tool-result character budgets**
for the 3-layer tool-result persistence system (`DEFAULT_TURN_BUDGET_CHARS =
200_000`, `budget_for_context_window(...)`). It has nothing to do with token or
dollar spend caps.

Therefore per-agent **spend** budgets are XNOBrain-owned. They are advisory
config stored in the agent's `config.yaml` (written atomically with a snapshot,
the same mutation discipline `update_agent_config` uses). Do not try to derive
spend caps from `budget_config.py`.

## 7. Account-usage overlay (optional, deferred)

`agent/account_usage.py::fetch_account_usage(...)` fetches provider **account**
credit/quota (Nous credits, Codex, Anthropic, OpenRouter). It is
provider-account level and overlaps `NineRouterManager.usage()` for the local
flow. Keep 9router `usage()` as the primary overlay; treat `account_usage` as an
optional future overlay, out of scope here.

## 8. Accuracy caveats

- **Estimated vs actual cost.** `estimated_cost_usd` is present from pricing
  tables; `actual_cost_usd` is populated only when the provider reports a final
  billed amount (and `cost_status`/`cost_source` describe provenance). Many rows
  will have `estimated` but null `actual`. The service must: prefer `actual`
  when present and final, else fall back to `estimated`, expose a `cost_basis`
  discriminator per row/total, and never present estimates as billed truth.
- **Cache and reasoning tokens** are separate columns; "total tokens" must state
  what it includes (recommend `input + output`; report cache/reasoning
  separately) to avoid double meaning.
- **Nullable costs.** Use `COALESCE(SUM(col), 0)`; a null must read as 0.
- **Zero data.** New/empty profiles have no rows; return zeroes, never mock.
- **Clock/timezone.** `started_at` is unix epoch. Day/week bucketing must fix a
  timezone (UTC by default, matching Hermes' `date(started_at,'unixepoch')`);
  document it so the UI labels are unambiguous.
- **Legacy/foreign rows.** Rows written by the native CLI or by Hermes tooling
  count too — that is desired (analytics reflects all real sessions), but the
  `model`/`billing_provider` may be free-form; group defensively (null -> a
  stable `"unknown"` bucket).

## 9. Compatibility surfaces to pin and test

Pin the Hermes commit (per plan 001) and add assertions that:

1. `AgentManager._ensure_session_schema` still creates the accounting columns in
   section 1 (the analytics SQL depends on them). Assert via
   `PRAGMA table_info(sessions)`.
2. `_open_readonly_db` opens a `?mode=ro` connection that rejects writes.
3. `NineRouterManager.usage(model)` returns the documented shape.
4. (Optional, reference) the native `/api/analytics/usage` and
   `/api/analytics/models` endpoints import and return a dict on a temporary
   home — a canary for upstream drift, not a runtime dependency.

## 10. Risks

- **Schema drift.** A future Hermes could rename/drop an accounting column. The
  compatibility test in section 9 is the guard; the integration must degrade a
  missing column to 0 rather than 500.
- **Many `state.db` reads.** N agents = N file opens per request. Bound with a
  window filter, the `started_at` index, a short in-memory TTL cache, and
  `asyncio.to_thread` for the blocking reads. See
  [architecture.md](architecture.md) "Performance".
- **Locked/corrupt DB.** A concurrent writer may briefly lock a file; `?mode=ro`
  + `timeout=1.0` + swallow-`sqlite3.Error`-to-empty (as `_sessions` already
  does) keeps one bad profile from failing the aggregate.
- **Cost realism.** Presenting estimates as truth misleads spend decisions;
  mitigated by the `cost_basis` discriminator (section 8).
- **Leakage.** `sessions` holds `system_prompt` and `title`; `messages` holds
  content. Analytics must select only numeric/dimension columns and never
  `SELECT *` into a response.

## 11. Open questions

- Default window: 30 days (matches Hermes) — confirm with product.
- Week bucketing boundary (ISO week vs 7-day rolling) — recommend ISO week, UTC.
- Should the "default" root profile (`~/.hermes`) appear as its own agent row in
  cross-agent totals, or be excluded? Recommend excluding non-agent profiles and
  aggregating exactly the set `AgentManager.list_agents()` returns, for a
  consistent per-agent axis.
- Budget period: calendar month vs rolling 30 days — recommend calendar month
  (UTC), with an optional `daily_usd` sub-cap.
