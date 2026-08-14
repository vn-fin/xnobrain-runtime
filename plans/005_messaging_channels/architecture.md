# 005 — Architecture

How messaging channels fit XNOBrain's layering. Read `findings.md` first for
the Hermes surface this rides, and `approaches.md` for why the adapter is
in-process Python (not loopback HTTP). `implementation.md` turns this into
file-by-file steps.

## 1. Layering fit

XNOBrain boundaries (`AGENTS.md`, `plans/001_kanban_foundation/README.md`):

```
React "Channels" page (src/components/ChannelsView.tsx, hooks/useChannels.ts, api/channels.ts)
        │  HTTP  /api/brain/v1/agents/{agent_id}/channels...  (APIEnvelope)
        ▼
routes/setup.py            ← ONLY route-assembly point; adds Route(...) rows
        ▼
handlers/api.py            ← HTTP translation; maps route.name → service call, success/failure envelope
        ▼
services/channels.py       ← rules: agent→profile resolution, five-state projection,
                             multiplex guard, snapshot-before-write, redaction preservation
        ▼
integrations/gateway.py    ← thin adapter over Hermes public APIs (gateway.config,
                             hermes_cli.config, gateway.pairing, gateway.status,
                             gateway lifecycle) + native onboarding endpoints
        ▼
Hermes (same process)      ← profile .env + config.yaml (platforms.*), gateway process,
                             PairingStore, gateway_state.json  ← authoritative state
```

Local layers call each other **directly** (no internal HTTP). The service owns
policy; the integration owns adaptation; neither leaks Hermes internals to
React. Redaction happens before data leaves the service.

## 2. Data flow & where state lives

**State is Hermes-owned. XNOBrain owns no channel state.**

| State | Lives in | Written via | Read via |
|---|---|---|---|
| Channel enabled flag | profile `config.yaml` → `platforms.<id>.enabled` | `hermes_cli.config.write_platform_config_field` | `gateway.config.load_gateway_config().platforms` |
| Channel credentials (bot tokens, allowed users) | profile `.env` | `hermes_cli.config.save_env_value` / `remove_env_value` | `load_env()` (values) → returned **redacted only** |
| Home channel binding | profile `config.yaml` → `platforms.<id>.home_channel` | native onboarding / config write | `PlatformConfig.home_channel` |
| Live connection status | `gateway_state.json` (per profile) | the running gateway | `gateway.status.read_runtime_status()` |
| Gateway process | OS process on `:8642` | `hermes gateway {start,stop,restart}` | `get_running_pid()` |
| Pairing pending/approved | `gateway/pairing.py` store files | `PairingStore` | `PairingStore.list_*` |

XNOBrain's **only** persistence responsibility is the mandated
snapshot-before-mutation: before any write to a profile's `.env` or
`config.yaml`, snapshot both (reuse the same snapshot mechanism as
`integrations/config.py` / `AGENTS.md` §Persistence). No new XNOBrain file
format, no channel cache.

`agent_id` → `profile`: an agent **is** a profile
(`integrations/hermes.py::_require_profile`). The service resolves and validates
the agent, then passes the profile name to every Hermes call (as the `profile`
arg / `_profile_scope`). A missing/invalid agent → 404 before any Hermes call.

## 3. New XNOBrain API contract

Base: `/api/brain/v1` (matches the existing agent-scoped surface in
`routes/setup.py`). All responses use the standard `APIEnvelope`
(`{success, data, message, status_code}`). All paths are per-agent so channel
config is unambiguously scoped to one profile.

### 3a. Channel catalog & credentials

| Method | Path | Operation (route.name) | Body model | Returns |
|---|---|---|---|---|
| GET | `/agents/{agent_id}/channels` | `channels_list` | — | catalog: per-channel `{id,name,description,docs_url,enabled,configured,connected_state,port_binding,multiplex_blocked,error_message,home_channel,env_vars[]}` (env values **redacted**) |
| GET | `/agents/{agent_id}/channels/{platform_id}` | `channel_get` | — | one channel detail (same shape) |
| PUT | `/agents/{agent_id}/channels/{platform_id}` | `channel_update` | `ChannelUpdate` | `{platform_id, enabled, configured, state}` — never token values |
| POST | `/agents/{agent_id}/channels/{platform_id}/credentials` | `channel_credentials` | `ChannelCredentialSet` | `{platform_id, configured, env_vars[](redacted)}` |
| POST | `/agents/{agent_id}/channels/{platform_id}/rotate` | `channel_rotate` | `ChannelCredentialSet` | same as credentials |
| POST | `/agents/{agent_id}/channels/{platform_id}/test` | `channel_test` | — | `{ok, state, message}` |

`ChannelUpdate` = enable/disable + optional env set/clear in one call (mirrors
Hermes `MessagingPlatformUpdate`). `ChannelCredentialSet` is the credential-only
path; `rotate` is `credentials` semantics with an explicit intent label for
audit. All three run the **multiplex port-binding guard** before writing.

### 3b. Gateway lifecycle & status

| Method | Path | Operation | Body model | Returns |
|---|---|---|---|---|
| GET | `/agents/{agent_id}/gateway/status` | `gateway_status` | — | `{running, pid, mode(single|multiplex|per-agent), drainable, busy, updated_at, degraded_reason?}` |
| POST | `/agents/{agent_id}/gateway/start` | `gateway_start` | — | `{ok, pid}` |
| POST | `/agents/{agent_id}/gateway/stop` | `gateway_stop` | — | `{ok, pid}` |
| POST | `/agents/{agent_id}/gateway/restart` | `gateway_restart` | — | `{ok, pid}` |
| POST | `/agents/{agent_id}/gateway/drain` | `gateway_drain` | `GatewayDrain` | `{ok, action}` |

`GatewayDrain` = `{action: "drain"|"cancel"}`.

### 3c. Pairing / linking

| Method | Path | Operation | Body model | Returns |
|---|---|---|---|---|
| GET | `/agents/{agent_id}/pairings` | `pairings_list` | — | `{pending:[…], approved:[…]}` (codes surfaced to UI only) |
| POST | `/agents/{agent_id}/pairings/approve` | `pairing_approve` | `PairingApprove` | `{ok, user}` |
| POST | `/agents/{agent_id}/pairings/revoke` | `pairing_revoke` | `PairingRevoke` | `{ok}` |
| POST | `/agents/{agent_id}/pairings/clear` | `pairing_clear` | — | `{ok, cleared}` |

`PairingApprove` = `{platform, code}`; `PairingRevoke` = `{platform, user_id}`.

### 3d. Guided onboarding (Telegram / WhatsApp)

| Method | Path | Operation | Body model | Returns |
|---|---|---|---|---|
| POST | `/agents/{agent_id}/channels/telegram/onboarding` | `telegram_onboard_start` | `TelegramOnboardStart` | `{pairing_id, deep_link, qr_payload, expires_at}` |
| GET | `/agents/{agent_id}/channels/telegram/onboarding/{pairing_id}` | `telegram_onboard_status` | — | `{status, bot_username?, expires_at}` |
| POST | `/agents/{agent_id}/channels/telegram/onboarding/{pairing_id}/apply` | `telegram_onboard_apply` | `TelegramOnboardApply` | `{ok, bot_username, needs_restart}` |
| DELETE | `/agents/{agent_id}/channels/telegram/onboarding/{pairing_id}` | `telegram_onboard_cancel` | — | `{ok}` |
| POST | `/agents/{agent_id}/channels/whatsapp/onboarding` | `whatsapp_onboard_start` | `WhatsAppOnboardStart` | QR session |
| GET | `/agents/{agent_id}/channels/whatsapp/onboarding/{pairing_id}` | `whatsapp_onboard_status` | — | status |
| POST | `/agents/{agent_id}/channels/whatsapp/onboarding/{pairing_id}/apply` | `whatsapp_onboard_apply` | `WhatsAppOnboardApply` | `{ok, needs_restart}` |
| DELETE | `/agents/{agent_id}/channels/whatsapp/onboarding/{pairing_id}` | `whatsapp_onboard_cancel` | — | `{ok}` |

`TelegramOnboardStart` = `{bot_name?}`; `TelegramOnboardApply` =
`{allowed_user_ids:[]}`; `WhatsAppOnboardStart` = `{mode?, allowed_users?}`;
`WhatsAppOnboardApply` = `{mode?, allowed_users?}`. The agent's profile is taken
from the path `agent_id`, not the body.

### 3e. Pydantic model names (new, in `xnobrain/models/api.py`)

`ChannelUpdate`, `ChannelCredentialSet`, `GatewayDrain`, `PairingApprove`,
`PairingRevoke`, `TelegramOnboardStart`, `TelegramOnboardApply`,
`WhatsAppOnboardStart`, `WhatsAppOnboardApply`. Response payloads are shaped by
the service (dicts) and wrapped by `APIEnvelope`, matching the existing Kanban
style (no per-response Pydantic classes required, but validate inputs strictly).

## 4. Integration-adapter methods (`xnobrain/integrations/gateway.py`)

A thin, lazy-importing adapter (mirror `integrations/kanban.py`'s
`KanbanUnavailable` + `_module()` pattern). Raises `GatewayUnavailable` when the
Hermes runtime is absent. Never returns raw secret values.

```
class GatewayUnavailable(RuntimeError): ...

# --- catalog / status (public gateway.config + gateway.status) ---
list_channels(profile) -> list[dict]        # Platform enum + plugin registry, per-profile enabled/configured/state
get_channel(profile, platform_id) -> dict
channel_env_keys(platform_id) -> tuple[str] # allow-list for validation
port_binds(platform_id, cfg=None) -> bool   # gateway.config.platform_binds_port
multiplex_enabled() -> bool                 # default profile's load_gateway_config().multiplex_profiles

# --- config writes (public hermes_cli.config), profile-scoped ---
set_channel_enabled(profile, platform_id, enabled) -> None      # write_platform_config_field
set_env(profile, key, value) -> None                            # save_env_value
clear_env(profile, key) -> None                                 # remove_env_value
snapshot_profile_config(profile) -> str                         # snapshot .env + config.yaml, returns snapshot id

# --- lifecycle (public gateway API; see approaches.md §3) ---
gateway_status(profile) -> dict             # running/pid/mode/drainable/busy from gateway.status
gateway_start(profile) / gateway_stop(profile) / gateway_restart(profile) -> dict
gateway_drain(profile, action) -> dict      # gateway.drain_control write/clear marker

# --- pairing (public gateway.pairing.PairingStore) ---
pairings(profile) -> dict
approve_pairing(profile, platform, code) -> dict
revoke_pairing(profile, platform, user_id) -> bool
clear_pending(profile) -> int

# --- onboarding (native HTTP endpoints reused for the external setup service) ---
telegram_onboard_* / whatsapp_onboard_*   # delegate to the native /api/messaging/*/onboarding routes
```

Env-var **values** never cross this boundary outward — `list_channels` /
`get_channel` return `redacted_value` + `is_set` only, produced with
`hermes_cli.config.redact_key`. Writes take values inbound once and discard them.

Profile scoping: the adapter enters Hermes' profile scope (the same mechanism
`_profile_scope` uses — set/resolve the profile's `HERMES_HOME`) around each
public-API call so reads/writes hit the target agent's `.env`/`config.yaml`, not
the root install's.

## 5. Service rules (`xnobrain/services/channels.py`)

1. Resolve `agent_id` → profile via `integrations/hermes.py`; 404 if missing.
2. **Read projection:** map Hermes' raw `state` (`disabled, not_configured,
   pending_restart, connected, startup_failed, gateway_stopped`) to a small
   product `connected_state` and a `port_binding`/`multiplex_blocked` flag. Never
   include raw token values.
3. **Multiplex guard (before any enable write):** if `port_binds(platform_id)`
   and `multiplex_enabled()` and profile ≠ default → raise a stable 409 with the
   same guidance Hermes gives ("configure this channel on the default agent").
   Mirror `_multiplex_port_binding_conflict`; do not bypass.
4. **Snapshot before mutation:** call `snapshot_profile_config(profile)` before
   any `.env`/`config.yaml` write.
5. **Env validation:** reject keys not in `channel_env_keys(platform_id)` (400).
6. **Redaction:** all responses run through a redactor; assert no `env_vars[]`
   entry ever carries a raw value.
7. **Lifecycle:** after an enable + credential write, the UI decides whether to
   restart; the service exposes `gateway_restart` and reports `needs_restart`.
8. Convert Hermes 404/409/429 into the XNOBrain error envelope with stable
   codes; log structured names only.

## 6. React UI surface (`src`)

- `api/channels.ts` — typed client over the routes in §3, following
  `api/kanban.ts` / `api/client.ts` (`request<T>`, `requestRaw`).
- `hooks/useChannels.ts` — per-agent channel state, following `hooks/useKanban.ts`
  / `hooks/useConnections.ts` (live reads, mutations, gateway status polling).
- `components/ChannelsView.tsx` — the "Channels" page for the selected agent:
  - A grid of channel cards (Telegram, Discord, Slack, WhatsApp, Signal, …),
    each showing name/icon, `connected_state` badge, enable toggle, credential
    fields (masked, `is_set` indicator), Test button, and a
    `multiplex_blocked` notice where relevant.
  - Guided onboarding modal for Telegram/WhatsApp (QR + deep link).
  - A gateway status/control strip (running/stopped, Start/Stop/Restart/Drain).
  - A pairing panel (pending codes → Approve; approved users → Revoke).
- Wire into `App.tsx` as a new `centerView === 'channels'` (or a Settings
  section), alongside the existing `kanban`/`teams`/`data` views (`App.tsx`
  l.252). Provider "Connections" stays separate — channels are transport, not
  providers.

## 7. Sequence diagrams

### 7a. Enable Telegram (manual bot token)

```
React ChannelsView            XNOBrain (routes→handler→service→integration)         Hermes (same process)
  │ toggle Telegram on              │                                                     │
  │ + paste bot token               │                                                     │
  ├── PUT /api/brain/v1/agents/A/channels/telegram ──▶                                │
  │      {enabled:true, env:{TELEGRAM_BOT_TOKEN:…, TELEGRAM_ALLOWED_USERS:…}}             │
  │                                 │ resolve A→profile (404 if missing)                  │
  │                                 │ multiplex guard: telegram not port-binding → OK     │
  │                                 │ snapshot profile .env + config.yaml ───────────────▶│ (XNOBrain snapshot)
  │                                 │ integration.set_env(TELEGRAM_BOT_TOKEN) ───────────▶│ save_env_value → .env
  │                                 │ integration.set_env(TELEGRAM_ALLOWED_USERS) ───────▶│ save_env_value
  │                                 │ integration.set_channel_enabled(telegram,true) ────▶│ write_platform_config_field
  │                                 │ read back status (redacted) ◀───────────────────────│ load_gateway_config
  │  ◀── 200 {platform:telegram, enabled:true, configured:true, state:pending_restart} ──┤
  ├── POST …/agents/A/gateway/restart ─────────────────▶ gateway_restart ──────────────▶│ hermes gateway restart
  │  ◀── 200 {ok, pid} ─────────────────────────────────┤                                │ gateway connects Telegram
  │ (later) GET …/gateway/status shows running; channel state → connected                │
```

### 7b. Message round-trip (phone → agent → phone)

```
Phone (Telegram app)     Telegram servers      Hermes gateway (profile A)        9router / model
  │ "hey agent" ──────────▶                          │                                │
  │                         │ long-poll delivers ────▶│ session dispatch (Hermes)      │
  │                         │                          │ resolve profile A, run turn ──▶│ (provider forced to 9router)
  │                         │                          │◀── model reply ────────────────┤
  │                         │◀── sendMessage ──────────┤                                │
  │◀── reply shown ─────────┤                          │                                │
```

XNOBrain is **not** on the message path — it only configured the channel and
drove lifecycle. Delivery, session, and dispatch are entirely Hermes'
(`gateway/session.py`, `gateway/stream_consumer.py`, `gateway/delivery.py`),
never re-implemented.
</content>
