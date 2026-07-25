# 011 — Architecture

How multi-account connections fit the fixed Brain4All layering. Read
[findings.md](findings.md) first for the verified 9router facts and the
Phase 0 probe list.

## Layering fit

```
React Connectors UI (src/src/components/ConnectionsView.tsx via
                     src/src/features/system/SystemView.tsx)
        │  src/src/api/providers.ts + src/src/hooks/useConnections.ts
        ▼
routes/setup.py ── the only route-assembly point (new Route lines, tag Providers)
        ▼
handlers/api.py ── HTTP translation only (six new operations in _operation)
        ▼
services/platform.py ── rules: provider allowlist, connection-ownership guard,
                        "connected = ≥1 active", priority normalization
        ▼
integrations/nine_router.py ── NineRouterManager: filtered HTTP facade over
        ▼                      9router (x-9r-cli-token via _request)
9router :20128 ── owns providerConnections (SQLite), keys/tokens, rotation
```

No repository layer is involved: **this feature stores nothing in Brain4All**.
All account state lives in 9router's database (which is fine — it is the
credential authority already). No `DATA_DIR` file changes, therefore no
snapshots. Hermes profiles are untouched: `normalize_nine_router_config()`
keeps every profile pointed at the single 9router endpoint.

## Integration adapter — `brain4all/integrations/nine_router.py`

All new methods go through the existing `_request()` (auth header, retry,
error mapping) and `_safe_id()` (id validation), and return **only
allowlisted fields** — the `data` column / key material never crosses this
boundary.

### Changed method

```python
async def list_connections(self) -> dict[str, Any]:
    """GET /api/providers, filtered to SUPPORTED_ROUTER_PROVIDERS.

    CHANGE: each connection row additionally carries
      "email":    str(item.get("email") or "")
      "priority": self._number(item.get("priority"))   # Phase 0: confirm field name
    Everything else (id, provider, auth_type, name, active, default_model,
    test_status, last_error) is unchanged, so every existing caller
    (providers(), list_models(), usage(), status()) keeps working.
    """
```

### New methods

```python
async def update_connection(
    self,
    connection_id: Any,
    *,
    active: bool | None = None,
    priority: int | None = None,
) -> dict[str, Any]:
    """PUT /api/providers/{id} with a partial body.

    Body (Phase 0-verified shape): {"isActive": bool} and/or {"priority": int}.
    Raises NineRouterAPIError(400) when both are None.
    After a successful update, re-ensure the auto combo the same way
    delete_connection() does (deactivation can remove a provider's models):
        models = (await self.list_models(ensure_auto=False))["data"]
        await self._ensure_auto_combo(models)
    Returns _filtered_connection_response(payload) when the router echoes the
    connection, else {"object": "nine_router.provider_update",
                      "id": connection_id, "updated": True}.
    """

async def usage_for_connection(self, connection_id: Any) -> dict[str, Any]:
    """GET /api/usage/{connectionId} for one account, unfiltered by model.

    Same normalization as usage() (reuse _normalize_quota; no
    _quota_matches_model filter — this view is account-scoped, not
    model-scoped). Returns:
      {"object": "router.connection_usage", "connection_id": ...,
       "available": bool, "plan": str, "message": str,
       "quotas": [ {name, used, total, remaining_percent, reset_at,
                    unlimited} ] (capped [:12]) }
    """
```

`_filtered_connection_response()` gains the same two fields as
`list_connections` (`email`, `priority`) — still an explicit allowlist,
nothing pass-through. `create_api_key_connection()`, `delete_connection()`,
`test_connection()`, and `oauth()` are reused as-is.

Refactor note: extract the quota-normalization loop shared by `usage()` and
`usage_for_connection()` into a private
`_quota_list(payload, *, provider="", model_id="") -> list[dict]` so the two
stay in sync (model filtering applied only when `model_id` is given).

## Service rules — `brain4all/services/platform.py`

New methods (same class that owns `providers()` today). Rules live here, not
in handlers:

```python
async def list_provider_connections(self, provider: str) -> dict[str, Any]:
    """All connections for one provider, sorted by (priority, name).
    404 via _provider() when the provider is not in SUPPORTED_PROVIDERS.
    Shape: {"provider_id": provider,
            "connected": <≥1 active>,
            "connections": [ ...adapter rows... ]}"""

async def add_provider_connection(self, provider: str, body: Mapping) -> dict:
    """API-key providers only (API_KEY_PROVIDERS); OAuth providers raise
    ServiceError(400, "use the provider connect flow to add an OAuth
    account") — OAuth additions reuse start/submit_provider_connect, which
    already create a new 9router row per completed flow.
    Delegates to router.create_api_key_connection({provider, api_key, name,
    default_model}). Returns {"provider_id", "connected": True, "connection":
    <filtered>}."""

async def patch_provider_connection(self, provider, connection_id, body) -> dict:
    """Validates the pair via _owned_connection() (below); requires at least
    one of active/priority; clamps priority to 0..999; delegates to
    router.update_connection. Returns the refreshed connection row from a
    follow-up list (source of truth = 9router)."""

async def test_provider_connection(self, provider, connection_id) -> dict:
    """_owned_connection() then router.test_connection(connection_id).
    Returns {"provider_id", "connection_id", "healthy": bool,
             "status": "healthy"|"unhealthy", "message": <error or "">}."""

async def delete_provider_connection(self, provider, connection_id) -> dict:
    """_owned_connection() then router.delete_connection(connection_id).
    Returns {"provider_id", "connection_id", "deleted": True,
             "connected": <recomputed ≥1 active>}."""

async def connection_usage(self, provider, connection_id) -> dict:
    """_owned_connection() then router.usage_for_connection(connection_id)."""

async def _owned_connection(self, provider: str, connection_id: str) -> dict:
    """Guard: provider must be supported (_provider()), and connection_id must
    belong to that provider in the current 9router list — otherwise
    ServiceError(404, code="not_found"). Prevents cross-provider access to an
    arbitrary router connection id through a spoofed URL."""
```

### Changed provider-level semantics (backward compatible)

- `providers()` (lines 592–611): keep the response shape byte-for-byte, but
  compute from **all** of the provider's connections:
  - `connected` / `status`: **≥1 active connection** (same truth value as
    today when 0 or 1 connection exists — strictly more correct with many).
  - `last_test_status` / `default_model`: taken from the **highest-priority
    active** connection (sorted `(priority, name)`), replacing "whichever was
    first".
  - New additive field `connection_count: int` (ignored by the existing
    mapper `src/src/api/mappers/providers.ts`, used by the new UI badge).
- `disconnect_provider()` (lines 662–669): behavior unchanged — explicit
  **remove-all-accounts** (approaches.md Decision D). The UI adds a confirm
  naming the count ("Remove all N accounts for Codex?"). Individual removal
  is the new normal path.
- `test_provider()` (lines 671–678): test the highest-priority **active**
  connection instead of the first row of any state; unchanged response shape.

## API contract — routes and models

### Route table (append to the `Providers` block, `brain4all/routes/setup.py` after line 134)

| Method | Path | Operation | Body model |
|---|---|---|---|
| GET | `/agent-gateway/v1/providers/{provider_id}/connections` | `provider_connections_list` | — |
| POST | `/agent-gateway/v1/providers/{provider_id}/connections` | `provider_connection_create` | `ConnectionCreate` |
| PATCH | `/agent-gateway/v1/providers/{provider_id}/connections/{connection_id}` | `provider_connection_patch` | `ConnectionPatch` |
| POST | `/agent-gateway/v1/providers/{provider_id}/connections/{connection_id}/test` | `provider_connection_test` | — |
| DELETE | `/agent-gateway/v1/providers/{provider_id}/connections/{connection_id}` | `provider_connection_delete` | — |
| GET | `/agent-gateway/v1/providers/{provider_id}/connections/{connection_id}/usage` | `provider_connection_usage` | — |

All `tags=("Providers",)`. Exact `Route(...)` lines are in
[implementation.md](implementation.md) Phase 3. Every existing provider-level
route (lines 126–134) is preserved unchanged.

### Pydantic models (`brain4all/models/api.py`, next to `ProviderCredential`)

```python
class ConnectionCreate(BaseModel):
    """Add an API-key account to a provider. The key is passed through to
    9router and never stored or echoed by Brain4All."""
    api_key: str = Field(min_length=1, max_length=4096)
    name: str | None = Field(default=None, max_length=128)
    default_model: str | None = Field(default=None, max_length=128)


class ConnectionPatch(BaseModel):
    """Partial update of one connection. At least one field must be set
    (service-enforced)."""
    active: bool | None = None
    priority: int | None = Field(default=None, ge=0, le=999)
```

Responses use the standard envelope from `APIHandlers.success()`
(`{"success", "data", "message", "status_code"}`), like every other route.

## Sequence — add a second account

### API-key variant (openai / anthropic / gemini)

```
UI "Add account" form (key, optional name)
  → POST /agent-gateway/v1/providers/openai/connections   {api_key, name?}
    handlers: provider_connection_create
    service:  add_provider_connection("openai", body)     (allowlist + mode check)
    adapter:  create_api_key_connection → POST :20128/api/providers
              → ensure_auto_combo()
    response: filtered connection {id, provider, auth_type, name, email,
              active, priority, default_model}   ← NO key material
  → UI refreshes GET .../openai/connections → two rows
```

### OAuth variant (claude / codex / antigravity)

Reuses the existing connect flow end-to-end — no new backend path:

```
UI "Add account" on an already-connected provider
  → openProviderAuthPopup()                    (src/src/utils/providerAuth.ts)
  → POST /agent-gateway/v1/providers/codex/connect
    service.start_provider_connect → router.oauth("codex","authorize",GET)
    ← login_url; popup navigates to it
  → user authorizes with the SECOND account; pastes callback URL
  → PUT /agent-gateway/v1/providers/codex/connect          {text: <callback>}
    service.submit_provider_connect → router.oauth("codex","exchange",POST)
    9router creates a NEW providerConnections row           (Phase 0 item 7)
  → UI refreshes GET .../codex/connections → two rows
```

The only frontend change for OAuth is *offering* the connect action while
`connected === true` (today `ConnectionsView` hides it) and refreshing the
connections list on completion. The hook's existing poll loop
(`useConnections.ts` lines 44–83) keys on provider `connected`, which is
already true when adding a second account — Phase 4 switches the completion
signal for this path to "connection count increased" (see
[implementation.md](implementation.md) Phase 4).

## UI component sketch

`ConnectionsView.tsx` card, expanded state (per provider):

```
┌─ [icon] Codex                                [connected · 2 accounts] ┐
│  desc…                                                               │
│  Accounts                                                            │
│  ⋮⋮ ● work@example.com   oauth   [Active ✓] [↑][↓] [Test ●] [▁▃▅ 62%] [✕] │
│  ⋮⋮ ○ personal@ex.com    oauth   [Active ✗] [↑][↓] [Test ●] [▁▁▁  —%] [✕] │
│  [+ Add account]                                                     │
│  "Active accounts rotate; order sets fallback preference."  (copy per │
│   Phase 0 finding — approaches.md Decision C)                        │
│  [Test] [Remove all accounts…]        ← existing provider-level row   │
└──────────────────────────────────────────────────────────────────────┘
```

- Row label: `email || name`; auth type as a small tag; never any key
  material (the API never returns it, and the UI renders only typed fields).
- Active toggle → PATCH `{active}`; up/down → PATCH `{priority}` writing
  normalized `0..n-1` (approaches.md Decision B); Test → per-row status dot
  (`test_status` refreshed from the response); usage bar → lowest
  `remaining_percent` across that connection's quota windows, lazy-loaded.
- "✕" removes one account (confirm). Provider-level "Remove all accounts…"
  keeps delete-all semantics behind an explicit count-naming confirm.
- "Add account": API-key providers open an inline key form (input
  `type="password"`, cleared on submit, like the existing panel); OAuth
  providers run the popup flow above.

State lives in `useConnections.ts`: `connectionsByProvider:
Record<string, ProviderConnection[]>`, `usageByConnection:
Record<string, ConnectionUsage>`, plus per-row pending flags. API methods in
`src/src/api/providers.ts` (typed, no `any`). Details per file in
[implementation.md](implementation.md) Phase 4.

## Backward compatibility

- All nine existing provider routes keep their paths, verbs, operations, and
  response shapes; `providers()` adds only `connection_count`.
- "Connected" is now defined as **≥1 active connection** everywhere
  (service + UI badge). With zero or one connection this is identical to
  today's behavior.
- `update_provider()` (PATCH `/update`) keeps working and remains equivalent
  to `POST .../connections` for API-key providers; the UI's shared key panel
  migrates to the new endpoint, but the old route is not removed.
- Existing tests (`FakeRouter` in
  [`brain4all/tests/test_fastapi.py`](../../brain4all/tests/test_fastapi.py),
  `FakeNineRouterManager` in
  [`brain4all/tests/test_nine_router.py`](../../brain4all/tests/test_nine_router.py))
  keep passing: `list_connections` only gains fields, and defaults
  (`priority` absent → 0, `email` absent → "") are tolerated.

## Security invariants

1. Brain4All never sees, stores, returns, or logs API keys or OAuth tokens.
   The create path passes the key through to 9router in one request body and
   the response is rebuilt from an allowlist (`_filtered_connection_response`
   — keep it). `ConnectionCreate.api_key` must never appear in logs: handlers
   do not log bodies (existing behavior — preserve).
2. `credentials.env` continues to carry only `NINE_ROUTER_API_KEY`, as today.
3. All state for this feature lives in 9router's DB; Brain4All writes no
   file, so snapshot rules do not apply — stated explicitly and asserted in
   [validation.md](validation.md) (no `DATA_DIR` diff after exercising every
   new route).
4. `connection_id` path params pass `_safe_id()` before URL interpolation
   (already the adapter's pattern), and the service ownership guard prevents
   addressing another provider's connection.
