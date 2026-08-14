# 009 — Validation

How completion is proven. A checkbox is done only with the evidence beside it.
Cross-links: [README.md](README.md), [implementation.md](implementation.md),
[findings.md](findings.md).

## 1. Compatibility test (schema + helpers)

`xnobrain/tests/test_analytics.py`, skipped when Hermes is unavailable (as
`test_kanban.py` does).

- `PRAGMA table_info(sessions)` on a freshly created profile contains the
  accounting columns from [findings.md](findings.md) §1
  (`started_at, model, billing_provider, input_tokens, output_tokens,
  cache_read_tokens, cache_write_tokens, reasoning_tokens, estimated_cost_usd,
  actual_cost_usd, cost_status, api_call_count`).
- `_open_readonly_db(path)` connection raises on `INSERT`/`CREATE` (read-only).
- `NineRouterManager.usage("auto")` returns `{object, available, quotas, ...}`.
- (Canary) native `_get_usage_analytics`/`_get_models_analytics` import and
  return a dict against a temp home.

Evidence: test names + a passing run.

## 2. Unit tests — aggregation math on real records

No mocked DB. Write real `sessions` rows into temp-`HERMES_HOME` profiles (via
the real `SessionDB`/schema or direct inserts into the real schema), then assert
the service output.

- **Totals** equal the hand-summed columns across all agents in the window.
- **Per-agent isolation**: agent A's usage view sums only A's rows; A's numbers
  plus B's equal the cross-agent totals.
- **Per-model**: `by_model` sums match per-`(model, provider)` hand totals;
  null model/provider fall into the `unknown`/`""` bucket.
- **Time-series**: day buckets match `date(started_at,'unixepoch')` grouping;
  week buckets fold days into the right ISO week (UTC); the series is **dense**
  (every window bucket present, empty ones zero).
- **Cost basis**: a row with a final `actual_cost_usd` reports `cost_basis:
  actual` and uses it; a row with only `estimated_cost_usd` reports `estimated`;
  totals expose both figures; nulls read as 0.
- **Empty profile**: an agent with no rows returns zeroes, not mock data.
- **Window filter**: a row older than `days` is excluded; one inside is
  included (boundary test).

Evidence: assertions comparing service output to independently computed sums.

## 3. Read-only safety

- Record `state.db` `st_mtime` (and size) before and after `usage_summary`,
  `agent_usage`, `models_breakdown`, `timeseries`, and `get_budget`; assert
  unchanged.
- Assert the integration only ever opens `file:...?mode=ro` (e.g. patch/inspect
  the connect call, or assert a write attempt through the analytics connection
  raises `sqlite3.OperationalError`).

Evidence: mtime/size unchanged; write-attempt raises.

## 4. Integration tests — routes via ASGI

Use `httpx.ASGITransport`/`AsyncClient` against the assembled app (as
`test_kanban.py`/`test_fastapi.py` do):

- `GET /api/brain/v1/analytics/usage?days=30&bucket=day` -> 200 envelope
  with `totals, agents, by_model, series, quota`.
- `bucket=week` returns weekly buckets.
- `GET /api/brain/v1/analytics/agents/{agent_id}/usage` -> 200; unknown
  agent -> 404 failure envelope.
- `GET /api/brain/v1/analytics/models` and `.../timeseries` -> 200.
- Invalid `days` (e.g. `-1`, `abc`) is clamped or -> 400; documented behavior
  covered by a test.
- `PUT /api/brain/v1/analytics/agents/{agent_id}/budget` with a valid body
  -> 200 and the returned `BudgetStatus`; invalid body (negative amount,
  `warn_threshold_percent > 100`) -> 400.
- 9router unavailable: monkeypatch `router.usage` to raise
  `NineRouterAPIError`; the summary still returns 200 with `quota.available =
  false`.

Evidence: status codes + envelope assertions.

## 5. Budget advisory behavior

- Set `monthly_usd` below current spend; `get_budget` (and the agent block in
  `usage`) returns `status: exceeded`, `advisory: true`.
- Set a cap so spend is between warn threshold and cap -> `status: warning`.
- Clear the cap (`monthly_usd: null`) -> `status: unset`.
- **No enforcement**: after setting an exceeded budget, a chat/run/task still
  succeeds (assert a chat call is not rejected by the budget). Hard enforcement
  is Enterprise (`docs/enterprise-extension.md`).
- **Snapshot on write**: after `set_budget`, a config snapshot exists for the
  agent (check the snapshot store the way existing config-mutation tests do).
- Budget writes touch only `config.yaml`; `state.db` mtime unchanged (§3).

Evidence: status transitions, unblocked chat, snapshot present.

## 6. No sensitive data exposed

- Assert no analytics response or DTO contains `system_prompt`, session `title`,
  message `content`, tool arguments/output, `Authorization`, or provider keys —
  only numeric counters and the `model`/`provider` dimensions.
- Assert logs emitted by the analytics path contain no prompts/content/keys
  (structured identifiers only), per `AGENTS.md`.

Evidence: response-key allowlist test + log inspection.

## 7. Suite and smoke

- `make test` (or focused `python -m pytest xnobrain/tests/test_analytics.py`)
  passes.
- `make check` passes (backend + lint + frontend type/build).
- `npm run build` passes (inline SVG chart, no new dependency added —
  verify `package.json` diff adds no chart lib).
- `make smoke-api`: the new routes respond over the real stack.

Evidence: command output showing success (read it; do not assume).

## 8. Manual end-to-end

1. `make run`.
2. Create two agents; run a couple of real chats on each (different models if
   possible).
3. Open the Analytics dashboard:
   - Totals tiles show non-zero tokens/cost/sessions; cost is tagged
     "estimated" when appropriate.
   - Per-model bars list the models actually used.
   - The time chart shows the days/weeks with activity; day/week toggle works.
   - Per-agent numbers add up to the cross-agent totals.
4. Set a very low budget on one agent; confirm its budget bar turns
   warning/exceeded, and that a further chat on that agent still runs (advisory).
5. Cross-check one agent's totals against a manual read of its `state.db`
   (`SELECT SUM(input_tokens)+SUM(output_tokens) FROM sessions WHERE started_at >
   cutoff`) — the numbers match.

Evidence: screenshots/notes; the manual SQL sum equals the API total.

## Acceptance checklist

- [ ] Compatibility test asserts the `sessions` accounting columns exist
  (evidence: passing `test_analytics.py::test_schema_columns`).
- [ ] Analytics opens `state.db` read-only only; `state.db` never modified
  (evidence: mtime/size unchanged; write-attempt raises).
- [ ] Cross-agent totals, per-agent, per-model, and day/week series match
  hand-computed sums of real records (evidence: unit-test assertions).
- [ ] Per-agent usage matches that agent's rows alone (evidence: isolation test).
- [ ] **Agent multi-select:** absent/empty `agents` = all agents; a subset scopes
  totals/per-model/series to exactly those agents — subset totals equal the sum of
  the selected agents' per-agent totals and nothing else (evidence: subset test).
- [ ] **Subset reduces reads:** selecting K of N agents opens only those K
  `state.db` files (evidence: read-count / open-call assertion).
- [ ] **`GET /analytics/agents`** lists selectable agents (id + display name)
  without opening any `state.db` (evidence: works with zero session rows; no read).
- [ ] **Time range:** relative `days` and absolute `from`/`to` both resolve
  correctly, `from`/`to` override `days`, and `start >= end` → 400 (evidence: range
  tests).
- [ ] **Bucket granularity:** `hour`/`day`/`week`/`month` each yield a correct
  **dense** series across the range; `week` folds to ISO weeks (evidence: bucket
  tests).
- [ ] `cost_basis` prefers final actual, else estimated, both exposed (evidence:
  cost-basis test).
- [ ] Routes return 200/400/404 correctly through `APIEnvelope` (evidence: ASGI
  tests).
- [ ] 9router overlay degrades gracefully on outage (evidence: monkeypatch test).
- [ ] Advisory budget set/get works, snapshots config, and never blocks
  execution (evidence: budget tests).
- [ ] No prompts/content/keys in responses or logs (evidence: allowlist + log
  test).
- [ ] No new persistent store or DB added; no new API process; provider forced
  9router (evidence: code review + `package.json` and deps diff).
- [ ] `make check` and `make smoke-api` pass (evidence: command output).
- [ ] Manual E2E: real chats -> visible totals/per-model/time + a budget warning
  (evidence: notes + matching manual SQL sum).
- [ ] Manual E2E controls: default view shows **All agents**; selecting a subset
  updates every panel; switching a preset range and a custom from/to range and a
  bucket all re-query and re-render; the selection survives a page reload
  (evidence: notes/screenshots).
