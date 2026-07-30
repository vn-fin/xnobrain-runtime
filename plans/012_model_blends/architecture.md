# 012 — Architecture

How Model Blends fit the Brain4All layering. Cross-links:
[README.md](README.md), [findings.md](findings.md),
[approaches.md](approaches.md), [implementation.md](implementation.md).

Naming rule everywhere in this design: the **Brain4All surface says "blend"**
(routes, Pydantic models, service, UI); the **9router calls say "combos"**
(paths, settings keys). The adapter is the translation line. `auto` remains a
built-in, system-managed blend labeled "Auto" (approaches.md Decision C).

## Layering fit

Pure proxy feature. Brain4All stores **nothing**: 9router's SQLite is the
single source of truth for combos and strategies; every endpoint is a live
read/write against `:20128`. No repository work, no snapshots (nothing under
`DATA_DIR` is mutated by the blends API itself), no new process.

```
routes/setup.py            new Route(...) lines, tag "Blends"
   |
handlers/api.py            new operations: blends_* (HTTP <-> envelope only)
   |
services/blends.py         NEW BlendService: name guards, "auto" read-only,
   |                       strategy hydration/validation, DTO shaping
   |
integrations/nine_router.py  NineRouterManager grows public combo CRUD +
                             settings-whitelist strategy accessors; the
                             existing _request() transport is reused as-is
```

Local layers call each other in-process. `NineRouterAPIError` (503 when
9router is down) is already in `EXPECTED_ERRORS`
(`brain4all/services/platform.py` line 810), so handler failure envelopes come
for free (approaches.md Decision D).

## Adapter — `brain4all/integrations/nine_router.py`

`_ensure_auto_combo` already speaks the whole combo surface inline
(findings.md §7). Refactor those calls into public methods and make
`_ensure_auto_combo` use them, then add the settings accessors. Exact
signatures:

```python
# --- combo CRUD (all proxy /api/combos*; raise NineRouterAPIError on failure)

async def list_combos(self) -> list[dict[str, Any]]:
    """GET /api/combos -> normalized [{id, name, kind, models: list[str],
    created_at, updated_at}]. Defensive: skip non-mapping rows; coerce
    models to list[str]."""

async def create_combo(self, name: str, models: list[str]) -> dict[str, Any]:
    """POST /api/combos {"name": ..., "models": [...]} -> normalized combo."""

async def update_combo(
    self, combo_id: str, *, name: str | None = None,
    models: list[str] | None = None,
) -> dict[str, Any]:
    """PUT /api/combos/{id} with only the provided keys (upstream PUT is
    partial — findings.md §3). combo_id passes _safe_id first."""

async def delete_combo(self, combo_id: str) -> dict[str, Any]:
    """DELETE /api/combos/{id} -> {"id": ..., "deleted": True}."""

# --- strategy accessors (proxy /api/settings; whitelist combo keys ONLY)

async def combo_settings(self) -> dict[str, Any]:
    """GET /api/settings, return ONLY
    {"combo_strategy": str, "combo_strategies": dict, "combo_sticky_limit": int|None}.
    Never return or log the raw settings payload (findings.md §6)."""

async def set_combo_strategy(
    self, name: str, *, strategy: str,
    judge_model: str | None = None,
    fusion_tuning: Mapping[str, Any] | None = None,
) -> None:
    """Read-modify-write of settings.comboStrategies:
    GET /api/settings -> merge {name: {"fallbackStrategy": strategy,
    "judgeModel": ..., "fusionTuning": ...}} into the existing map (dropping
    None values) -> PATCH /api/settings {"comboStrategies": merged}.
    The whole map must be sent because the upstream merge is shallow at the
    top level (findings.md §6). fusion_tuning is pass-through opaque data."""

async def clear_combo_strategy(self, name: str) -> None:
    """Same read-modify-write, removing the entry. Called on blend delete and
    on rename (old name)."""

async def set_combo_sticky_limit(self, limit: int) -> None:
    """PATCH /api/settings {"comboStickyRoundRobinLimit": limit}. GLOBAL —
    applies to every round-robin blend (findings.md §4)."""
```

Rules for the accessors:

- Strategy literals are the verified upstream ones: `"fallback"`,
  `"round-robin"`, `"fusion"` (findings.md §4). No renaming layer.
- The settings read-modify-write has a lost-update window under concurrent
  writers (Brain4All + the 9router dashboard). Accepted for a single-operator
  local tool; documented in the docstring and findings.md §11. Do not build
  locking.

### `list_models()` extension — surface blends

Today the `owned_by not in active_owners` filter hides every combo
(findings.md §7). Change `list_models()` to collect, from the **same**
`/v1/models?kind=llm` payload (no extra HTTP call), entries with
`owned_by == "combo"` — excluding `auto`, which is already special-cased — as:

```python
{"id": <combo name>, "provider": "blend", "name": <combo name>}
```

and return `data` ordered: the hardcoded `auto` entry, then blends, then real
provider models. Why this is backward-safe:

- `PlatformService.providers()` and `APIHandlers._provider_models` filter by
  the six real provider ids, so `provider: "blend"` entries never leak into
  per-provider lists (findings.md §7).
- `_ensure_auto_combo` skips ids without `/`, and combo names cannot contain
  `/` (findings.md §3), so blends can never be swept into the `auto` combo.
  Belt-and-braces: also skip `provider == "blend"` entries in its selection
  loop.
- `NineRouterManager.usage(<blend name>)` already degrades to the "empty
  usage" response.

## Service — `brain4all/services/blends.py` (new)

```python
class BlendService:
    """Rules for user-named model blends over 9router combos."""

    RESERVED = NINE_ROUTER_DEFAULT_MODEL          # "auto"
    NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    MAX_MODELS = 24
    STRATEGIES = ("fallback", "round-robin", "fusion")

    def __init__(self, router: NineRouterManager): ...

    async def list_blends(self) -> dict[str, Any]
    async def create_blend(self, body: Mapping[str, Any]) -> dict[str, Any]
    async def update_blend(self, blend_id: str, body: Mapping[str, Any]) -> dict[str, Any]
    async def delete_blend(self, blend_id: str) -> dict[str, Any]
    async def available_models(self) -> dict[str, Any]
```

Constructed in `PlatformService.__init__`
(`brain4all/services/platform.py`) as `self.blends = BlendService(router)`,
mirroring `self.kanban` / `self.analytics`.

### Service rules (the whole policy layer)

1. **`auto` is read-only.** `list_blends` includes it, decorated
   `{"system": true, "read_only": true, "name": "auto", "display_name":
   "Auto"}` with its live model list from 9router. `update_blend` /
   `delete_blend` on the combo whose `name == "auto"` raise
   `ServiceError("the Auto blend is system-managed", status=403,
   code="blend_reserved")`. `create_blend` with name `auto`
   (case-insensitive) → the same 403. `ensure_auto_combo()` remains the only
   writer of `auto`.
2. **Name guard.** `NAME_RE` (leading alphanumeric, then `[A-Za-z0-9._-]`, ≤64
   chars) — a strict subset of both the 9router charset and the adapter's
   `_safe_id` (findings.md §7), so a valid blend name is always a valid combo
   name and a valid URL id. Violation → 400 `invalid_blend_name`.
3. **No shadowing.** The name must not equal (case-insensitive) `auto`, any
   existing blend name (409 `blend_name_conflict` — pre-checked so Brain4All
   returns a clean 409 instead of upstream's generic 400), or any **real
   model id** currently listed (409 `blend_name_conflict`; a combo name equal
   to a model id would shadow the model at request time — findings.md §11).
4. **Models guard.** `models` is an ordered list: 1–24 entries, no
   duplicates, every entry present in `available_models()` (real models only —
   no blends, no `auto`, so nesting is impossible through this surface).
   Order is preserved exactly as given — it is the fallback order.
5. **Strategy guard.** `strategy ∈ {"fallback", "round-robin", "fusion"}`
   (default `fallback`). `fusion` requires ≥2 models and a `judge_model` that
   is a real model id (may also be a member of the blend). `judge_model` is
   rejected for non-fusion strategies. `sticky_limit` (int ≥1) is accepted
   only alongside `round-robin` and writes the **global**
   `comboStickyRoundRobinLimit` (findings.md §4) — the response flags it
   `"sticky_limit_scope": "global"` so the UI can say so.
6. **Write order.** Create: combo first, then strategy (skip the settings
   write entirely when strategy is `fallback` with no judge — absence means
   fallback upstream). If the strategy write fails after combo creation,
   delete the combo (best-effort compensation) and re-raise. Rename: update
   combo, then move the `comboStrategies` entry old→new. Delete: delete
   combo, then `clear_combo_strategy` best-effort.
7. **Hydration.** `list_blends` merges `list_combos()` with
   `combo_settings()`: each blend's `strategy` =
   `combo_strategies[name].fallbackStrategy or combo_strategy or "fallback"`
   — the same resolution 9router applies (findings.md §4).
8. **Errors.** All guard failures raise `ServiceError` (from
   `.platform`); adapter failures propagate as `NineRouterAPIError` (503 when
   down). Both are in `EXPECTED_ERRORS`.

### Blend DTO (response shape inside the standard envelope)

```json
{
  "id": "cmb_x1",                 // 9router combo id (stable across rename)
  "name": "research-blend",       // == the virtual model id used by agents
  "display_name": "research-blend",
  "system": false,                // true only for "auto"
  "read_only": false,             // true only for "auto"
  "models": ["cc/claude-opus-4-1", "cx/gpt-5.4"],
  "strategy": "fallback",         // "fallback" | "round-robin" | "fusion"
  "judge_model": null,            // set when strategy == "fusion"
  "sticky_limit": null,           // global value, echoed for round-robin
  "sticky_limit_scope": "global",
  "created_at": "...", "updated_at": "..."
}
```

## Route table and Pydantic models

Versioned under `/api/brain/v1`, new tag `Blends`, added to
`brain4all/routes/setup.py` (the only assembly point). **Order matters**:
`available-models` must be declared before `{blend_id}` because Starlette
matches routes in registration order.

| Method | Path | Operation | Body | Purpose |
|--------|------|-----------|------|---------|
| GET | `/api/brain/v1/blends` | `blends_list` | — | All blends (incl. `auto` as system), hydrated with strategy. |
| POST | `/api/brain/v1/blends` | `blends_create` | `BlendCreate` | Create a blend (+ strategy when not default). 201. |
| GET | `/api/brain/v1/blends/available-models` | `blends_available_models` | — | Real models eligible for inclusion (no blends, no `auto`). |
| PATCH | `/api/brain/v1/blends/{blend_id}` | `blends_patch` | `BlendPatch` | Rename / edit models+order / change strategy+judge / sticky limit. |
| DELETE | `/api/brain/v1/blends/{blend_id}` | `blends_delete` | — | Delete blend + its strategy entry. 403 for `auto`. |

`blend_id` is the 9router combo **id** (stable across rename); responses carry
both `id` and `name`.

Pydantic (`brain4all/models/api.py`, exported via
`brain4all/models/__init__.py`):

```python
class BlendCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64,
                      pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    models: list[str] = Field(min_length=1, max_length=24)
    strategy: Literal["fallback", "round-robin", "fusion"] = "fallback"
    judge_model: str | None = Field(default=None, max_length=256)
    sticky_limit: int | None = Field(default=None, ge=1, le=1000)

class BlendPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64,
                             pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    models: list[str] | None = Field(default=None, min_length=1, max_length=24)
    strategy: Literal["fallback", "round-robin", "fusion"] | None = None
    judge_model: str | None = Field(default=None, max_length=256)
    sticky_limit: int | None = Field(default=None, ge=1, le=1000)
```

Handler operations (`brain4all/handlers/api.py` `_operation` map — HTTP
translation only, all rules live in the service):

```python
"blends_list": (s.blends.list_blends, "blends retrieved successfully", 200),
"blends_create": (lambda: s.blends.create_blend(body), "blend created successfully", 201),
"blends_available_models": (s.blends.available_models, "blend-eligible models retrieved successfully", 200),
"blends_patch": (lambda: s.blends.update_blend(p["blend_id"], body), "blend updated successfully", 200),
"blends_delete": (lambda: s.blends.delete_blend(p["blend_id"]), "blend deleted successfully", 200),
```

## How a blend name reaches an agent profile

Unchanged, existing path (findings.md §8) — stated here because it is the
reason the feature needs zero Hermes work:

```
UI model picker ──> agentsApi.update(id, {provider, model: "<blend name>"})
  └─ src/src/api/agents.ts routedConfig(): provider forced to "nine-router"
PATCH /api/brain/v1/agents-configs/{agent_id}   (ConfigPatch)
  └─ handlers/api.py "config_agent_patch"
  └─ PlatformService.update_agent_config: snapshot config.yaml, then
  └─ AgentManager.update_config: _nonempty_string(model) ->
     _set_nested(config, ("model","default"), model) ->
     normalize_nine_router_config(config, model)
profiles/<agent-id>/config.yaml:
  model: {provider: custom:nine-router, default: "<blend name>", base_url: http://127.0.0.1:20128/v1}
```

At chat time Hermes sends `model: "<blend name>"` to 9router, which resolves
the combo and applies its strategy (findings.md §4). The session row records
`model = "<blend name>"`, which is what plan-009 analytics groups by
(findings.md §9).

## Sequence — create blend → use in agent → chat

```
User            Brain4All API              9router (:20128)            Hermes
 |  POST /blends {name:"duo",     |                              |
 |   models:[cc/opus, cx/gpt],    |                              |
 |   strategy:"fallback"}         |                              |
 |------------------------------->|  POST /api/combos            |
 |                                |----------------------------->| (combo row)
 |                                |  (strategy default: no       |
 |                                |   settings write needed)     |
 |  201 {id, name:"duo", ...}     |                              |
 |<-------------------------------|                              |
 |  PATCH /agents-configs/a1      |                              |
 |   {model:"duo"}                |                              |
 |------------------------------->| (snapshot config.yaml;       |
 |                                |  model.default = "duo")      |
 |  POST .../chat/stream          |                              |
 |------------------------------->|  hermes chat --model duo --------------->|
 |                                |                              |  POST /v1/chat/completions {model:"duo"}
 |                                |<-----------------------------------------|
 |                                |  combo "duo" resolved:       |
 |                                |  try cc/opus, on error       |
 |                                |  fail over to cx/gpt         |
 |                                |  log: Combo "duo" with 2     |
 |                                |  models (strategy: fallback) |
 |  SSE tokens                    |                              |
 |<-------------------------------|<-----------------------------|
```

## React UI

Two touch points, following the existing feature layout
(`src/src/features/system/`, `src/src/api/*.ts`, `src/src/hooks/*.ts`):

### 1. "Model Blends" management panel (Settings)

- `src/src/hooks/useRouter.ts`: extend
  `SettingsSection = 'profiles' | 'vm' | 'connectors' | 'blends'`.
- `src/src/features/system/SystemView.tsx`: add tab
  `{ id: 'blends', label: 'Model Blends' }` and a
  `{section === 'blends' && <BlendsSection .../>}` card, mirroring how the
  `connectors` tab embeds `ConnectionsView`.
- New components under `src/src/features/system/`:
  - **`BlendsSection.tsx`** — list: one row per blend with name, strategy
    chip (`fallback` / `round-robin` / `fusion`), model count, and for `auto`
    a "System" badge with edit/delete disabled. "New blend" button. Delete
    with inline confirm. States: loading, empty ("No blends yet — a blend is
    a named group of models that behaves as one model"), and unavailable
    banner when the API returns 503 ("9router is not reachable"), consistent
    with `ConnectionsView` state handling.
  - **`BlendEditorDialog.tsx`** — create/edit dialog: name input (validated
    live against the name rule); ordered multi-select of
    `GET /blends/available-models` with per-row up/down buttons (order = 
    fallback order); strategy radio (fallback | round-robin | fusion); when
    `round-robin`, a sticky-limit number input labeled "Sticky limit (applies
    to all round-robin blends)"; when `fusion`, a judge-model select over the
    same available models plus the caveat copy:
    **"Fusion runs every model in the blend on each request (higher cost);
    tools are disabled for fusion."** (verified tools-stripping —
    findings.md §4).
- API client `src/src/api/blends.ts` (mirrors `src/src/api/analytics.ts`
  style: typed DTOs + `request()` from `src/src/api/client.ts`).
- Hook `src/src/hooks/useBlends.ts` (mirrors `useConnections.ts`: `blends`,
  `status`, `error`, `refresh`, `createBlend`, `updateBlend`, `deleteBlend`,
  `availableModels`).

### 2. Model pickers show blends

- **Composer picker** (`src/src/components/ChatArea.tsx`, the
  `model-picker` menu around lines 726–761): prepend a **"Blends"** group —
  rendered from a new `blends: string[]` prop (names, `auto` first labeled
  "Auto") — above the per-provider groups. Selecting one calls the existing
  `onSelectModel('blend', name)`; `routedConfig()` already normalizes the
  provider (findings.md §8). `App.tsx` passes the blends list from
  `useBlends`.
- **Agent settings dialog** (`src/src/components/modals.tsx`, model field at
  lines 307–308): keep the free-text input but add a datalist/select of
  blends + real models so a blend is one click. (Minimal change; the input
  already accepts any string.)
- Analytics by-model table needs no change; blend names appear as the model
  dimension (findings.md §9). Optional nicety: style rows whose model matches
  a known blend name with the same "blend" chip.

## Failure behavior

When 9router is down every blends endpoint returns the standard failure
envelope `{success:false, message:"9Router is unavailable",
error:{code:"nine_router_unavailable"}, status_code:503}` (existing
`_request()` mapping + `EXPECTED_ERRORS`). The UI shows the unavailable
banner and disables mutation buttons; it never caches or fabricates blend
data (approaches.md Decision D). No retries beyond the adapter's existing
single re-attempt.
