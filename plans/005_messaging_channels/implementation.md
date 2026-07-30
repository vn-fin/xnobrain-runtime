# 005 — Implementation

Ordered, phased, file-by-file. Execute phases in order; each ends in a testable
state. Read `architecture.md` for the contract and `approaches.md` for the
chosen mechanisms. Exact new route paths and model names are fixed here so the
handler `operations` map and the frontend client agree.

Real paths (all under repo root `/home/kim/Documents/xno/brain4all-dev/brain4all/`):

- Routes: `brain4all/routes/setup.py`
- Handlers: `brain4all/handlers/api.py`
- Service (new): `brain4all/services/channels.py`
- Integration (new): `brain4all/integrations/gateway.py`
- Models: `brain4all/models/api.py` (+ export in `brain4all/models/__init__.py`)
- Tests (new): `brain4all/tests/test_channels.py`
- Frontend: `src/src/api/channels.ts`, `src/src/hooks/useChannels.ts`,
  `src/src/components/ChannelsView.tsx`, `src/src/App.tsx`
- Pinned Hermes (reference only): `.tools/hermes-agent/`

---

## Phase 0 — Pin & prove upstream contracts

1. **Pin Hermes** to an immutable release/commit (replace any moving
   `HERMES_BRANCH=main`), recorded in `docs/development.md` +
   `docs/architecture.md`, exactly as the Kanban plan requires
   (`plans/001_kanban_foundation/README.md` Phase 0). Re-verify the
   `web_server.py` line numbers cited in `findings.md` against this commit and
   correct them in-code if they drifted.
2. **Compatibility module/test** `brain4all/tests/test_channels_compat.py` (or a
   `compat` marker in `test_channels.py`) asserting, against the pinned artifact
   in a temp `HERMES_HOME` (mirror `test_kanban.py`):
   - `from gateway.config import Platform, PORT_BINDING_PLATFORM_VALUES,
     platform_binds_port, load_gateway_config, PlatformConfig` succeeds; enum
     contains `telegram, discord, slack, signal, whatsapp`;
     `load_gateway_config().multiplex_profiles` is a bool;
     `GatewayConfig._is_platform_connected` is callable.
   - `from hermes_cli.config import save_env_value, remove_env_value, load_env,
     redact_key, write_platform_config_field, load_config, OPTIONAL_ENV_VARS`
     succeeds.
   - `from gateway.pairing import PairingStore`; instance exposes
     `list_pending, list_approved, approve_code, revoke, clear_pending`.
   - `from gateway.status import read_runtime_status, get_running_pid`.
   - The native app route table contains the paths in `findings.md` §1a–1d
     (build the app / inspect `app.router.routes`, or hit them with a
     `TestClient` for 401/422 rather than 404).
3. **Confirm the public gateway-lifecycle path** (`approaches.md` §3): verify a
   public way to start/stop/restart a profile's gateway without the private
   `web_server._spawn_hermes_action` — either the `hermes gateway …` CLI
   subcommand or an importable `hermes_cli.gateway` entrypoint. If none exists,
   file an upstream task to expose a small public hook; **do not copy** the spawn
   logic. Record the decision in this file.
4. **Startup readiness:** add a single readiness check (alongside the Kanban
   one) that fails with **one** remediation line if the channel compat contract
   is unmet. Do not limp along with partial channel mutations.

Exit: `brain4all/tests/test_channels_compat.py` passes against the pinned image.

---

## Phase 1 — Models

Add to `brain4all/models/api.py` (strict validation; mirror existing style) and
export each from `brain4all/models/__init__.py` (the `from ..models import (…)`
block in `routes/setup.py` must resolve them):

```python
class ChannelUpdate(BaseModel):
    enabled: bool | None = None
    env: dict[str, str] = Field(default_factory=dict)
    clear_env: list[str] = Field(default_factory=list, max_length=64)

class ChannelCredentialSet(BaseModel):
    env: dict[str, str] = Field(default_factory=dict)
    clear_env: list[str] = Field(default_factory=list, max_length=64)
    reason: str | None = Field(default=None, max_length=200)   # audit label, e.g. "rotate"

class GatewayDrain(BaseModel):
    action: Literal["drain", "cancel"] = "drain"

class PairingApprove(BaseModel):
    platform: str = Field(min_length=1, max_length=64)
    code: str = Field(min_length=1, max_length=64)

class PairingRevoke(BaseModel):
    platform: str = Field(min_length=1, max_length=64)
    user_id: str = Field(min_length=1, max_length=256)

class TelegramOnboardStart(BaseModel):
    bot_name: str | None = Field(default=None, max_length=128)

class TelegramOnboardApply(BaseModel):
    allowed_user_ids: list[str] = Field(min_length=1, max_length=256)

class WhatsAppOnboardStart(BaseModel):
    mode: Literal["bot", "self-chat"] = "bot"
    allowed_users: str | None = Field(default=None, max_length=4000)

class WhatsAppOnboardApply(BaseModel):
    mode: Literal["bot", "self-chat"] | None = None
    allowed_users: str | None = Field(default=None, max_length=4000)
```

Validation rules: env keys/values are size-bounded; the **service** enforces the
per-platform key allow-list (not the model, since it is platform-dependent).
Never add a field that echoes a raw stored token back to the client.

---

## Phase 2 — Integration adapter (`brain4all/integrations/gateway.py`)

New file. Mirror `integrations/kanban.py`: lazy import, `GatewayUnavailable`,
profile-scoped calls, no secret leakage outward. Methods (signatures in
`architecture.md` §4):

1. `_config_mod()` / `_gateway_config_mod()` / `_pairing_store()` /
   `_status_mod()` — lazy imports of `hermes_cli.config`, `gateway.config`,
   `gateway.pairing`, `gateway.status`; raise `GatewayUnavailable` if absent.
2. Profile scope helper: a context manager that resolves the agent's profile
   dir and enters Hermes' profile scope (set the profile's `HERMES_HOME` the way
   `_profile_scope` does) so every read/write hits the right `.env`/`config.yaml`.
   Reuse `integrations/hermes.py` profile resolution for the dir.
3. **Catalog/status reads:** `list_channels(profile)` builds entries from
   `gateway.config.Platform.__members__` (skip `local`) + `platform_registry.
   plugin_entries()`; for each, derive `enabled`/`configured`/`state`/
   `home_channel` from `load_gateway_config()` + `read_runtime_status()`; build
   `env_vars[]` with `is_set` + `redact_key(value)` **only**. `port_binds()`
   wraps `platform_binds_port`. `multiplex_enabled()` reads the **default**
   profile's `load_gateway_config().multiplex_profiles`.
4. **Config writes:** `snapshot_profile_config(profile)` (snapshot `.env` +
   `config.yaml`, reuse `integrations/config.py` snapshot logic);
   `set_env`/`clear_env` (`save_env_value`/`remove_env_value`);
   `set_channel_enabled` (`write_platform_config_field`);
   `ensure_multiplex_default()` (set `gateway.multiplex_profiles=true` on the
   default profile when needed).
5. **Lifecycle:** `gateway_status/start/stop/restart/drain` via the public path
   chosen in Phase 0 §3 (CLI subcommand spawn or native endpoint;
   drain via `gateway.drain_control.write_drain_request`/`clear_drain_request`).
6. **Pairing:** delegate to `PairingStore`.
7. **Onboarding:** `telegram_onboard_*` / `whatsapp_onboard_*` — ride the native
   `/api/messaging/*/onboarding/*` flow (external setup service); pass the
   agent's profile.

---

## Phase 3 — Service (`brain4all/services/channels.py`)

New file. Owns all policy (`architecture.md` §5):

1. `__init__(self, hermes, gateway)` — takes the existing Hermes integration
   (for `agent_id → profile` resolution + 404) and the new `gateway` adapter.
2. Methods, one per operation: `list_channels`, `get_channel`, `update_channel`,
   `set_credentials`, `rotate_credentials`, `test_channel`, `gateway_status`,
   `gateway_start/stop/restart/drain`, `list_pairings`, `approve_pairing`,
   `revoke_pairing`, `clear_pairings`, and the telegram/whatsapp onboarding
   passthroughs.
3. Enforce, in this order, on every mutating channel call:
   a. resolve+validate agent → profile (404),
   b. if enabling and `port_binds(id)` and `multiplex_enabled()` and
      profile≠default → **409** with Hermes' guidance,
   c. `snapshot_profile_config(profile)`,
   d. validate env keys against `channel_env_keys(id)` (400),
   e. apply writes,
   f. read back + **redact** before returning.
4. Register `EXPECTED_ERRORS` so `handlers/api.py::dispatch` maps them to the
   envelope (extend `brain4all/services/__init__.py` exports).
5. Wire the service into the app's service composition wherever `PlatformService`
   / Kanban service are constructed (follow `services/__init__.py` +
   the app factory that builds `APIHandlers`). The handler must reach it (e.g.
   `service.channels` or a sibling passed to `APIHandlers`).

---

## Phase 4 — Handlers (`brain4all/handlers/api.py`)

Add operations to the `operations` dict in `_operation` (l.48), keyed by the
`route.name` values below. Pattern matches existing entries (`p`=path_params,
`q`=query, `s`=service). Examples:

```python
"channels_list":   (lambda: ch.list_channels(p["agent_id"]), "channels retrieved successfully", 200),
"channel_get":     (lambda: ch.get_channel(p["agent_id"], p["platform_id"]), "channel retrieved successfully", 200),
"channel_update":  (lambda: ch.update_channel(p["agent_id"], p["platform_id"], body), "channel updated successfully", 200),
"channel_credentials": (lambda: ch.set_credentials(p["agent_id"], p["platform_id"], body), "credentials saved successfully", 200),
"channel_rotate":  (lambda: ch.rotate_credentials(p["agent_id"], p["platform_id"], body), "credentials rotated successfully", 200),
"channel_test":    (lambda: ch.test_channel(p["agent_id"], p["platform_id"]), "channel tested", 200),
"gateway_status":  (lambda: ch.gateway_status(p["agent_id"]), "gateway status retrieved", 200),
"gateway_start":   (lambda: ch.gateway_start(p["agent_id"]), "gateway starting", 202),
"gateway_stop":    (lambda: ch.gateway_stop(p["agent_id"]), "gateway stopping", 202),
"gateway_restart": (lambda: ch.gateway_restart(p["agent_id"]), "gateway restarting", 202),
"gateway_drain":   (lambda: ch.gateway_drain(p["agent_id"], body), "gateway drain updated", 200),
"pairings_list":   (lambda: ch.list_pairings(p["agent_id"]), "pairings retrieved", 200),
"pairing_approve": (lambda: ch.approve_pairing(p["agent_id"], body), "pairing approved", 200),
"pairing_revoke":  (lambda: ch.revoke_pairing(p["agent_id"], body), "pairing revoked", 200),
"pairing_clear":   (lambda: ch.clear_pairings(p["agent_id"]), "pending pairings cleared", 200),
"telegram_onboard_start":  (lambda: ch.telegram_onboard_start(p["agent_id"], body), "telegram onboarding started", 201),
"telegram_onboard_status": (lambda: ch.telegram_onboard_status(p["agent_id"], p["pairing_id"]), "telegram onboarding status", 200),
"telegram_onboard_apply":  (lambda: ch.telegram_onboard_apply(p["agent_id"], p["pairing_id"], body), "telegram onboarding applied", 200),
"telegram_onboard_cancel": (lambda: ch.telegram_onboard_cancel(p["agent_id"], p["pairing_id"]), "telegram onboarding cancelled", 200),
"whatsapp_onboard_start":  (lambda: ch.whatsapp_onboard_start(p["agent_id"], body), "whatsapp onboarding started", 201),
"whatsapp_onboard_status": (lambda: ch.whatsapp_onboard_status(p["agent_id"], p["pairing_id"]), "whatsapp onboarding status", 200),
"whatsapp_onboard_apply":  (lambda: ch.whatsapp_onboard_apply(p["agent_id"], p["pairing_id"], body), "whatsapp onboarding applied", 200),
"whatsapp_onboard_cancel": (lambda: ch.whatsapp_onboard_cancel(p["agent_id"], p["pairing_id"]), "whatsapp onboarding cancelled", 200),
```

where `ch` is the channels service handle. Bind `ch` in `APIHandlers.__init__`
(add a `channels` param, or reach it off the composed service). Errors raised by
the service are caught by the existing `dispatch` try/except and rendered by
`failure(...)`; ensure the service's exception types are in `EXPECTED_ERRORS`.

---

## Phase 5 — Routes (`brain4all/routes/setup.py`)

Add to the `from ..models import (…)` block:
`ChannelUpdate, ChannelCredentialSet, GatewayDrain, PairingApprove,
PairingRevoke, TelegramOnboardStart, TelegramOnboardApply, WhatsAppOnboardStart,
WhatsAppOnboardApply`.

Append these `Route(...)` rows to `ROUTES` (before the trailing `)`), tag
`("Channels",)`:

```python
    Route("GET", "/api/brain/v1/agents/{agent_id}/channels", "channels_list", tags=("Channels",)),
    Route("GET", "/api/brain/v1/agents/{agent_id}/channels/{platform_id}", "channel_get", tags=("Channels",)),
    Route("PUT", "/api/brain/v1/agents/{agent_id}/channels/{platform_id}", "channel_update", ChannelUpdate, tags=("Channels",)),
    Route("POST", "/api/brain/v1/agents/{agent_id}/channels/{platform_id}/credentials", "channel_credentials", ChannelCredentialSet, tags=("Channels",)),
    Route("POST", "/api/brain/v1/agents/{agent_id}/channels/{platform_id}/rotate", "channel_rotate", ChannelCredentialSet, tags=("Channels",)),
    Route("POST", "/api/brain/v1/agents/{agent_id}/channels/{platform_id}/test", "channel_test", tags=("Channels",)),

    Route("GET", "/api/brain/v1/agents/{agent_id}/gateway/status", "gateway_status", tags=("Channels",)),
    Route("POST", "/api/brain/v1/agents/{agent_id}/gateway/start", "gateway_start", tags=("Channels",)),
    Route("POST", "/api/brain/v1/agents/{agent_id}/gateway/stop", "gateway_stop", tags=("Channels",)),
    Route("POST", "/api/brain/v1/agents/{agent_id}/gateway/restart", "gateway_restart", tags=("Channels",)),
    Route("POST", "/api/brain/v1/agents/{agent_id}/gateway/drain", "gateway_drain", GatewayDrain, tags=("Channels",)),

    Route("GET", "/api/brain/v1/agents/{agent_id}/pairings", "pairings_list", tags=("Channels",)),
    Route("POST", "/api/brain/v1/agents/{agent_id}/pairings/approve", "pairing_approve", PairingApprove, tags=("Channels",)),
    Route("POST", "/api/brain/v1/agents/{agent_id}/pairings/revoke", "pairing_revoke", PairingRevoke, tags=("Channels",)),
    Route("POST", "/api/brain/v1/agents/{agent_id}/pairings/clear", "pairing_clear", tags=("Channels",)),

    Route("POST", "/api/brain/v1/agents/{agent_id}/channels/telegram/onboarding", "telegram_onboard_start", TelegramOnboardStart, tags=("Channels",)),
    Route("GET", "/api/brain/v1/agents/{agent_id}/channels/telegram/onboarding/{pairing_id}", "telegram_onboard_status", tags=("Channels",)),
    Route("POST", "/api/brain/v1/agents/{agent_id}/channels/telegram/onboarding/{pairing_id}/apply", "telegram_onboard_apply", TelegramOnboardApply, tags=("Channels",)),
    Route("DELETE", "/api/brain/v1/agents/{agent_id}/channels/telegram/onboarding/{pairing_id}", "telegram_onboard_cancel", tags=("Channels",)),

    Route("POST", "/api/brain/v1/agents/{agent_id}/channels/whatsapp/onboarding", "whatsapp_onboard_start", WhatsAppOnboardStart, tags=("Channels",)),
    Route("GET", "/api/brain/v1/agents/{agent_id}/channels/whatsapp/onboarding/{pairing_id}", "whatsapp_onboard_status", tags=("Channels",)),
    Route("POST", "/api/brain/v1/agents/{agent_id}/channels/whatsapp/onboarding/{pairing_id}/apply", "whatsapp_onboard_apply", WhatsAppOnboardApply, tags=("Channels",)),
    Route("DELETE", "/api/brain/v1/agents/{agent_id}/channels/whatsapp/onboarding/{pairing_id}", "whatsapp_onboard_cancel", tags=("Channels",)),
```

Notes:
- The generic `Route` machinery already handles body vs no-body and wraps
  responses in `APIEnvelope`; no `special` handler is needed for any of these.
- Path ordering: these are concrete prefixes and sit ahead of Hermes' SPA
  catch-all thanks to the existing re-order at the bottom of `setup_routes`
  (l.207). No change needed there.
- `{agent_id}` scoping means `/channels/telegram/onboarding` never collides with
  `/channels/{platform_id}` because the literal `telegram/onboarding` sub-path is
  longer/more specific; if Starlette ordering causes ambiguity, register the
  onboarding routes **before** the `{platform_id}` routes in `ROUTES`.

---

## Phase 6 — Frontend (`src/src`)

1. `api/channels.ts` — typed functions over the Phase 5 routes, using
   `request`/`requestRaw` from `api/client.ts` (see `api/kanban.ts`). Types:
   `Channel`, `ChannelEnvVar` (`{key, required, is_set, redacted_value}` — no
   raw value type), `GatewayStatus`, `Pairing`. Add to `src/src/types.ts`.
2. `hooks/useChannels.ts` — per-agent state; load channels + gateway status;
   mutations (enable/disable, save/rotate credentials, test, start/stop/restart/
   drain, approve/revoke pairing, onboarding). Follow `hooks/useKanban.ts` /
   `hooks/useConnections.ts` (loading/empty/error/offline states).
3. `components/ChannelsView.tsx` — the Channels page (`architecture.md` §6):
   channel cards with state badge + enable toggle + masked credential fields +
   Test; guided Telegram/WhatsApp onboarding modal (QR + deep link); a gateway
   control strip; a pairing panel. Add a `ChannelsView.test.tsx` mirroring
   `KanbanView.test.tsx`.
4. `App.tsx` — add a `centerView === 'channels'` branch next to the existing
   `kanban`/`teams` branches (l.252), and a nav entry (or a Settings section per
   the 002 navigation model in `plans/CHECKLIST.md`). Keep provider
   "Connections" separate.
5. Translate all new strings in every shipped locale (`src/src/locales`).

---

## Phase 7 — Tests

`brain4all/tests/test_channels.py` (real pinned Hermes, temp `HERMES_HOME`,
mirror `test_kanban.py`) and handler tests in `test_fastapi.py`. Full matrix in
`validation.md` §2–3. At minimum: list/enable/disable/test round-trip; multiplex
409; snapshot-before-write; redaction in every response; pairing approve/revoke;
gateway status; onboarding happy path (external service mocked only at the
network boundary).

---

## Order-of-work summary

Phase 0 (pin+compat) → 1 (models) → 2 (integration) → 3 (service) → 4 (handlers)
→ 5 (routes) → 6 (frontend) → 7 (tests) → harden per `validation.md`. Ship the
read path (Phase 1–2 reads + list route) as the first verifiable slice, then
enable/credentials, then lifecycle+pairing, then onboarding+UI — vertical slices
per the Kanban discipline (`plans/001_kanban_foundation/README.md`).
</content>
