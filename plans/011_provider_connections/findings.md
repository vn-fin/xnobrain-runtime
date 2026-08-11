# 011 — Findings

What was verified in this repository and in the installed 9router package,
what the exact gap is, and what must still be probed against a **live**
9router in Phase 0. File references were re-checked against the working tree
on 2026-07-25.

## 1. 9router natively supports multiple connections per provider

9router (npm package `9router`, MIT, github.com/decolua/9router) is installed
locally at `.tools/npm-global/lib/node_modules/9router/`. Its README states:

> "Multi-account — Round-robin between accounts per provider"
> "Auto fallback — Subscription → Cheap → Free, zero downtime"

and advertises 40+ providers / 100+ models.

Its SQLite schema stores **one row per account**, not per provider. The
migration code at
`.tools/npm-global/lib/node_modules/9router/app/src/lib/db/migrate.js`
(lines 120–123) inserts into:

```
providerConnections(id TEXT PK, provider, authType, name, email,
                    priority INTEGER, isActive INTEGER DEFAULT 1,
                    data, createdAt, updatedAt)
```

with index `idx_pc_priority ON (provider, priority)` — i.e. the composite
index exists precisely to order accounts *within* a provider. The runtime
service `.tools/npm-global/lib/node_modules/9router/app/src/shared/services/quotaAutoPing.js`
(line 274) queries `getProviderConnections({ provider, isActive: true })` and
calls `updateProviderConnection(connection.id, {...})` (line 240), confirming
per-connection reads and updates are first-class in its data layer.

The `data` column is where credential material (API key / OAuth tokens)
lives. **Brain4All must never read or forward that column's contents.**

## 2. 9router HTTP API surface (route manifest — paths verified, verbs NOT)

The compiled route manifest at
`.tools/npm-global/lib/node_modules/9router/app/.next-cli-build/server/app-paths-manifest.json`
lists (relevant subset):

- `GET/POST /api/providers` — list / create connections
- `GET/PUT/DELETE /api/providers/[id]` — read / update / delete one connection
- `POST /api/providers/[id]/test` — validate one connection
- `GET /api/providers/[id]/models` — models for one connection
- `/api/providers/test-batch`, `/api/providers/validate`
- `/api/oauth/[provider]/[action]` — OAuth flows (already adapted by
  `NineRouterManager.oauth()`)
- `/api/usage/[connectionId]` — per-connection quota windows (already used by
  `NineRouterManager.usage()`)

**Caveat:** a Next.js app-paths manifest lists *paths*, not HTTP methods. The
verbs Brain4All already exercises are proven in production use
(`GET/POST /api/providers`, `DELETE /api/providers/{id}`,
`POST /api/providers/{id}/test`, `GET /api/usage/{connectionId}`, the OAuth
GET/POST split in `_OAUTH_GET_ACTIONS`/`_OAUTH_POST_ACTIONS`). The **PUT on
`/api/providers/{id}` for `isActive`/`priority` is expected but unproven** —
its existence, accepted body shape, and response shape are Phase 0 probes
(section 6). The provider routes' source only exists compiled/minified under
`app/.next-cli-build/`, so static verification is not practical; probe live.

Authentication: every call goes through `NineRouterManager._request()`
([`brain4all/integrations/nine_router.py`](../../brain4all/integrations/nine_router.py)
lines 384–421) which sends the derived `x-9r-cli-token` header (`_cli_token()`,
lines 524–534). New endpoints reuse `_request()` and inherit this.

## 3. Brain4All's current single-connection collapse (the gap)

### 3a. `services/platform.py` — one connection per provider, delete-all disconnect

[`brain4all/services/platform.py`](../../brain4all/services/platform.py):

- `SUPPORTED_PROVIDERS` (line 31) and `API_KEY_PROVIDERS` (line 32) mirror the
  adapter's sets.
- `providers()` (lines 592–611) collapses to **one** connection per provider:

  ```python
  connection = next((item for item in connections
                     if item.get("provider") == provider
                     and item.get("active", True)), None)
  ```

  Every additional account is invisible; `last_test_status` and
  `default_model` come from whichever connection happens to be first.
- `disconnect_provider()` (lines 662–669) deletes **ALL** connections for the
  provider in a loop — there is no way to remove just one account.
- `test_provider()` (lines 671–678) tests only the first matching connection
  (`next(...)` at line 674), even an inactive one.
- `start_provider_connect()` / `submit_provider_connect()` (lines 619–653)
  already work regardless of how many connections exist — the "can't add a
  second account" restriction is purely that the UI hides the connect action
  once `connected` is true (section 3d). `_oauth_attempts` is keyed by
  provider, so one in-flight OAuth attempt per provider — acceptable and
  unchanged.
- `update_provider()` (lines 655–660) for API-key providers already calls
  `create_api_key_connection`, i.e. **saving a key today silently creates an
  additional 9router connection** rather than replacing one. Today's UI then
  shows only one of them. This plan makes that existing behavior visible and
  intentional ("Add account").

### 3b. `integrations/nine_router.py` — allowlist and dropped fields

[`brain4all/integrations/nine_router.py`](../../brain4all/integrations/nine_router.py):

- `SUPPORTED_ROUTER_PROVIDERS = {claude, codex, antigravity, openai,
  anthropic, gemini}` (lines 25–27) — 6 of 9router's 40+.
  `OAUTH_ROUTER_PROVIDERS` (line 28) and `API_KEY_ROUTER_PROVIDERS` (line 29)
  split them.
- `list_connections()` (lines 126–148) filters to that set and builds each
  row from an explicit field picker — **it currently drops `priority` and
  `email`** (both present in the schema, section 1). Field name in the 9router
  response is expected to be `priority` (matching the column) but that exact
  name is a Phase 0 probe; camelCase (`isActive`, `authType`, `testStatus`,
  `lastError`, `defaultModel`) is the router's convention for the others.
- `create_api_key_connection()` (lines 150–167) POSTs `/api/providers` and
  returns via `_filtered_connection_response()` (lines 423–437), which
  allowlists output fields so the key never round-trips. Keep this exact
  pattern for everything new.
- `delete_connection()` (lines 169–178) DELETEs one connection and re-ensures
  the `auto` combo. `test_connection()` (lines 180–190) POSTs `/test`.
- `usage(model)` (lines 274–325) maps model → provider → **first active
  connection** → `GET /api/usage/{connection_id}`. The per-connection HTTP
  call already exists; only a `usage_for_connection(connection_id)` entry
  point is missing.
- `ROUTER_MODEL_ALIASES` (lines 30–37) hardcodes the model-owner mapping per
  provider — one reason the allowlist stays curated (approaches.md
  Decision A).
- `_safe_id()` (lines 515–518) validates ids; reuse for `connection_id` path
  params. `_request()` refuses paths outside `/api/`+`/v1/` (line 390).

### 3c. Routes, handlers, models — provider-level only

- [`brain4all/routes/setup.py`](../../brain4all/routes/setup.py) lines
  126–134: the whole `Providers` tag is provider-granular (`/connect`,
  `/update`, `/disconnect`, `/test`, `/models`). No connection-granular route
  exists.
- [`brain4all/handlers/api.py`](../../brain4all/handlers/api.py) lines
  170–178: the matching operations in the `_operation` dispatch table.
- [`brain4all/models/api.py`](../../brain4all/models/api.py) line 183:
  `ProviderCredential` is the only provider body model. No
  create/patch model for a single connection exists.

### 3d. Frontend — one card, one state

- [`src/components/ConnectionsView.tsx`](../../src/components/ConnectionsView.tsx)
  renders one card per provider; once `p.connected` is true it shows only
  Test/Disconnect (lines 71–102) — the connect action disappears, so a second
  account can never be added. The shared API-key panel (lines 129–155) saves
  via `onSaveKey` → `providersApi.update` which, per 3a, already *adds* a
  connection under the hood.
- [`src/features/system/SystemView.tsx`](../../src/features/system/SystemView.tsx)
  renders `ConnectionsView` inside the `connectors` section (lines 152–161).
- [`src/hooks/useConnections.ts`](../../src/hooks/useConnections.ts)
  holds provider-level state only (`ConnectionProvider[]`) and the OAuth
  popup/poll flow (lines 44–107) via
  [`src/utils/providerAuth.ts`](../../src/utils/providerAuth.ts).
- [`src/api/providers.ts`](../../src/api/providers.ts) has no
  connection-level methods; [`src/types.ts`](../../src/types.ts)
  `ConnectionProvider` (line 11) has no accounts array.

## 4. Hermes profile side is unaffected

`normalize_nine_router_config()`
([`brain4all/integrations/nine_router.py`](../../brain4all/integrations/nine_router.py)
lines 58–93) forces every profile at the single 9router endpoint
(`http://127.0.0.1:20128/v1`). Which account serves a request is decided
inside 9router. Nothing in this plan touches profile `config.yaml`, so
**no Brain4All-owned persistent file changes → snapshot rules do not apply.
This feature stores nothing in Brain4All**; all state lives in 9router's DB.

## 5. Version pins (Phase 0 restates and asserts these)

- `Dockerfile.backend` line 4: `ARG NINE_ROUTER_VERSION=v0.5.40` (git build
  stage) and line 18: `ARG NINE_ROUTER_NPM_VERSION=0.5.40` (npm install).
- `scripts/install-linux.sh` line 24:
  `nine_router_version="${BRAIN4ALL_NINE_ROUTER_VERSION:-0.5.40}"`.

All three must agree; the plan does not bump them.

## 6. What Phase 0 MUST probe against a LIVE 9router

> **PROBED 2026-07-25 against 9router v0.5.40 (live). Results inline below.**
> - **Item 1 — RESOLVED.** `PUT /api/providers/{id}` accepts partial bodies
>   `{"isActive": false}` and `{"priority": N}` (200); response shape is
>   `{"connection": {...full row...}}`. The load-bearing assumption holds.
> - **Item 2 — RESOLVED.** `GET /api/providers` row keys observed:
>   `id, provider, authType, name, email, priority, isActive, testStatus,
>   lastError, lastErrorAt, lastRefreshAt, expiresAt, expiresIn, createdAt,
>   updatedAt, providerSpecificData`. Note: **no `defaultModel`** (adapter
>   tolerates absence → `""`); the credential-bearing field is
>   **`providerSpecificData`** (the schema's `data` is surfaced under this
>   name) — the response-scan test must forbid `providerSpecificData`.
> - **Item 3 — PARTIALLY RESOLVED / important.** 9router **re-normalizes
>   `priority` itself**: sending `priority: 5` stored `priority: 1`. Priority
>   is therefore a rank 9router compacts, not a value we control — the service
>   **must read-after-write** and the UI must reflect the router's value, not
>   the requested one (approaches.md Decision B: reorder sends desired order,
>   then re-reads). Rotation-vs-priority runtime behavior across two real
>   accounts remains a manual observation (kept in item 7 of the probe test).
> - **Item 4 — RESOLVED.** `POST /api/providers` accepts a syntactically valid
>   fake key (`sk-plan011-probe`) and creates a row; validation happens at
>   `/test` (dummy key → `{"valid": false, "error": ..., "refreshed": false}`).
> - **Item 5 — RESOLVED.** `GET /api/usage/{id}` for an api-key connection
>   returned `{"message": ...}` with **no `quotas`** → `available: false`,
>   `quotas: []`. OAuth-connection quota shape unchanged from `usage()`.
> - `test`/`delete` verbs confirmed working; throwaway row cleaned up.
>
> The original probe list is retained below for the compatibility test to
> re-run on any 9router bump.

1. **`PUT /api/providers/{id}` exists and its body shape.** Expected
   `{"isActive": false}` and `{"priority": 2}` as partial updates (matching
   `updateProviderConnection(id, {...})` in the router's data layer). Verify:
   status code, whether partial bodies are accepted, whether unknown fields
   are rejected, and the response shape (full connection object vs
   `{success}`).
2. **Connection response field names.** `GET /api/providers` items: confirm
   `priority` (expected, matches the column) and `email`; confirm
   `isActive`/`authType`/`testStatus`/`lastError`/`defaultModel` casing that
   `list_connections()` already assumes.
3. **Round-robin vs priority semantics.** The README says "round-robin
   between accounts per provider"; `idx_pc_priority` implies an ordering.
   Working hypothesis to verify: rotation happens among active accounts, and
   `priority` orders fallback/selection preference (lower = preferred).
   Determine what changing `priority` observably does (e.g. via
   request-routing logs or `/api/usage` attribution) and whether equal
   priorities round-robin. **The UI copy in Phase 4 must match the observed
   behavior** (approaches.md Decision C).
4. **Create-with-dummy-key behavior.** Whether `POST /api/providers` accepts
   a syntactically valid but fake API key (expected: yes; validation happens
   at `/test`). The probe needs this to create/patch/delete a throwaway
   connection without real credentials.
5. **`GET /api/usage/{connectionId}` shape per auth type.** Already consumed
   for OAuth providers by `usage()`; confirm the `{plan, quotas: {name:
   {used,total,remaining,remainingPercentage,resetAt,unlimited}}}` shape and
   what an API-key connection returns (possibly empty quotas / a `message`).
6. **Effect of deactivating the last active connection.** Confirm
   `/v1/models` drops the provider's models (Brain4All's `list_models()`
   already assumes active-connection gating) and that the `auto` combo
   re-ensure logic (`_ensure_auto_combo`) still behaves.
7. **OAuth exchange response for an added second account.** Confirm
   `POST /api/oauth/{provider}/exchange` succeeds when the provider already
   has a connection and creates a **new** row (not overwriting), and whether
   the response identifies the new connection id.

## 7. Risks

- **Upstream 0.x drift.** 9router is a 0.x dependency; any of the shapes in
  section 6 may change between versions. Mitigation: the version is pinned at
  `0.5.40` in three places (section 5), and the Phase 0 probe doubles as the
  compatibility test to re-run on any bump.
- **PUT may not exist / may require the full object.** If the probe finds no
  partial-update PUT, fall back to read-modify-write of the allowlisted
  fields only (never echoing `data`/key material), or drop the priority
  feature to active-toggle-only — decide in Phase 0, record the result in
  this file.
- **Credential leak surface grows.** Six new response paths. Mitigation:
  every adapter method returns through an explicit field allowlist, and the
  response-scan test in [validation.md](validation.md) walks every new
  route's JSON for forbidden substrings.
- **Deleting/deactivating the account currently serving Hermes traffic.**
  9router owns failover; Brain4All only re-ensures the `auto` combo (existing
  `delete_connection` behavior, extended to deactivation). The UI warns when
  the action would leave zero active accounts.
- **Priority semantics mismatch.** If observed behavior is pure round-robin
  and priority does nothing user-visible, the reorder UI would mislead.
  Phase 0 gates this: ship reorder only with honest copy or hide it (see
  approaches.md Decisions B/C).

## 8. Open questions

- Does 9router expose `email` for OAuth accounts of all three OAuth
  providers, or only some? (Affects row labeling; fall back to `name`.)
- Does `POST /api/providers/{id}/test` update `testStatus`/`lastError` on the
  stored row (so a subsequent list reflects it), or is the result transient?
- Is there a per-connection `defaultModel` semantics difference when several
  accounts of one provider disagree? (Out of scope to change; just confirm
  list rendering is sane.)
- `/api/providers/test-batch` and `/api/providers/validate` — useful later
  for a "test all accounts" button; not required by this plan.
