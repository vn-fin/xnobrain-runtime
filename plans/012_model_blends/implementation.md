# 012 — Implementation

Ordered, phased, file-by-file. Follow the chosen approaches in
[approaches.md](approaches.md) and the contract in
[architecture.md](architecture.md). Naming rule throughout: Brain4All surface
says **blend**; 9router calls say **combos**; upstream paths/keys are never
renamed. Brain4All persists nothing for this feature — every endpoint is a
live 9router proxy.

## Phase 0 — Probe the pinned 9router

The dependency is already pinned (`Dockerfile.backend` line 18
`ARG NINE_ROUTER_NPM_VERSION=0.5.40`; `scripts/install-linux.sh` line 261).
Do not bump it in this plan.

File: `brain4all/tests/test_blends_probe.py` (new). A live-probe suite that
runs against the local 9router and **skips cleanly when it is not running**
(mirror the guard style of `@unittest.skipUnless(HERMES_AVAILABLE, ...)` in
`brain4all/tests/test_kanban.py` line 52 — here, attempt
`NineRouterManager().status()` in `setUpClass` and `raise unittest.SkipTest`
when `available` is false). Use a real `NineRouterManager` (its `_request`
handles the CLI token) and a unique probe name like `b4a-probe-<pid>`.

Implement exactly the probes in [findings.md](findings.md) §10:

1. `GET /api/combos` returns `{"combos": [...]}` with `id/name/models/kind/
   createdAt/updatedAt` per row.
2. `POST /api/combos` with two real model ids → 201-created combo with `id`;
   visible in the list afterwards.
3. Name charset: `"bad name!"` rejected (4xx), `"ok-name_1.x"` accepted.
4. Duplicate name rejected (record upstream status code).
5. `PUT /api/combos/{id}` partial bodies: models-only leaves the name; name-
   only leaves the models.
6. `DELETE /api/combos/{id}` → `{"success": true}`; unknown id → 404 (assert
   `NineRouterAPIError.status == 404` through the adapter).
7. The created combo appears in `GET /v1/models?kind=llm` as
   `{"id": <name>, "owned_by": "combo"}`.
8. `GET /api/settings` is an object without a `password` key; record presence
   of the three combo keys.
9. `PATCH /api/settings {"comboStrategies": {...}}` round-trips and
   **replaces the whole map** (write map A, then map B, assert A's entry is
   gone) — proving the read-modify-write requirement.
10. Fusion entry (`fallbackStrategy: "fusion"`, `judgeModel`) round-trips;
    log the observed `fusionTuning` shape if any.
11. `PATCH {"comboStickyRoundRobinLimit": 3}` round-trips (global key).
12. `tearDown`/`addCleanup`: delete every probe combo; restore the prior
    `comboStrategies` map and sticky limit exactly.

Gate: the rest of the plan proceeds only after this suite passes against
0.5.40. If any probe contradicts [findings.md](findings.md), update
findings.md first and adjust the design before writing dependent code.

## Phase 1 — Adapter refactor (`brain4all/integrations/nine_router.py`)

Signatures and rules in [architecture.md](architecture.md) § Adapter.

1. **Public combo CRUD.** Add `list_combos`, `create_combo`, `update_combo`,
   `delete_combo` as thin `_request` wrappers returning normalized dicts
   (`id`, `name`, `kind`, `models: list[str]`, `created_at`, `updated_at`).
   `combo_id` goes through the existing `_safe_id(...)` before URL
   interpolation, exactly as `_ensure_auto_combo` does today (lines 362,
   376).
2. **Refactor `_ensure_auto_combo`** (lines 336–382) to call the new public
   methods instead of inline `_request` calls. Behavior must be
   byte-identical: same selection loop (≤12 models, one per owner, `/`
   required in the id), same create/update/delete-by-name logic. Add a
   `provider == "blend"` skip in the selection loop as belt-and-braces
   (architecture.md).
3. **Settings accessors.** Add `combo_settings`, `set_combo_strategy`,
   `clear_combo_strategy`, `set_combo_sticky_limit` per the signatures.
   `combo_settings` whitelists the three combo keys and never returns or logs
   anything else from the settings payload. The strategy writers do
   GET → merge/remove the per-name entry → `PATCH` the **whole**
   `comboStrategies` map (findings.md §6). Drop `None` values from entries;
   pass `fusion_tuning` through opaquely.
4. **Extend `list_models()`** (lines 217–272): inside the existing loop over
   `/v1/models` rows, collect `owned_by == "combo"` entries (the current
   `owner not in active_owners` branch) into a separate `blends` list as
   `{"id": model_id, "provider": "blend", "name": model_id}` — `auto` is
   already excluded by the earlier `model_id == NINE_ROUTER_DEFAULT_MODEL`
   check. Return `data` as `[auto entry, *blends, *models]`. No second HTTP
   call. Existing consumers are unaffected because they filter by real
   provider ids (findings.md §7).

## Phase 2 — Service + models

### 2a. `brain4all/models/api.py`

Add `BlendCreate` and `BlendPatch` exactly as specified in
[architecture.md](architecture.md) § Route table (name pattern
`^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`, models 1–24, strategy literal set
`fallback|round-robin|fusion`, `judge_model`, `sticky_limit ge=1`). Export
both in `brain4all/models/__init__.py` (extend the existing `from .api import
(...)` block, alphabetical position).

### 2b. `brain4all/services/blends.py` (new)

`BlendService(router: NineRouterManager)` implementing exactly the eight
service rules in [architecture.md](architecture.md) § Service rules:

- `list_blends()` — `list_combos()` + `combo_settings()`; hydrate each
  blend's strategy with the upstream resolution order
  (`comboStrategies[name].fallbackStrategy or comboStrategy or "fallback"`);
  decorate `auto` with `system/read_only` and display name "Auto"; sort:
  `auto` first, then by name.
- `create_blend(body)` — guards (name regex double-check, reserved `auto`
  case-insensitive, collision vs existing blends **and** real model ids,
  models all in `available_models()`, no duplicates, fusion ⇒ ≥2 models +
  valid `judge_model`, `judge_model` only with fusion, `sticky_limit` only
  with round-robin); then `create_combo`; then `set_combo_strategy` /
  `set_combo_sticky_limit` when non-default; compensating `delete_combo` if
  the settings write raises; return the composed DTO.
- `update_blend(blend_id, body)` — resolve the combo by id (404
  `blend_not_found`); 403 `blend_reserved` when its name is `auto`; apply the
  same guards to changed fields; on rename move the strategy entry old→new;
  return the fresh DTO.
- `delete_blend(blend_id)` — 403 for `auto`; `delete_combo` then best-effort
  `clear_combo_strategy`; return `{"id": ..., "name": ..., "deleted": true}`.
- `available_models()` — `router.list_models()` filtered to
  `provider not in {"blend", NINE_ROUTER_PROVIDER_KEY}` and
  `id != "auto"`; returns `{"data": [{id, provider, name}, ...]}`.

Errors: `ServiceError` (import from `.platform`) with the codes in
architecture.md (`invalid_blend_name` 400, `blend_name_conflict` 409,
`blend_reserved` 403, `blend_not_found` 404, `invalid_blend` 400 for
models/strategy guard failures). Register the service:

- `brain4all/services/platform.py` `PlatformService.__init__`: after the
  analytics wiring (line 76–77 pattern), add
  `from .blends import BlendService` / `self.blends = BlendService(router)`.
- `brain4all/services/__init__.py`: export `BlendService` alongside
  `KanbanService`.

## Phase 3 — Handlers + routes

### 3a. `brain4all/handlers/api.py`

Add to the `_operation` map (keep alphabetical grouping near the provider
operations; no logic in the handler):

```python
"blends_list": (s.blends.list_blends, "blends retrieved successfully", 200),
"blends_create": (lambda: s.blends.create_blend(body), "blend created successfully", 201),
"blends_available_models": (s.blends.available_models, "blend-eligible models retrieved successfully", 200),
"blends_patch": (lambda: s.blends.update_blend(p["blend_id"], body), "blend updated successfully", 200),
"blends_delete": (lambda: s.blends.delete_blend(p["blend_id"]), "blend deleted successfully", 200),
```

### 3b. `brain4all/routes/setup.py`

Import `BlendCreate, BlendPatch` in the existing `from ..models import (...)`
block, and add to `ROUTES` (after the Providers block; **`available-models`
before `{blend_id}`** — Starlette matches in registration order):

```python
Route("GET", "/agent-gateway/v1/blends", "blends_list", tags=("Blends",)),
Route("POST", "/agent-gateway/v1/blends", "blends_create", BlendCreate, tags=("Blends",)),
Route("GET", "/agent-gateway/v1/blends/available-models", "blends_available_models", tags=("Blends",)),
Route("PATCH", "/agent-gateway/v1/blends/{blend_id}", "blends_patch", BlendPatch, tags=("Blends",)),
Route("DELETE", "/agent-gateway/v1/blends/{blend_id}", "blends_delete", tags=("Blends",)),
```

No other file registers routes (`routes/setup.py` is the only assembly
point).

## Phase 4 — Frontend

Follow the existing per-feature file layout; all copy in plain English
matching current UI tone.

1. **`src/src/api/blends.ts`** (new) — mirror `src/src/api/analytics.ts`:
   typed DTOs (`Blend`, `BlendStrategy = 'fallback' | 'round-robin' |
   'fusion'`, `BlendCreateInput`, `BlendPatchInput`, `BlendModel`) and a
   `blendsApi` object with `list()`, `create(input)`, `update(id, input)`,
   `remove(id)`, `availableModels()` calling
   `/agent-gateway/v1/blends*` through `request()` from
   `src/src/api/client.ts`.
2. **`src/src/hooks/useBlends.ts`** (new) — mirror
   `src/src/hooks/useConnections.ts`: state `{blends, status, error}`,
   `refresh()` on mount, `createBlend`, `updateBlend`, `deleteBlend`,
   `loadAvailableModels`; surface a distinct `unavailable` flag when the API
   error status is 503 so the UI can show the 9router-down banner.
3. **Settings tab** — `src/src/hooks/useRouter.ts`: extend the
   `SettingsSection` union (line 4) with `'blends'` (and the boot-route
   parser). `src/src/features/system/SystemView.tsx`: add
   `{ id: 'blends', label: 'Model Blends' }` to the `tabs` array (line 61)
   and a `{section === 'blends' && <BlendsSection ... />}` card.
4. **`src/src/features/system/BlendsSection.tsx`** (new) — list + delete
   confirm + "New blend"; per-row: name, strategy chip, model count, `auto`
   shown with a "System" badge and disabled actions. Unavailable banner on
   503.
5. **`src/src/features/system/BlendEditorDialog.tsx`** (new) — create/edit
   dialog per the sketch in [architecture.md](architecture.md) § React UI:
   validated name input; ordered multi-select with up/down reorder over
   `availableModels`; strategy radio; sticky-limit input (round-robin, with
   the "applies to all round-robin blends" note); judge-model select
   (fusion) and the fusion caveat copy: *"Fusion runs every model in the
   blend on each request (higher cost); tools are disabled for fusion."*
6. **Composer picker** — `src/src/components/ChatArea.tsx`: add a
   `blends: string[]` prop; render a "Blends" group at the top of the
   `model-picker` menu (before the `providers.map(...)` block at lines
   734–756), each entry calling `onSelectModel('blend', name)`; keep the
   active-check logic (`agent.model === name`). `src/src/App.tsx`: obtain
   names from `useBlends` and pass them where `ChatArea` is rendered
   (`onSelectModel` at line 282 already routes through
   `assistants.updateAgent` → `routedConfig`, which forces provider
   `nine-router` — no backend change).
7. **Agent settings dialog** — `src/src/components/modals.tsx` (model input
   at lines 307–308): attach a `<datalist>` (or small select) fed with blend
   names + real model ids so blends are selectable without retyping.
8. **Contracts** — add the blend DTO types to
   `src/src/api/contracts/agentGateway.ts` alongside
   `ProviderModelsResponseDTO`.

## Phase 5 — Tests

1. **`brain4all/tests/test_blends.py`** (new) — unit tests with the
   fake-router pattern from `brain4all/tests/test_nine_router.py`
   (`FakeNineRouterManager` overriding `_request` with a
   `(method, path) -> response` map, recording requests). Fixtures include
   `("GET", "/api/combos")`, `("POST", "/api/combos")`,
   `("PUT", "/api/combos/<id>")`, `("DELETE", "/api/combos/<id>")`,
   `("GET", "/api/settings")`, `("PATCH", "/api/settings")`, and a
   `/v1/models?kind=llm` payload containing real models plus a combo entry
   `{"id": "duo", "owned_by": "combo"}`. Cases:
   - adapter: `list_combos` normalization; `update_combo` sends only provided
     keys; `set_combo_strategy` performs GET-then-PATCH with the **whole
     merged map**; `combo_settings` returns only the three whitelisted keys.
   - `list_models` includes `{"id": "duo", "provider": "blend"}` after `auto`
     and before provider models; `_ensure_auto_combo` still selects only
     `/`-bearing real models (blend not swept in).
   - service: create happy path (combo POST then no settings write for
     default strategy; settings PATCH for round-robin/fusion); compensating
     delete when the settings write raises; name-collision guard vs blends,
     real model ids, and `auto` (409/403); charset rejection; fusion requires
     ≥2 models + judge; `judge_model` rejected without fusion; rename moves
     the strategy entry; delete clears it.
   - `auto` immutability: `update_blend`/`delete_blend` on the `auto` combo
     id → `ServiceError` 403 `blend_reserved`, and **no** 9router mutation
     request was recorded.
2. **Integration (ASGI)** — extend `brain4all/tests/test_fastapi.py` (it
   already has a `FakeRouter`, line 22): add combo/settings responses; assert
   `GET/POST/PATCH/DELETE /agent-gateway/v1/blends*` envelope shapes and
   status codes (incl. 201 create, 403 auto, 409 collision, 503 when the fake
   raises `NineRouterAPIError(status=503)`); assert
   `GET /agent-gateway/v1/blends/available-models` excludes blends and
   `auto`; assert `PATCH /agent-gateway/v1/agents-configs/{id}` with
   `{"model": "duo"}` succeeds and the profile `config.yaml` gets
   `model.default: duo` (proves the pass-through, findings.md §8).
3. **Strategy round-trip** — service-level test: create with
   `strategy: "round-robin", sticky_limit: 2` then `list_blends` reflects
   both; patch to `fusion` with a judge; patch back to `fallback` clears the
   entry.
4. **Frontend** — `cd src && npm run build` for type safety; add
   `src/src/api/blends.test.ts` mirroring `agents.test.ts` if the suite
   pattern applies (request paths + payload mapping).
5. Full gate: `make test`, then `make check` (see
   [validation.md](validation.md)).

## Documentation (with Phase 5)

- `docs/api.md`: the five Blends routes, DTO fields, error codes, and the
  "Brain4All stores nothing — 9router is authoritative" note.
- `docs/architecture.md`: one paragraph on the blend/combo naming line and
  the strategy read-modify-write caveat.
