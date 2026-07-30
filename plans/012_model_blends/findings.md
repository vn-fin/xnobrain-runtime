# 012 — Findings

Everything below was verified by direct inspection of this repository and of
the **installed, pinned 9router 0.5.40** at
`.tools/npm-global/lib/node_modules/9router/` (its `package.json` says
`"version": "0.5.40"`). Compiled Next.js chunks are minified, so identifier
names differ per build — the quoted fragments are evidence of *behavior*, and
every behavior we depend on at runtime is re-checked by a Phase-0 probe (§10).
Cross-links: [README.md](README.md), [architecture.md](architecture.md),
[approaches.md](approaches.md), [implementation.md](implementation.md).

## 1. The 9router pin

- `Dockerfile.backend` line 18: `ARG NINE_ROUTER_NPM_VERSION=0.5.40`, consumed
  at line 99: `"9router@${NINE_ROUTER_NPM_VERSION}"`.
- `scripts/install-linux.sh` line 261 installs `"9router@${nine_router_version}"`.
- Local dev copy: `.tools/npm-global/lib/node_modules/9router/`.

The plan targets exactly this version. 9router is 0.x — any version bump must
re-run the Phase-0 probes (§10) before shipping.

## 2. Combos are rows in 9router's own SQLite database

Schema definition (compiled chunk
`.tools/npm-global/lib/node_modules/9router/app/.next-cli-build/server/chunks/4055.js`,
identical copy in `chunks/5217.js`):

```
combos:{columns:{id:"TEXT PRIMARY KEY",name:"TEXT UNIQUE NOT NULL",kind:"TEXT",
models:"TEXT NOT NULL",createdAt:"TEXT NOT NULL",updatedAt:"TEXT NOT NULL"},
indexes:["CREATE INDEX IF NOT EXISTS idx_combo_name ON combos(name)"]}
```

`models` holds a JSON array of model ids. CRUD SQL (chunk `chunks/4884.js`):

```
FROM combos ORDER BY createdAt ASC
FROM combos WHERE id = ?
FROM combos WHERE name = ?
INTO combos(id, name, kind, models, createdAt, updatedAt) VALUES(?, ?, ?, ?, ?, ?)
UPDATE combos SET name = ?, kind = ?, models = ?, updatedAt = ? WHERE id = ?
DELETE FROM combos WHERE id = ?
```

So a combo has a stable `id` (TEXT PK) distinct from its unique `name`. The
`name` is what chat requests use; the `id` is what `PUT`/`DELETE` use.
Brain4All never touches this database directly — HTTP only.

## 3. The 9router combos HTTP API

Route manifest
(`.tools/npm-global/lib/node_modules/9router/app/.next-cli-build/server/app-paths-manifest.json`):

- `/api/combos/route` — GET (list), POST (create)
- `/api/combos/[id]/route` — GET, PUT, DELETE by id
- `/api/settings/route` — GET, PATCH
- `/api/brain/v1/models/route` — the OpenAI-compatible model list

Verified behavior of the compiled handlers:

**`GET /api/combos`** (`app/.next-cli-build/server/app/api/combos/route.js`)
returns `{"combos": [...]}`.

**`POST /api/combos`** — body `{name, models, kind}`; validation in the same
file:

- missing name → 400 `{"error":"Name is required"}`
- name charset regex `y=/^[a-zA-Z0-9_.\-]+$/` → 400
  `{"error":"Name can only contain letters, numbers, -, _ and ."}` — note
  **no `/` allowed**, which matters in §7.
- duplicate name → 400 `{"error":"Combo name already exists"}`
- success → 201 with the created combo object.

**`PUT /api/combos/{id}`**
(`app/.next-cli-build/server/app/api/combos/[id]/route.js`) accepts a
**partial** body `{name?, models?, kind?}`; a supplied `name` is re-validated
against the same regex and against uniqueness excluding itself
(`if(a&&a.id!==c) ... "Combo name already exists"`); unknown id → 404
`{"error":"Combo not found"}`. On rename it invalidates the resolution cache
for both the old and new names.

**`DELETE /api/combos/{id}`** → `{"success": true}`, 404 when missing.

This matches what `NineRouterManager._ensure_auto_combo` already sends
(`brain4all/integrations/nine_router.py` lines 336–382: `GET /api/combos`,
`POST /api/combos {"name","models"}`, `PUT /api/combos/{id} {"models"}`,
`DELETE /api/combos/{id}`), so `_request()` provably speaks this surface today.

## 4. A combo is a virtual model, resolved per request, with a strategy

Chat-path evidence (chunk `chunks/7807.js`; `d` is the request's `model`
string, `k` is the settings object):

```
let r=await (0,i.d_)(d);                       // combo lookup by name
if(r){let e=k.comboStrategies||{},
      f=e[d]?.fallbackStrategy||k.comboStrategy||"fallback";
  if("fusion"===f)return s.info("CHAT",
    `Combo "${d}" with ${r.length} models (strategy: fusion)`), ...
  let g=k.comboStickyRoundRobinLimit;
  return s.info("CHAT",
    `Combo "${d}" with ${r.length} models (strategy: ${f}, sticky: ${g})`), ...
```

Facts this establishes:

- **Resolution is by name at request time.** A request whose `model` equals a
  combo name fans into the combo's model list; nothing is precompiled.
- **Per-combo strategy** lives in settings under
  `comboStrategies[<name>].fallbackStrategy` (yes, the per-combo key is
  literally named `fallbackStrategy` even for non-fallback strategies), with
  the global `comboStrategy` as fallback, defaulting to `"fallback"`.
- **Strategy literals**: `"fallback"`, `"round-robin"`, `"fusion"`. The
  round-robin literal is verified in chunk `chunks/8910.js`, which rotates the
  model list only when the strategy is exactly `"round-robin"`
  (`if(!a||a.length<=1||"round-robin"!==c)return a;`) and shows the sticky
  limit defaulting to 1 (`comboStickyLimit:k=1`).
- **Sticky round-robin limit is GLOBAL**, not per-combo: it is read from
  `k.comboStickyRoundRobinLimit` at the settings top level. The UI and API
  must present it as an instance-wide setting (architecture.md).
- **Fusion** fans the request to every model in the combo and combines via
  `judgeModel` + `fusionTuning` from the per-combo settings entry:
  `judgeModel:e[d]?.judgeModel,tuning:e[d]?.fusionTuning`.
- **Fusion strips tools from child requests** (same chunk):

  ```
  handleSingleModel:(c,d,e)=>{let f=b;if(e&&b){
    let{tools:a,tool_choice:c,...d}=b.body||{};f={...b,body:d}}...}
  ```

  `tools` and `tool_choice` are destructured out of the child body. This is
  why the UI must warn that tools are disabled under fusion (README goal 4).
- The log lines `Combo "<name>" with N models (strategy: fusion)` and
  `Combo "<name>" with N models (strategy: <f>, sticky: <g>)` are the manual
  E2E evidence hooks used in [validation.md](validation.md).

The exact shape of `fusionTuning` is **not** verified — Phase-0 probe (§10).

## 5. Combos appear in `/v1/models` flagged `owned_by: "combo"`

Chunk `chunks/5612.js`, building the `/v1/models` response from combos:

```
let c={id:b.name,object:"model",owned_by:"combo"};
("webSearch"===b.kind||"webFetch"===b.kind)&&(c.kind=b.kind)
```

So a combo is listed with **`id` = the combo *name*** (not its TEXT PK) and
`owned_by: "combo"`. Combos of the default kind pass the `?kind=llm` filter
that `list_models()` already sends.

## 6. The 9router settings API: GET/PATCH, shallow top-level merge

- The 9router CLI's own client (readable source,
  `.tools/npm-global/lib/node_modules/9router/src/cli/api/client.js`
  ~lines 397–410) uses `GET /api/settings` and `PATCH /api/settings` with a
  plain JSON body.
- The PATCH handler
  (`app/.next-cli-build/server/app/api/settings/route.js`, function `F`)
  strips password fields and — key evidence — explicitly watches the combo
  keys and invalidates the combo cache when any of them changes:

  ```
  (Object.prototype.hasOwnProperty.call(b,"comboStrategy")||
   Object.prototype.hasOwnProperty.call(b,"comboStickyRoundRobinLimit")||
   Object.prototype.hasOwnProperty.call(b,"comboStrategies"))&&(0,y.UP)()
  ```

- Persistence (chunk `chunks/4884.js`, function `j`): settings are one JSON
  row (`settings(id=1, data)`); the update **reads the existing row and does a
  shallow top-level merge in a transaction**:

  ```
  b={...d?(0,e.q)(d.data,{}):{},...a},
  c.run("INSERT INTO settings(id, data) VALUES(1, ?)
         ON CONFLICT(id) DO UPDATE SET data = excluded.data",...)
  ```

Consequences:

- `PATCH /api/settings {"comboStrategies": {...}}` **is** a partial update of
  settings, but it **replaces the whole `comboStrategies` map**. Writing one
  blend's strategy therefore requires read-modify-write of that single key:
  `GET /api/settings` → merge the per-name entry → `PATCH` the merged map.
  There is a small lost-update window if two writers race; acceptable for a
  single-operator local tool, documented in architecture.md.
- `GET /api/settings` returns the whole instance settings (it strips
  `password`/`oidcClientSecret` server-side, but the payload still contains
  unrelated instance configuration such as proxy and OIDC fields). The
  Brain4All adapter must **whitelist** and expose only `comboStrategy`,
  `comboStrategies`, and `comboStickyRoundRobinLimit` — never the raw payload.

Because both handler functions are compiled, the GET/PATCH shapes are re-proved
live in Phase 0 (§10).

## 7. Brain4All today: one hidden combo, and a filter that hides all others

`brain4all/integrations/nine_router.py`:

- `NINE_ROUTER_DEFAULT_MODEL = "auto"` (line 23).
- `ensure_auto_combo()` (line 327) / `_ensure_auto_combo(models)` (lines
  336–382): builds the `auto` combo from up to 12 real models, **one per
  provider owner**, considering only ids that contain `/`
  (`if not model_id or model_id == NINE_ROUTER_DEFAULT_MODEL or "/" not in model_id: continue`).
  It creates/updates/deletes `auto` via `/api/combos` as listed in §3. It is
  called after provider connect/disconnect/OAuth success and from
  `list_models(ensure_auto=True)`.
- **The key gap** — `list_models()` (lines 217–272) fetches
  `/v1/models?kind=llm` and keeps only entries whose `owned_by` is an *active
  provider alias* (`cc`, `cx`, `ag`, `openai`, `anthropic`, `gemini` — the
  values of `ROUTER_MODEL_ALIASES` for connected providers):

  ```python
  owner = str(item.get("owned_by") or self._model_owner(model_id)).strip()
  if owner not in active_owners:
      continue
  ```

  A user combo arrives with `owned_by: "combo"` (§5), which is never in
  `active_owners`, so **every custom combo is silently filtered out today**.
  (`auto` itself is skipped earlier by the `model_id == NINE_ROUTER_DEFAULT_MODEL`
  check and re-added as the hardcoded first entry `{"id": "auto",
  "provider": "nine-router", "name": "Auto"}`.) Extending this filter is the
  core adapter change (architecture.md).
- Two structural safety facts for the extension:
  - The combo name charset (§3) forbids `/`, and `_ensure_auto_combo` skips
    ids without `/` — so blends can never be recursively swept into the
    `auto` combo even after they appear in `list_models()` output.
  - `_safe_id` (line 515, regex `^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$`) is what
    combo ids pass through before URL interpolation; Brain4All's blend-name
    rule (architecture.md) is chosen as a subset of both this and the 9router
    regex so names and ids always validate on both sides.
- `_request()` (lines 384–421) already restricts paths to `/api/` and `/v1/`,
  signs with the `x-9r-cli-token`, and maps connection failures to
  `NineRouterAPIError("9Router is unavailable", code="nine_router_unavailable",
  status=503)`. `NineRouterAPIError` is in `EXPECTED_ERRORS`
  (`brain4all/services/platform.py` line 810), so handlers already translate a
  down 9router into the standard failure envelope with status 503.
- Downstream consumers that must stay unpolluted by blend entries, and are —
  because they filter by provider id:
  - `PlatformService.providers()` (`brain4all/services/platform.py` lines
    592–611): `available_models` keeps `item["provider"] == provider` for the
    six real providers only.
  - `APIHandlers._provider_models` (`brain4all/handlers/api.py` lines
    192–194): same per-provider filter, plus a hardcoded `auto` first entry.
  - `NineRouterManager.usage(model)` (line 274): `_provider_for_model` returns
    `""` for a blend name (no `/` prefix match), yielding the existing
    "empty usage" response — harmless.

## 8. A blend name flows into an agent with zero Hermes changes

`brain4all/integrations/hermes.py`:

- `update_config()` (lines 354–439) handles
  `PATCH /api/brain/v1/agents-configs/{agent_id}` (route →
  `config_agent_patch` → `PlatformService.update_agent_config`, which
  snapshots `config.yaml` first — `brain4all/services/platform.py` lines
  190–198). The `model` field is validated only by `_nonempty_string`
  (line 389; definition line 1845 — any non-empty string passes) and written
  via `self._set_nested(config, ("model", "default"), model)` (line 390),
  then `normalize_nine_router_config(config, model)` (line 431) pins the
  provider to 9router and mirrors the model into the provider block. **No
  model-list validation exists, so a blend name is accepted as-is.**
- The `provider` field, if present, must be one of
  `{"9router", "nine-router", "auto", "custom:nine-router"}` (line 383). The
  frontend already normalizes this: `routedConfig()` in `src/api/agents.ts`
  rewrites any provider to `nine-router` before PATCHing, so the picker can
  pass a "blend" pseudo-provider without backend changes.
- Per-conversation override: `ChatRequest.model`
  (`brain4all/models/api.py`) reaches `_prepare_chat_command` which appends
  `--model <string>` to the Hermes CLI invocation (line 614) — a blend name
  works there too.
- `_conversation_model()` (line 1410) resolves the session's model from the
  request body or `config.yaml` `model.default` and it is written into the
  session record — the basis of §9.

## 9. Analytics shows blend names automatically (plan-009 tie-in)

Plan 009 (implemented) aggregates the pre-summed `sessions` table per profile;
`sessions.model` records the **requested** model string (today `"auto"` for
auto-routed agents; see `plans/009_usage_analytics/findings.md` §1 for the
column list and `brain4all/integrations/hermes.py` `_conversation_model` /
`_create_session`, lines 1410–1440). `brain4all/services/analytics.py` groups
by exactly that string (`by_model` accumulation around lines 223–249). So when
an agent's model is a blend name, the analytics by-model table shows the blend
name as the session's model — with no code change. The *actual* underlying
provider model chosen by 9router per request is not visible to Hermes and
therefore not attributable locally; the UI copy should say usage is attributed
to the blend, not to its member models. Verified by reading both files; proven
live in [validation.md](validation.md).

## 10. Phase-0 probe list (live 9router, skip-if-down)

> **PROBED 2026-07-25 against 9router v0.5.40 (live). All confirmed. Refinements:**
> - `GET /api/combos` → `{"combos": [{id, name, kind, models, createdAt, updatedAt}]}`. ✅
> - **`POST /api/combos` returns the combo object DIRECTLY** (top-level `id`,
>   not wrapped in `{"combo": ...}`) — the adapter normalizer must read the
>   top level (fallback to a `combo` key if a future version wraps it). ✅
> - Created combo appears in `/v1/models?kind=llm` as
>   `{"id": <name>, "object": "model", "owned_by": "combo"}`. ✅
> - `PUT /api/combos/{id}` partial (models-only) keeps the name. ✅
> - Name charset: `"bad name!"` → **400**; `"ok-name_1.x"` accepted. ✅
> - `GET /api/settings` has **no `password` key**; combo keys present:
>   `comboStrategy`, `comboStrategies`, `comboStickyRoundRobinLimit`. ✅
> - `PATCH /api/settings {"comboStrategies": {...}}` **replaces the whole map**
>   (writing map B removed map A's entry) → read-modify-write is required. ✅
> - Fusion entry `{"fallbackStrategy": "fusion", "judgeModel": <id>}` round-trips. ✅
>
> The original probe list is retained for the compatibility test to re-run on
> any 9router bump.

Every fact sourced from compiled chunks (§3–§6) is re-proved against the
running pinned 9router at `http://127.0.0.1:20128` before any dependent code
is written. Probes (test file named in
[implementation.md](implementation.md) Phase 0):

1. **Combos list shape**: `GET /api/combos` → `{"combos": [...]}`; each row
   has `id`, `name`, `models` (JSON array), `kind`, `createdAt`, `updatedAt`.
2. **Create**: `POST /api/combos {"name": "b4a-probe", "models": [<two real
   ids>]}` → 201 with `id`; the combo then appears in `GET /api/combos`.
3. **Name charset**: `POST` with `"bad name!"` → 400; with `"ok-name_1.x"` →
   201. Record the observed accepted/rejected set against
   `/^[a-zA-Z0-9_.\-]+$/`.
4. **Duplicate name**: second `POST` with the same name → 400 ("already
   exists" — status code recorded; upstream uses 400, Brain4All maps its own
   pre-check to 409, see architecture.md).
5. **Partial PUT body**: `PUT /api/combos/{id}` with only `{"models": [...]}`
   → 200 and name unchanged; with only `{"name": "renamed"}` → 200 and models
   unchanged.
6. **Delete**: `DELETE /api/combos/{id}` → `{"success": true}`; unknown id →
   404.
7. **Virtual-model listing**: after create, `GET /v1/models?kind=llm` contains
   `{"id": "<combo name>", "owned_by": "combo"}`.
8. **Settings GET shape**: `GET /api/settings` → object; note whether
   `comboStrategy`, `comboStrategies`, `comboStickyRoundRobinLimit` are
   present/absent when unset; assert no `password` key.
9. **Settings PATCH semantics**: `PATCH /api/settings
   {"comboStrategies": {"b4a-probe": {"fallbackStrategy": "round-robin"}}}`
   then GET: entry present; then PATCH with a *different* map and confirm the
   **whole `comboStrategies` key was replaced** (shallow top-level merge,
   §6) — this justifies the read-modify-write in the adapter.
10. **Fusion settings entry**: PATCH
    `{"comboStrategies": {"b4a-probe": {"fallbackStrategy": "fusion",
    "judgeModel": "<real id>"}}}` round-trips; record what `fusionTuning`
    looks like if the 9router dashboard sets one (shape currently unverified —
    until probed, Brain4All treats it as an opaque optional object it never
    fabricates).
11. **Sticky limit**: PATCH `{"comboStickyRoundRobinLimit": 3}` round-trips at
    the settings top level (global — §4).
12. **Cleanup**: probes delete every combo/settings entry they create and
    restore prior `comboStrategies` content.

Optional smoke (manual, not CI): a chat completion with `model` set to the
probe combo name returns from one of its member models and logs the
`Combo "..." (strategy: ...)` line.

## 11. Risks

- **0.x upstream drift.** 9router is pre-1.0; combos routes, settings keys,
  and strategy literals can change between patch versions. Mitigation: the
  version pin (§1) plus the Phase-0 probe suite, which must be re-run on any
  pin bump. All chunk-derived facts are treated as unverified until the probe
  passes.
- **Strategy settings shape partially unverified.** `fusionTuning`'s schema
  and any additional per-combo settings keys are unknown (§10 item 10). The
  API treats them as pass-through opaque data or omits them (approaches.md
  Decision E).
- **Fusion cost.** Fusion multiplies spend by the blend size on *every*
  request, plus the judge call, and silently disables tools (§4). Mitigated by
  explicit UI copy and by exposing fusion last in the strategy choices.
- **Settings read-modify-write race.** Concurrent strategy writes (Brain4All
  and the 9router dashboard open at once) can lose one update (§6).
  Single-operator local deployment makes this acceptable; documented.
- **Name shadowing.** A combo name that equals a real model id would shadow
  it at request time. Brain4All's service rejects such names on create/rename
  (architecture.md guard rules); names created directly in the 9router
  dashboard bypass this — listed as-is, not repaired.

## 12. Open questions

- Can a combo's `models` array reference another combo name (nested blends)?
  Not relied upon and not exposed: `GET /blends/available-models` only offers
  real provider models. A Phase-0 curiosity probe may record the upstream
  behavior, but no Brain4All behavior depends on the answer.
- Does 9router cap `models` length? Not observed in the handlers; Brain4All
  imposes its own cap of 24 (architecture.md) so the UI stays sane either way.
- Should the global `comboStrategy` default ever be surfaced? Deferred — this
  plan only reads it as the fallback when hydrating per-blend strategies, and
  writes only `comboStrategies` entries and `comboStickyRoundRobinLimit`.
