# 011 — Implementation

Ordered, file-by-file steps. Each phase is independently commit-able and
keeps `make check` green. Design rationale in
[architecture.md](architecture.md); verified facts and probe list in
[findings.md](findings.md).

## Phase 0 — Pin note + live 9router probe

Nothing later in this plan may be built on an unprobed 9router behavior.

### 0a. Assert the pins (no version bump)

Add to the new probe test (0b) a static assertion that the three pins agree,
so a future bump cannot silently desync them:

- `Dockerfile.backend` line 4 `ARG NINE_ROUTER_VERSION=v0.5.40` and line 18
  `ARG NINE_ROUTER_NPM_VERSION=0.5.40`
- `scripts/install-linux.sh` line 24
  `nine_router_version="${BRAIN4ALL_NINE_ROUTER_VERSION:-0.5.40}"`

Implementation: read both files with a regex in the test module and assert
all extracted versions normalize to the same `0.5.40`.

### 0b. Live probe test — `brain4all/tests/test_nine_router_probe.py` (new)

Pattern: `unittest.IsolatedAsyncioTestCase` that **skips cleanly when 9router
is down** (the rest of the suite fakes the router; this one is the only test
allowed to talk to a real one):

```python
"""Phase 0 probe for plan 011: verify the live 9router endpoints and shapes
this plan depends on. Skips when no 9router answers on :20128."""

import os, unittest, aiohttp
from brain4all.integrations.nine_router import NineRouterManager

BASE = os.environ.get("NINE_ROUTER_URL", "http://127.0.0.1:20128")

async def _router_up() -> bool:
    try:
        timeout = aiohttp.ClientTimeout(total=2)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(BASE + "/api/providers") as response:
                return response.status in (200, 401)
    except Exception:
        return False

class NineRouterLiveProbeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if not await _router_up():
            self.skipTest("9router is not running on " + BASE)
        self.manager = NineRouterManager(base_url=BASE)
```

Probes (one test method each, matching findings.md §6; use
`self.manager._request(...)` so the real auth header is exercised):

1. `test_pins_agree` — 0a assertion (this one never skips).
2. `test_list_connections_shape` — `GET /api/providers` returns
   `{"connections": [...]}`; for every item assert the keys
   `id/provider/authType/isActive` exist and record (via
   `self.assertIn`/soft log) whether `priority`, `email`, `name`,
   `testStatus`, `lastError`, `defaultModel` are present. **This pins the
   field names Phase 1 consumes.**
3. `test_connection_lifecycle_put_isactive_and_priority` — create a
   throwaway connection with a dummy key
   (`POST /api/providers {"provider": "openai", "apiKey":
   "sk-plan011-probe", "name": "plan011-probe"}`); then
   `PUT /api/providers/{id} {"isActive": false}` → re-list → assert
   inactive; `PUT {"priority": 5}` → re-list → assert priority is 5;
   finally `DELETE /api/providers/{id}` (also in `addCleanup` so failures
   don't leak the row). **This is the load-bearing probe: it proves the PUT
   verb, partial-body acceptance, and both field names.** If it fails,
   STOP — apply the contingency in findings.md §7 (read-modify-write or
   drop priority) and update findings/approaches before Phase 1.
4. `test_test_endpoint_shape` — `POST /api/providers/{id}/test` on the
   throwaway connection returns `{"valid": false, ...}` (dummy key must not
   validate) with an `error` string.
5. `test_usage_for_connection_shape` — `GET /api/usage/{id}` on the
   throwaway connection returns a mapping; assert `quotas` (possibly empty)
   and note the shape for api-key vs OAuth connections.
6. `test_oauth_routes_exist` — `GET /api/oauth/codex/authorize?...` returns
   an `authUrl` (no completion; documents that re-running authorize while
   already connected is accepted).
7. `test_rotation_semantics_documented` — not automatable without two real
   accounts; this test asserts a documented conclusion exists: it reads
   `plans/011_provider_connections/findings.md` and fails if the §6 item 3
   round-robin/priority question is still marked unresolved once
   `BRAIN4ALL_PLAN011_PHASE0_DONE=1` is set in the environment. (Keeps the
   manual observation step — two real accounts, watch attribution in
   `/api/usage` — from being skipped silently.)

Run: `python -m unittest brain4all.tests.test_nine_router_probe -v` with a
local `9router` started (e.g. `.tools/npm-global/bin/9router`). Record the
observed shapes as a comment block at the top of the test file and update
findings.md §6 with the answers.

## Phase 1 — Adapter methods (`brain4all/integrations/nine_router.py`)

### 1a. Extend `list_connections()` (lines 126–148)

Inside the row builder append:

```python
"email": str(item.get("email") or ""),
"priority": self._number(item.get("priority")),
```

(`_number` already exists, line 509; absent/null priority → 0.) Field names
per the Phase 0 probe — adjust if probe 2 found different casing.

### 1b. Extend `_filtered_connection_response()` (lines 423–437)

Add the same two allowlisted fields to the returned `connection` dict:

```python
"email": str(connection.get("email") or ""),
"priority": self._number(connection.get("priority")),
```

### 1c. New `update_connection()` (place after `delete_connection`, ~line 178)

```python
async def update_connection(
    self,
    connection_id: Any,
    *,
    active: bool | None = None,
    priority: int | None = None,
) -> dict[str, Any]:
    """PUT one connection's isActive/priority. Never touches credentials."""

    connection_id = self._safe_id(connection_id, "connection_id")
    request_body: dict[str, Any] = {}
    if active is not None:
        request_body["isActive"] = bool(active)
    if priority is not None:
        request_body["priority"] = int(priority)
    if not request_body:
        raise NineRouterAPIError(
            "active or priority is required",
            code="invalid_provider_connection", status=400,
        )
    payload = await self._request(
        "PUT", f"/api/providers/{quote(connection_id, safe='')}", request_body
    )
    models = (await self.list_models(ensure_auto=False))["data"]
    await self._ensure_auto_combo(models)
    if isinstance(payload, Mapping) and isinstance(payload.get("connection"), Mapping):
        return self._filtered_connection_response(payload)
    return {"object": "nine_router.provider_update", "id": connection_id, "updated": True}
```

The `_ensure_auto_combo` re-run mirrors `delete_connection()` — deactivating
a provider's last account must shrink the `auto` combo the same way deletion
does. Adjust the PUT body keys if probe 3 found otherwise.

### 1d. New `usage_for_connection()` (place after `usage()`, ~line 326)

Extract the quota loop shared with `usage()` first:

```python
def _quota_list(self, payload: Any, *, provider: str = "", model_id: str = "") -> list[dict[str, Any]]:
    raw_quotas = payload.get("quotas", {}) if isinstance(payload, Mapping) else {}
    quotas: list[dict[str, Any]] = []
    if isinstance(raw_quotas, Mapping):
        for name, raw_quota in raw_quotas.items():
            if not isinstance(raw_quota, Mapping):
                continue
            quota_name = str(name or "").strip()
            if model_id and not self._quota_matches_model(provider, model_id, quota_name):
                continue
            quotas.append(self._normalize_quota(quota_name, raw_quota))
    return quotas
```

`usage()` calls `self._quota_list(payload, provider=provider,
model_id=model_id)` (behavior identical — existing tests in
`brain4all/tests/test_nine_router.py` line 149 must still pass). Then:

```python
async def usage_for_connection(self, connection_id: Any) -> dict[str, Any]:
    """Account-scoped quota windows, unfiltered by model."""

    connection_id = self._safe_id(connection_id, "connection_id")
    payload = await self._request(
        "GET", f"/api/usage/{quote(connection_id, safe='')}"
    )
    quotas = self._quota_list(payload)
    is_mapping = isinstance(payload, Mapping)
    return {
        "object": "router.connection_usage",
        "connection_id": connection_id,
        "available": bool(quotas),
        "plan": str(payload.get("plan") or "") if is_mapping else "",
        "message": str(payload.get("message") or "") if is_mapping else "",
        "quotas": quotas[:12],
    }
```

Note both methods build responses field-by-field — nothing from the router
payload is forwarded wholesale.

## Phase 2 — Service rules + Pydantic models

### 2a. `brain4all/models/api.py` (append near `ProviderCredential`, line 183)

Add `ConnectionCreate` and `ConnectionPatch` exactly as specified in
[architecture.md](architecture.md) § API contract. Export them wherever the
models package re-exports (`brain4all/models/__init__.py` — mirror how
`ProviderCredential` is exported; `routes/setup.py` imports from
`..models`).

### 2b. `brain4all/services/platform.py` (append after `test_provider()`, ~line 678)

Implement the six methods plus the guard from
[architecture.md](architecture.md) § Service rules:

```python
async def list_provider_connections(self, provider: str) -> dict[str, Any]:
    self._provider(provider)
    connections = await self._provider_connections(provider)
    return {
        "provider_id": provider,
        "connected": any(item.get("active") is not False for item in connections),
        "connections": connections,
    }

async def add_provider_connection(self, provider: str, body: Mapping[str, Any]) -> dict[str, Any]:
    self._provider(provider)
    if provider not in API_KEY_PROVIDERS:
        raise ServiceError(
            "use the provider connect flow to add an OAuth account",
            status=400, code="oauth_connect_required",
        )
    result = await self.router.create_api_key_connection({
        "provider": provider,
        "api_key": body.get("api_key"),
        "name": body.get("name"),
        "default_model": body.get("default_model"),
    })
    return {"provider_id": provider, "connected": True, "connection": result["connection"]}

async def patch_provider_connection(self, provider: str, connection_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
    await self._owned_connection(provider, connection_id)
    active = body.get("active")
    priority = body.get("priority")
    if active is None and priority is None:
        raise ServiceError("active or priority is required", code="invalid_request")
    if priority is not None:
        priority = max(0, min(999, int(priority)))
    await self.router.update_connection(connection_id, active=active, priority=priority)
    refreshed = await self._owned_connection(provider, connection_id)
    return {"provider_id": provider, "connection": refreshed}

async def test_provider_connection(self, provider: str, connection_id: str) -> dict[str, Any]:
    await self._owned_connection(provider, connection_id)
    result = await self.router.test_connection(connection_id)
    healthy = bool(result.get("valid"))
    return {
        "provider_id": provider, "connection_id": connection_id,
        "healthy": healthy, "status": "healthy" if healthy else "unhealthy",
        "message": result.get("error") or "",
    }

async def delete_provider_connection(self, provider: str, connection_id: str) -> dict[str, Any]:
    await self._owned_connection(provider, connection_id)
    await self.router.delete_connection(connection_id)
    remaining = await self._provider_connections(provider)
    return {
        "provider_id": provider, "connection_id": connection_id, "deleted": True,
        "connected": any(item.get("active") is not False for item in remaining),
    }

async def connection_usage(self, provider: str, connection_id: str) -> dict[str, Any]:
    await self._owned_connection(provider, connection_id)
    payload = await self.router.usage_for_connection(connection_id)
    return {"provider_id": provider, **payload}

async def _provider_connections(self, provider: str) -> list[dict[str, Any]]:
    connections = (await self.router.list_connections())["connections"]
    rows = [item for item in connections if item.get("provider") == provider]
    rows.sort(key=lambda item: (item.get("priority", 0), str(item.get("name") or "")))
    return rows

async def _owned_connection(self, provider: str, connection_id: str) -> dict[str, Any]:
    self._provider(provider)
    rows = await self._provider_connections(provider)
    current = next((item for item in rows if item.get("id") == connection_id), None)
    if current is None:
        raise ServiceError("connection not found", status=404, code="not_found")
    return current
```

### 2c. Adjust the three provider-level methods (backward compatible)

- `providers()` (lines 592–611): replace the single-connection `next(...)`
  with `provider_rows = sorted([...], key=...)` /
  `active_rows = [r for r in provider_rows if r.get("active") is not False]`;
  set `connected=bool(active_rows)`, take `last_test_status`/`default_model`
  from `active_rows[0]` when present, and add
  `"connection_count": len(provider_rows)`.
- `test_provider()` (line 674): pick the first **active** row (fall back to
  the first row when none active — preserves today's "unhealthy, not
  not_connected" behavior for inactive-only providers).
- `disconnect_provider()`: unchanged (Decision D).

## Phase 3 — Handlers + routes

### 3a. `brain4all/handlers/api.py` — extend `_operation` (after line 178)

```python
"provider_connections_list": (lambda: s.list_provider_connections(p["provider_id"]), "provider connections retrieved successfully", 200),
"provider_connection_create": (lambda: s.add_provider_connection(p["provider_id"], body), "provider connection created", 201),
"provider_connection_patch": (lambda: s.patch_provider_connection(p["provider_id"], p["connection_id"], body), "provider connection updated", 200),
"provider_connection_test": (lambda: s.test_provider_connection(p["provider_id"], p["connection_id"]), "provider connection tested", 200),
"provider_connection_delete": (lambda: s.delete_provider_connection(p["provider_id"], p["connection_id"]), "provider connection deleted", 200),
"provider_connection_usage": (lambda: s.connection_usage(p["provider_id"], p["connection_id"]), "provider connection usage retrieved", 200),
```

No other handler logic — HTTP translation only.

### 3b. `brain4all/routes/setup.py` — exact `Route(...)` lines (after line 134)

Import `ConnectionCreate, ConnectionPatch` in the existing `..models` import
block (line 14), then append inside the Providers group:

```python
Route("GET", "/api/brain/v1/providers/{provider_id}/connections", "provider_connections_list", tags=("Providers",)),
Route("POST", "/api/brain/v1/providers/{provider_id}/connections", "provider_connection_create", ConnectionCreate, tags=("Providers",)),
Route("PATCH", "/api/brain/v1/providers/{provider_id}/connections/{connection_id}", "provider_connection_patch", ConnectionPatch, tags=("Providers",)),
Route("POST", "/api/brain/v1/providers/{provider_id}/connections/{connection_id}/test", "provider_connection_test", tags=("Providers",)),
Route("DELETE", "/api/brain/v1/providers/{provider_id}/connections/{connection_id}", "provider_connection_delete", tags=("Providers",)),
Route("GET", "/api/brain/v1/providers/{provider_id}/connections/{connection_id}/usage", "provider_connection_usage", tags=("Providers",)),
```

## Phase 4 — Frontend (file by file)

### 4a. `src/api/providers.ts` — types + methods

```ts
export type ProviderConnection = {
  id: string;
  provider: string;
  auth_type: string;
  name: string;
  email: string;
  active: boolean;
  priority: number;
  default_model: string;
  test_status: string;
  last_error: string;
};

export type ConnectionQuota = {
  name: string; used: number; total: number;
  remaining_percent: number; reset_at: string; unlimited: boolean;
};
export type ConnectionUsage = {
  connection_id: string; available: boolean; plan: string;
  message: string; quotas: ConnectionQuota[];
};
```

Methods on `providersApi` (all under the existing `ROOT`, all fields typed —
never render anything not in these types):

```ts
listConnections(id): GET  `${ROOT}/${encoded(id)}/connections`      → { connections: ProviderConnection[] }
addConnection(id, { api_key, name?, default_model? }): POST …/connections → { connection: ProviderConnection }
patchConnection(id, connectionId, { active?, priority? }): PATCH …/connections/${encoded(connectionId)}
testConnection(id, connectionId): POST …/connections/${encoded(connectionId)}/test → { healthy, status, message }
deleteConnection(id, connectionId): DELETE …/connections/${encoded(connectionId)}
connectionUsage(id, connectionId): GET …/connections/${encoded(connectionId)}/usage → ConnectionUsage
```

Add the matching DTO fields in
`src/api/contracts/agentGateway.ts` (`connection_count?` on
`ProviderConnectorDTO`) and surface `connection_count` through
`mapConnectionProvider` in `src/api/mappers/providers.ts` /
`ConnectionProvider` in `src/types.ts` (optional field).

### 4b. `src/hooks/useConnections.ts` — state + actions

Add:

- `connectionsByProvider: Record<string, ProviderConnection[]>` and
  `usageByConnection: Record<string, ConnectionUsage>` state;
  `rowPendingId: string | null` for per-row spinners.
- `loadConnections(providerId)` — fetch + store; called when a card expands
  and after every mutation below.
- `addAccount(providerId, input)` — api-key path
  (`providersApi.addConnection` then `loadConnections` + `refresh()`); for
  OAuth providers delegate to the existing `connect(providerId)`.
- `setAccountActive(providerId, connectionId, active)`,
  `reorderAccount(providerId, connectionId, direction)` (computes normalized
  0..n-1 priorities per approaches.md B2 and PATCHes changed rows),
  `testAccount(...)`, `removeAccount(...)` (then `refresh()` so the provider
  badge updates), `loadAccountUsage(...)` (lazy, per row).
- OAuth "add another account" completion: the existing poll loop (lines
  44–83) exits on `provider.connected`, which is already true. Extend the
  loop to also snapshot `connectionsByProvider[authProviderId]?.length` at
  start and finish when the count grows (call `loadConnections` inside the
  poll). Keep the popup helpers from
  `src/utils/providerAuth.ts` untouched.
- Allow `connect(id)` to run when `provider.connected === true` (remove no
  code — the guard is only in the view, 4c).

### 4c. `src/components/ConnectionsView.tsx` — accounts UI

Per the sketch in [architecture.md](architecture.md):

- Card head badge: `connected · N accounts` using
  `p.connection_count` (fallback: hide count when undefined).
- Expandable "Accounts" section per card (collapsed by default; expanding
  triggers `onLoadConnections(p.id)`). Row = `email || name`, auth-type tag,
  Active toggle, ↑/↓ buttons, Test button + status dot
  (`test_status`: `valid|healthy` → green, `unknown` → gray, else red with
  `last_error` as `title`), usage mini-bar (min `remaining_percent` across
  quotas; em-dash when `available === false`), remove ✕ with
  `window.confirm`.
- "Add account" button: api-key providers → inline `type="password"` input
  (cleared after submit, mirroring lines 143–150); OAuth providers →
  `onConnect(p.id)` (works while connected now).
- Provider-level disconnect renamed to "Remove all accounts…" with a confirm
  that includes the account count (Decision D).
- Rotation caption under the list: i18n key `connections.accountsHint`, text
  fixed by the Phase 0 finding (Decision C).
- New props threaded from `SystemView` → `ConnectionsView`:
  `connectionsByProvider`, `usageByConnection`, `rowPendingId`,
  `onLoadConnections`, `onAddAccount`, `onSetAccountActive`,
  `onReorderAccount`, `onTestAccount`, `onRemoveAccount`,
  `onLoadAccountUsage`.

### 4d. `src/features/system/SystemView.tsx`

Extend `SystemViewProps` (lines 10–34) with the new callbacks/state and pass
them through to `ConnectionsView` (lines 152–161). The composition root that
instantiates `useConnections` (follow current wiring of
`providers`/`onConnect`/`onDisconnect`) supplies them.

### 4e. i18n — `src/locales/*.json`

Add under `connections.`: `accounts`, `accountsHint`, `addAccount`,
`removeAccount`, `removeAllAccounts`, `removeAllConfirm` (with `{{count}}`),
`accountActive`, `moveUp`, `moveDown`, `usageUnavailable`. All seven locale
files (`en`, `de`, `es`, `fr`, `ja`, `vi`, `zh`).

### 4f. Styles

Extend the existing `conn-*` styles (same stylesheet that defines
`conn-card`, `conn-btn`) with `conn-accounts`, `conn-account-row`,
`conn-usage-bar` — no new dependency, no chart library (the usage bar is a
plain div fill).

## Phase 5 — Tests

### 5a. Adapter tests — `brain4all/tests/test_nine_router.py`

Extend `FakeNineRouterManager` (lines 20–33) fixtures with multi-connection
responses; new tests:

- `test_list_connections_returns_priority_and_email_without_credentials` —
  two codex rows with `priority` 1/0 and `apiKey`/`data` planted in the fake
  response; assert both new fields surface and `apiKey`/`data` never do
  (extends the existing assertion at lines 132–135).
- `test_update_connection_puts_partial_body_and_reensures_auto` — assert the
  recorded request is `("PUT", "/api/providers/codex-2", {"isActive": False})`
  and that `/api/combos` maintenance ran.
- `test_update_connection_requires_a_field` — both kwargs None → 400.
- `test_usage_for_connection_returns_all_quota_windows` — unlike
  `usage(model)`, no model filtering: the `review_session` quota from the
  existing fixture (line 166) IS included.

### 5b. Route/service integration — `brain4all/tests/test_fastapi.py`

Extend `FakeRouter` (lines 22–24) into a stateful multi-connection fake:

```python
class FakeRouter:
    def __init__(self):
        self.connections = [
            {"id": "codex-1", "provider": "codex", "auth_type": "oauth",
             "name": "work", "email": "work@example.com", "active": True,
             "priority": 0, "default_model": "", "test_status": "valid",
             "last_error": "", "api_key_should_never_leak": "sk-secret"},
            {"id": "codex-2", "provider": "codex", "auth_type": "oauth",
             "name": "personal", "email": "personal@example.com",
             "active": False, "priority": 1, ...},
        ]
    async def list_connections(self): return {"connections": [dict(c) for c in self.connections]}
    async def list_models(self): return {"data": []}
    async def update_connection(self, cid, *, active=None, priority=None): ...
    async def usage_for_connection(self, cid): ...
    async def test_connection(self, cid): return {"valid": True, "error": ""}
    async def delete_connection(self, cid): ...
    async def create_api_key_connection(self, body): ...  # never store the key
```

(The planted `api_key_should_never_leak` value powers the response-scan test;
the real adapter would have filtered it, so the service layer must too — the
service copies rows as returned by the adapter, hence the fake mimics the
adapter contract WITHOUT the planted key in normal fields, plus one test that
plants it to prove the service doesn't widen the shape. Keep both variants.)

New tests (ASGI client, envelope assertions like the existing ones):

- list: `GET /api/brain/v1/providers/codex/connections` → 200, two rows
  sorted by priority, `connected` true.
- ownership guard: `GET /api/brain/v1/providers/openai/connections/…`
  with `codex-1` in PATCH/test/delete/usage paths → 404.
- create: `POST …/openai/connections {"api_key": "sk-x"}` → 201 and the
  response JSON does not contain `sk-x`; `POST …/codex/connections` → 400
  `oauth_connect_required`.
- patch: `PATCH …/codex/connections/codex-2 {"active": true}` → row active;
  `{"priority": 0}` reorders; `{}` → 400.
- delete one: only `codex-2` removed; `connected` still true; provider-level
  `POST …/codex/disconnect` still deletes all (backward-compat check).
- providers list backward-compat: `GET /api/brain/v1/providers` — shape
  unchanged plus `connection_count`, `connected` true with one active of two.
- usage: `GET …/connections/codex-1/usage` → normalized quota list.
- **response-scan (security)**: exercise every route above and assert the
  serialized response text contains none of
  `api_key, apiKey, token, accessToken, refreshToken, sk-secret, data`
  (`data` checked as a connection field, not the envelope key — scan
  `data.connections[*]`/`data.connection` objects for an exact allowlist of
  keys instead of substrings: `{id, provider, auth_type, name, email,
  active, priority, default_model, test_status, last_error}`). This is the
  allowlist test named in [validation.md](validation.md).

### 5c. Frontend

`npm run build` for type verification; if the repo has component
tests (`npm test -- ConnectionsView`), add: renders N account rows from
props, reorder computes normalized priorities, remove-all confirm shows the
count, and no property named `api_key` is ever rendered.

## Ordering summary

0. Probe + pins (gate) →
1. adapter (`nine_router.py`) →
2. models + service (`models/api.py`, `services/platform.py`) →
3. handlers + routes (`handlers/api.py`, `routes/setup.py`) →
4. frontend (`api/providers.ts` → `useConnections.ts` →
   `ConnectionsView.tsx`/`SystemView.tsx` → locales/styles) →
5. tests green, `make check`, then [validation.md](validation.md).
