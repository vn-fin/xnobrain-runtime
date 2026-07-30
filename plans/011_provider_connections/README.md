# 011 — Multi-account provider connections

Priority: P1 — direct user pain. A user with two Codex subscriptions (or two
OpenAI keys) cannot add the second one today, even though 9router supports it
natively. Depends on nothing; ships independently of plans 005–009.

Read the sibling documents in order:

- [findings.md](findings.md) — 9router's native multi-account model and
  Brain4All's current single-connection collapse.
- [architecture.md](architecture.md) — layering, adapter/service signatures,
  API contract, UI sketch, sequence diagrams.
- [approaches.md](approaches.md) — options considered and the chosen ones.
- [implementation.md](implementation.md) — ordered, file-by-file steps.
- [validation.md](validation.md) — how completion is proven.

Also read before starting: [`AGENTS.md`](../../AGENTS.md),
[`plans/LOCAL_FEATURES_CHECKLIST.md`](../LOCAL_FEATURES_CHECKLIST.md)
(program principles),
[`brain4all/routes/setup.py`](../../brain4all/routes/setup.py) (the only
route-assembly point; current provider routes at lines 126–134),
[`brain4all/integrations/nine_router.py`](../../brain4all/integrations/nine_router.py)
(the adapter this plan extends), and
[`brain4all/services/platform.py`](../../brain4all/services/platform.py)
(`providers()` through `test_provider()`, lines 592–688).

## Goal

Surface 9router's **native multi-account support** ("Multi-account —
Round-robin between accounts per provider", per its README) in Brain4All. The
router already stores any number of connections per provider in its
`providerConnections` SQLite table with `priority` and `isActive` columns;
Brain4All currently collapses that to one connection per provider and hides
the rest. After this plan a local user can:

1. **See every connection (account) per provider** — id, name/email,
   auth type, active flag, priority, last test status, last error.
2. **Add another account to an already-connected provider** — API-key POST
   for `openai`/`anthropic`/`gemini`; a re-run of the existing OAuth flow for
   `claude`/`codex`/`antigravity`.
3. **Manage each connection individually** — activate/deactivate, reorder
   (priority), test, delete one account without touching the others.
4. **See per-connection quota/usage** from 9router's
   `/api/usage/{connectionId}`.
5. Keep using every existing provider-level route unchanged: a provider is
   "connected" when it has **at least one active connection**, and
   provider-level disconnect becomes an explicit "remove all accounts" action.

All account state stays inside 9router's own database. **This feature stores
nothing in Brain4All** — no new files under `DATA_DIR`, so snapshot rules do
not apply — and Brain4All continues to never see, store, return, or log API
keys or OAuth tokens.

## Non-goals

- **No providers beyond the curated allowlist.** The six providers in
  `SUPPORTED_ROUTER_PROVIDERS` (`claude`, `codex`, `antigravity`, `openai`,
  `anthropic`, `gemini`) stay the whole surface. Widening toward 9router's
  40+ providers is Decision A in [approaches.md](approaches.md); the chosen
  answer is "keep the curated six now, add an env-flag follow-up later".
- **No Hermes profile changes.** Profiles keep pointing at the single 9router
  endpoint via `normalize_nine_router_config()`
  ([`brain4all/integrations/nine_router.py`](../../brain4all/integrations/nine_router.py)
  line 58). Multi-account routing lives entirely behind 9router; Hermes never
  learns about individual accounts.
- **No 9router fork or patch.** We call its HTTP API only. Anything its API
  cannot do (verified in Phase 0) is out of scope.
- **No second API process, no Go, no PostgreSQL, no ORM.** One FastAPI/Hermes
  process on `:8642`, one 9router on `:20128`.
- **No load-balancing policy engine in Brain4All.** Rotation/fallback among
  accounts is 9router's job; we only expose its knobs (`isActive`,
  `priority`).
- **No mock data.** Every row rendered comes from a live 9router response;
  when 9router is down the UI shows the same unavailable state it shows
  today.

## Mandatory constraints (restated, all obeyed)

- One FastAPI/Hermes process (`:8642`) + one 9router (`:20128`); no Go,
  PostgreSQL, ORM, or second API process.
- Provider forced to 9router; Hermes profiles keep pointing at 9router
  (unchanged by this plan).
- [`brain4all/routes/setup.py`](../../brain4all/routes/setup.py) is the only
  route-assembly point. Handlers own HTTP, services own rules, integrations
  adapt 9router over HTTP, models are Pydantic.
- **Credentials never enter Brain4All.** Keys live only in 9router; every
  adapter response passes through an explicit field allowlist
  (`_filtered_connection_response` and the `list_connections` field picker).
- **Phase 0 pins and probes.** 9router is a separate npm dependency pinned at
  `0.5.40` (`Dockerfile.backend` `ARG NINE_ROUTER_NPM_VERSION=0.5.40` /
  `ARG NINE_ROUTER_VERSION=v0.5.40`, and
  `scripts/install-linux.sh` `nine_router_version=…:-0.5.40`). Phase 0 probes
  every 9router endpoint/verb this plan uses against a live router before any
  code depends on it — the route manifest lists paths, not methods.

## Phase overview

- **Phase 0 — Pin note + live probe.** Assert the version pins; add a live
  probe test (skips cleanly when 9router is down) that verifies
  `GET/POST/PUT/DELETE /api/providers[...]`, the PUT body shape for
  `isActive`/`priority`, the connection response field names (`priority`,
  `email`), `POST /api/providers/{id}/test`, and
  `GET /api/usage/{connectionId}`.
- **Phase 1 — Adapter.** Extend
  [`brain4all/integrations/nine_router.py`](../../brain4all/integrations/nine_router.py):
  `list_connections` returns `priority` and `email`; new `update_connection`
  (PUT active/priority) and `usage_for_connection`.
- **Phase 2 — Service + models.** Connection-level rules in
  [`brain4all/services/platform.py`](../../brain4all/services/platform.py)
  (ownership guard, "connected = ≥1 active", priority normalization) and the
  `ConnectionCreate`/`ConnectionPatch` Pydantic models.
- **Phase 3 — Handlers + routes.** Six new operations and six new
  `Route(...)` lines under `/api/brain/v1/providers/{provider_id}/connections`,
  tag `Providers`.
- **Phase 4 — Frontend.** Accounts list per provider card in the Connectors
  section: [`src/src/api/providers.ts`](../../src/src/api/providers.ts),
  [`src/src/hooks/useConnections.ts`](../../src/src/hooks/useConnections.ts),
  [`src/src/components/ConnectionsView.tsx`](../../src/src/components/ConnectionsView.tsx)
  (rendered by
  [`src/src/features/system/SystemView.tsx`](../../src/src/features/system/SystemView.tsx)),
  reusing [`src/src/utils/providerAuth.ts`](../../src/src/utils/providerAuth.ts)
  popup flows.
- **Phase 5 — Tests.** Multi-connection `FakeRouter` fixtures in the
  [`brain4all/tests/test_fastapi.py`](../../brain4all/tests/test_fastapi.py)
  pattern plus `FakeNineRouterManager` adapter tests in
  [`brain4all/tests/test_nine_router.py`](../../brain4all/tests/test_nine_router.py);
  a response-scan test asserts no key material ever appears.

## Definition of done

- With two real accounts connected to one provider (e.g. two Codex logins),
  `GET /api/brain/v1/providers/codex/connections` lists both with
  distinct ids, names/emails, priorities, and test statuses.
- Adding a second OpenAI API key through the UI creates a second connection
  and the first one keeps working; the provider card still reads "connected".
- Deactivating one connection (PATCH `active:false`) leaves the provider
  connected while the other account is active; deactivating both flips the
  provider card to disconnected and `list_models()` drops that provider's
  models (existing behavior, now driven by "≥1 active").
- Reordering via PATCH `priority` persists in 9router and is reflected on
  reload; observed routing semantics (round-robin vs fallback order — Phase 0
  finding) are stated honestly in the UI copy.
- Deleting one connection removes only that account; the provider-level
  "Disconnect" removes all accounts only after an explicit confirm.
- `GET .../connections/{connection_id}/usage` returns that account's quota
  windows, and the UI shows a per-row usage bar.
- No response or log ever contains `api_key`, `apiKey`, `token`,
  `accessToken`, `refreshToken`, or any credential material — proven by the
  response-scan test in [validation.md](validation.md).
- Phase 0 probe passes against a live pinned 9router `0.5.40`; unit,
  integration, and `make check` all pass; manual E2E in
  [validation.md](validation.md) completed with evidence.
