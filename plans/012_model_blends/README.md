# 012 — Model Blends

Priority: P2. Ships independently. Soft dependency: plan 011 (provider
connections) improves multi-account behavior *behind* a blend but is not
required — blends work against whatever providers are connected today.

Read the sibling documents in order:

- [findings.md](findings.md) — verified upstream 9router facts, the exact
  XNOBrain gap, and the Phase-0 probe list.
- [architecture.md](architecture.md) — layering, adapter signatures, API
  contract, UI sketch, data flow.
- [approaches.md](approaches.md) — decisions (naming, strategy placement,
  "auto" protection, failure behavior, fusion exposure) and the chosen paths.
- [implementation.md](implementation.md) — ordered, file-by-file steps.
- [validation.md](validation.md) — how completion is proven.

Also read before starting: [`AGENTS.md`](../../AGENTS.md),
[`plans/LOCAL_FEATURES_CHECKLIST.md`](../LOCAL_FEATURES_CHECKLIST.md) (program
principles),
[`xnobrain/routes/setup.py`](../../xnobrain/routes/setup.py) (only route
assembly point),
[`xnobrain/integrations/nine_router.py`](../../xnobrain/integrations/nine_router.py)
(the adapter this plan extends), and
[`xnobrain/integrations/hermes.py`](../../xnobrain/integrations/hermes.py)
(`update_config` / `normalize_nine_router_config` — the model-string path a
blend rides).

## What a blend is

9router already supports **combos**: a named, ordered list of models stored in
its own SQLite database and exposed as a single *virtual model* — any chat
request whose `model` equals the combo name is resolved to the combo's model
list at request time, with a per-combo **strategy** (`fallback` |
`round-robin` | `fusion`) read from 9router settings. All of this is verified
against the pinned 9router 0.5.40 in [findings.md](findings.md).

XNOBrain uses this machinery today for exactly one hidden, system-managed
combo named `auto`
(`NineRouterManager.ensure_auto_combo()` in
[`xnobrain/integrations/nine_router.py`](../../xnobrain/integrations/nine_router.py)).
User-created combos are invisible: `list_models()` filters them out (they
surface in `/v1/models` with `owned_by: "combo"`, which is not an active
provider alias — findings.md §7).

This plan makes the capability first-class under the product name **Model
Blend** ("Blend" for short; naming rationale in
[approaches.md](approaches.md) Decision A). The API layer says *blend*; every
9router call still says *combos* — upstream ids, routes, and settings keys are
never renamed.

## Goal

Let a local operator create, edit, and use named model blends:

1. **CRUD**: create / rename / edit models / reorder / delete named blends
   through XNOBrain, proxied to 9router `/api/combos*`. The reserved `auto`
   combo stays system-managed and is read-only through this surface.
2. **Strategy**: a per-blend strategy editor — `fallback` (ordered failover),
   `round-robin` (sticky rotation), or `fusion` (fan out to every model and
   combine via a judge model) — written through 9router `/api/settings`
   (`comboStrategies`).
3. **Surfacing**: blends appear in every model picker (composer picker, agent
   settings), and an agent's model can be set to a blend name. This needs
   **zero Hermes changes** — a blend name is just a model string flowing
   through the existing `config.yaml` path (findings.md §8).
4. **UI**: a "Model Blends" management panel in Settings, plus a "Blends"
   group at the top of the model picker. Fusion cost/tooling caveats surfaced
   in copy.
5. **Usage visibility**: the plan-009 analytics by-model table shows blend
   names automatically, because Hermes records the *requested* model string in
   `sessions.model` (findings.md §9). No analytics change needed; verified and
   stated, not built.

**XNOBrain stores nothing.** 9router's own database is the single source of
truth for blends and strategies; every XNOBrain endpoint is a live proxy. No
snapshots are needed because no XNOBrain-owned file is mutated (the snapshot
rule applies to persistence-promising mutations of XNOBrain/Hermes state —
setting an *agent's model* to a blend continues to go through the existing
`update_agent_config` snapshot path untouched).

## Non-goals

- **No per-request routing logic in XNOBrain.** 9router owns resolution,
  failover, rotation, and fusion. XNOBrain never inspects or rewrites chat
  requests for blends.
- **No auto-blend redesign.** `ensure_auto_combo()` keeps its current
  behavior (up to 12 models, one per provider owner). `auto` is shown as a
  system blend, never editable here.
- **No cost optimizer / smart routing.** No latency- or price-based model
  selection; strategy is exactly what 9router implements.
- **No Go, PostgreSQL, ORM, or second API process.** One FastAPI/Hermes
  process on `:8642`, one 9router on `:20128`. Provider stays forced to
  9router.
- **No credentials handling.** Blend APIs never touch provider keys; the
  settings adapter whitelists combo keys only and never returns the raw
  9router settings payload (it contains unrelated instance config —
  findings.md §6).
- **No mock data.** Every blend shown comes from a live 9router read; when
  9router is down the API returns the standard 503 failure envelope
  (approaches.md Decision D).
- **No new persistence.** No XNOBrain-side blend store, cache file, or
  snapshot of 9router state.

## Phase overview

- **Phase 0 — Pin and probe.** The 9router dependency is already pinned
  (`Dockerfile.backend` `ARG NINE_ROUTER_NPM_VERSION=0.5.40`,
  `scripts/install-linux.sh`). Add a live-probe test (skip when 9router is not
  running) that asserts the combos CRUD shapes, the settings GET/PATCH
  behavior for `comboStrategy`/`comboStrategies`/`comboStickyRoundRobinLimit`,
  the combo name charset, and how combos appear in `/v1/models`
  (findings.md §10 lists every probe).
- **Phase 1 — Adapter refactor.** Promote the combo calls buried in
  `_ensure_auto_combo` into public `NineRouterManager` methods
  (`list_combos`, `create_combo`, `update_combo`, `delete_combo`) plus
  strategy accessors over `/api/settings`; extend `list_models()` to append
  blends as `{id: <name>, provider: "blend", name}` entries.
- **Phase 2 — Service + models.** `xnobrain/services/blends.py`
  (`BlendService`: name guards, `auto` read-only, strategy hydration) and the
  `BlendCreate`/`BlendPatch` Pydantic models in
  `xnobrain/models/api.py`.
- **Phase 3 — Handlers + routes.** `blends_*` operations in
  `xnobrain/handlers/api.py` and the `Route(...)` lines in
  `xnobrain/routes/setup.py` (tag `Blends`, versioned `/api/brain/v1`).
- **Phase 4 — Frontend.** `src/api/blends.ts`, `src/hooks/useBlends.ts`,
  a Blends section in `src/features/system/SystemView.tsx`, and the
  "Blends" group in the `src/components/ChatArea.tsx` model picker.
- **Phase 5 — Tests.** Fake-router unit tests (combo + settings fixtures),
  service guard tests (name collision, `auto` immutability), ASGI integration
  tests, frontend build.

File-by-file steps are in [implementation.md](implementation.md).

## Definition of done

- Creating a blend of two real models via `POST /api/brain/v1/blends`
  creates the combo in 9router (visible in `GET /api/combos` and as a model in
  `/v1/models`), and the blend appears in `GET /api/brain/v1/blends` and
  in every XNOBrain model list with `provider: "blend"`.
- Setting an agent's model to the blend name through the existing
  `PATCH /api/brain/v1/agents-configs/{agent_id}` writes
  `model.default: <blend>` into the profile `config.yaml`, and a chat on that
  agent routes through the blend (9router log line
  `Combo "<name>" with N models (strategy: ...)`).
- Switching the blend's strategy to `round-robin` and to `fusion` round-trips
  through `GET /api/brain/v1/blends` and changes the logged strategy.
- `auto` is listed as a system blend and every mutation of it through the
  blends API is rejected (403), while `ensure_auto_combo()` keeps managing it.
- With 9router stopped, every blends endpoint returns the standard failure
  envelope with status 503 and the UI shows its unavailable state — no crash,
  no stale fabricated data.
- Analytics by-model view attributes sessions run under a blend to the blend
  name (verified with a real chat; no analytics code change).
- No route or log exposes credentials or the raw 9router settings payload.
- Probe test, unit/integration tests, `npm run build`, and
  `make check` pass with evidence recorded in
  [validation.md](validation.md).
