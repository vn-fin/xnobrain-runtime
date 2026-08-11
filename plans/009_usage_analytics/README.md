# 009 — Usage analytics and budgets

Priority: P2. Depends on the Hermes pin and read-only session helpers proven in
plan 001. Ships independently of plans 005–008.

Read the sibling documents in order:

- [findings.md](findings.md) — where the data lives and the exact gap.
- [architecture.md](architecture.md) — layering, API contract, data flow.
- [approaches.md](approaches.md) — options considered and the chosen one.
- [implementation.md](implementation.md) — ordered, file-by-file steps.
- [validation.md](validation.md) — how completion is proven.

Also read before starting: [`AGENTS.md`](../../AGENTS.md),
[`brain4all/routes/setup.py`](../../brain4all/routes/setup.py),
[`brain4all/integrations/hermes.py`](../../brain4all/integrations/hermes.py)
(session `state.db` schema and read-only helpers),
[`brain4all/integrations/nine_router.py`](../../brain4all/integrations/nine_router.py)
(`usage()` quota overlay), and
[`docs/enterprise-extension.md`](../../docs/enterprise-extension.md) (why hard
enforcement stays out of the OSS edition).

## Goal

Give a local, self-hosted operator a truthful view of token and cost spend, and
an advisory way to cap it:

- Aggregate token and cost totals **per agent**, **per model**, and **over time**
  (day and week buckets), computed across every agent's Hermes session
  `state.db`, opened read-only.
- Overlay the current provider quota windows already returned by 9router
  (`NineRouterManager.usage()`).
- Let the user set an optional **per-agent budget cap** that produces a **soft
  warning** when the current period's spend crosses it. The cap is advisory
  only; nothing is blocked.

Today Brain4All exposes only per-conversation usage, and that route is a
zero-valued stub
([`brain4all/handlers/api.py`](../../brain4all/handlers/api.py) operation
`conversations_usage`). There is no aggregation, no per-model or time breakdown,
and no budget concept. This plan closes that gap.

## Non-goals

- **No hard enforcement locally.** Budgets never reject a chat, a run, or a
  task. Hard quota enforcement is Enterprise-only (see
  [`docs/enterprise-extension.md`](../../docs/enterprise-extension.md): quota
  middleware and the control-plane billing source of truth). Local caps stay
  advisory.
- **No new database and no new persistent store.** Aggregation is computed on
  read from each profile's existing `state.db`. An in-process, time-bounded
  memory cache is allowed (it is not persistence); no file or table is created
  to hold aggregates.
- **No writes to Hermes `state.db`.** Every session read uses the read-only URI
  (`file:...?mode=ro`) helper. Analytics is a pure reader.
- **No second API process, no Go, no PostgreSQL, no ORM.** One FastAPI/Hermes
  process on `:8642`, one 9router on `:20128`.
- **No forking or copying Hermes internals.** We may ride the pinned Hermes
  native analytics endpoints for compatibility testing, but the core computes
  from the columns Brain4All already guarantees.
- **No mock or demo analytics.** Every number must trace to a real session
  record. Empty ranges return zeroes, not synthetic data.

## Scope

In:

1. A read-only aggregation integration that opens each agent's `state.db` with
   `?mode=ro` and sums the session accounting columns within a time window.
2. An `AnalyticsService` that shapes totals, per-agent, per-model, and
   time-series (day/week) views, folds in the 9router quota overlay, and
   evaluates advisory budgets.
3. Advisory per-agent budget config stored in the agent's own `config.yaml`
   (snapshot-before-write), read back with computed spend and a status.
4. New versioned Brain4All routes under an `Analytics` tag.
5. A React Analytics dashboard: totals tiles, per-model bars, a time chart, and
   a per-agent budget bar, using a lightweight inline chart (no new dependency).
6. Tests against real session records in a temporary `HERMES_HOME`.

Out: aux per-task breakdown (`session_model_usage`), provider account-credit
overlays (`agent/account_usage.py`), and model-capability enrichment
(`agent/models_dev.py`). These are optional Hermes extras noted in
[findings.md](findings.md) and deferred; they are not required for the gap this
plan closes.

## Phase overview

- **Phase 0 — Pin and prove.** Extend the compatibility test to assert the
  `sessions` accounting columns exist, the read-only helpers work, and
  `NineRouterManager.usage()` is callable. Optionally smoke the native
  `/api/analytics/usage` and `/api/analytics/models` endpoints for reference.
- **Phase 1 — Models.** Request/response Pydantic models for usage views and the
  budget patch.
- **Phase 2 — Integration + service.** `brain4all/integrations/analytics.py`
  (read-only aggregation) and `brain4all/services/analytics.py` (shaping,
  budget evaluation, quota overlay). Wire `self.analytics` in `platform.py`.
- **Phase 3 — Handlers + routes.** Operations and `Route(...)` lines in the
  single route-assembly point.
- **Phase 4 — Budgets.** Advisory get/set in `config.yaml` with snapshot.
- **Phase 5 — Frontend.** Dashboard components, a hook, an API client, an inline
  chart, and navigation.
- **Phase 6 — Tests and docs.** Real-record unit/integration tests, `make check`,
  smoke, and doc updates.

Each phase's file-by-file steps are in [implementation.md](implementation.md).

## Definition of done

- Running a couple of real chats on two different agents, then reading the new
  routes, returns non-zero totals that sum correctly per agent, per model, and
  per day/week, and match hand-computed sums of the underlying `sessions` rows.
- The per-agent usage view matches that agent's `state.db` rows alone.
- Setting a low per-agent budget and exceeding it flips that agent's budget
  status to `warning`/`exceeded` in the response, and **no chat or run is
  blocked**.
- No route or log ever exposes prompts, message content, tool arguments/output,
  credentials, or provider keys.
- A test proves `state.db` is opened read-only and never written by the
  analytics path.
- Compatibility test, focused unit/integration tests, and `make check` pass.
- Docs (`docs/api.md`, `docs/architecture.md`) describe the routes, the
  compute-on-read model, the estimated-vs-actual cost caveat, and the advisory
  nature of budgets.
</content>
