# 005 — Findings (gap analysis)

All paths below are real and were read during analysis. `.tools/hermes-agent/`
is the pinned Hermes source Brain4All extends (read-only reference). Line
numbers are from the artifact present on 2026-07-24 and must be re-verified
against the pinned commit in Phase 0 (`implementation.md` §Phase 0).

Cross-references: `architecture.md` (contract), `approaches.md` (options),
`implementation.md` (steps), `validation.md` (tests).

## 1. What Hermes provides

### 1a. Gateway lifecycle — native HTTP API (`hermes_cli/web_server.py`)

The gateway runs **inside** the Hermes process. Lifecycle is driven by these
native endpoints on the shared FastAPI app (`:8642`), all profile-scoped by a
`profile` query param:

| Endpoint | Line | Behavior |
|---|---|---|
| `POST /api/gateway/start?profile=` | 13414 | Spawns `hermes gateway start` for the profile (`_spawn_hermes_action`, `_gateway_subcommand`, l.3784/3878). Returns `{ok, pid, name}`. |
| `POST /api/gateway/stop?profile=` | 13426 | Spawns `hermes gateway stop`. |
| `POST /api/gateway/restart?profile=` | 3985 | Background `hermes gateway restart` (`_spawn_gateway_restart`, l.3942), returns `{ok, pid}`. |
| `POST /api/gateway/drain` | 4002 | Body `{"action":"drain"|"cancel"}`. Writes/removes the `.drain_request.json` marker the gateway's `_drain_control_watcher` observes (`gateway/drain_control.py`). The marker **is** the control channel — there is no HTTP channel into the running gateway. |

The lifecycle "public contract" is the `hermes gateway {start,stop,restart}`
CLI subcommand; the HTTP endpoints are thin wrappers that `subprocess`-spawn it
(`_spawn_hermes_action`, l.3784). `hermes_cli/gateway.py` is the public gateway
module. **Brain4All must never re-implement the dispatch/session loop** — same
rule as the Kanban dispatcher (`plans/001_kanban_foundation/README.md`
§Dispatcher lifecycle).

### 1b. Channel catalog, credentials, enable/test — native HTTP API

| Endpoint | Line | Behavior |
|---|---|---|
| `GET /api/messaging/platforms?profile=` | 9448 | Returns `{env_path, gateway_start_command, platforms:[…]}`. Each platform payload (`_messaging_platform_payload`, l.8438) has `id, name, description, docs_url, enabled, configured, gateway_running, state, error_code, error_message, updated_at, home_channel, env_vars[]`. Each `env_vars[]` entry is `{key, required, is_set, redacted_value, description, …}` — **values are redacted** via `redact_key`. |
| `PUT /api/messaging/platforms/{platform_id}` | 9520 | Body = `MessagingPlatformUpdate` (l.1252): `{enabled?, env:{}, clear_env:[], profile?}`. Writes each `env[k]=v` via `save_env_value`, removes `clear_env[k]` via `remove_env_value`, and writes `platforms.<id>.enabled` via `write_platform_config_field`. Validates env keys against the platform's allow-list; rejects unknown keys (400). Rejects enabling a **port-binding** platform on a secondary profile under multiplex (409, `_multiplex_port_binding_conflict`, l.9469). Audit-logs **names only, never values** (l.9570). |
| `POST /api/messaging/platforms/{platform_id}/test?profile=` | 9587 | Reports whether the platform is enabled, configured, gateway-running, and connected. Returns `{ok, state, message}`. Does not leak secrets. |

The catalog is built from the gateway's `Platform` enum plus the plugin
registry (`_messaging_platform_catalog`, l.8261), so newly installed adapters
appear without a code change.

### 1c. Guided onboarding — native HTTP API

Telegram (QR / bot-father-free pairing via an external setup service):

- `POST /api/messaging/telegram/onboarding/start` (l.9232) → `{pairing_id,
  suggested_username, deep_link, qr_payload, expires_at}`.
- `GET /api/messaging/telegram/onboarding/{pairing_id}` (l.9270) → `{status:
  waiting|ready, bot_username?, owner_user_id?, expires_at}`.
- `POST /api/messaging/telegram/onboarding/{pairing_id}/apply` (l.9372) →
  Body `TelegramOnboardingApply` `{allowed_user_ids:[], profile?}`. Saves
  `TELEGRAM_BOT_TOKEN` + `TELEGRAM_ALLOWED_USERS` to the profile `.env`, sets
  `platforms.telegram.enabled=true`, and auto-restarts the gateway. Returns
  `{ok, platform, bot_username, needs_restart, …}`.
- `DELETE /api/messaging/telegram/onboarding/{pairing_id}` (l.9441) — cancel.

WhatsApp (QR pairing, `whatsapp` web-multi-device mode):

- `POST /api/messaging/whatsapp/onboarding/start` (l.8944) → QR session.
- `GET /api/messaging/whatsapp/onboarding/{pairing_id}` (l.9001).
- `POST /api/messaging/whatsapp/onboarding/{pairing_id}/apply` (l.9016).
- `DELETE /api/messaging/whatsapp/onboarding/{pairing_id}` (l.9072).

### 1d. Pairing / linking — native HTTP API

Backed by `gateway/pairing.py::PairingStore` (`_pairing_store`, l.13176):

- `GET /api/pairing` (l.13182) → `{pending:[…], approved:[…]}`.
- `POST /api/pairing/approve` (l.13191) — Body `PairingApprove{platform, code}`.
  Approves a pending code; 404 unknown/expired, 429 locked-out.
- `POST /api/pairing/revoke` (l.13213) — Body `PairingRevoke{platform, user_id}`.
- `POST /api/pairing/clear-pending` (l.13227) → `{ok, cleared}`.

### 1e. Public Python primitives (no HTTP wrapper needed)

These are **public** (no leading underscore) and importable in-process, exactly
as the Kanban adapter imports `hermes_cli.kanban_db`:

- `gateway.config` (`.tools/hermes-agent/gateway/config.py`):
  - `Platform` enum (l.272): `telegram, discord, whatsapp, whatsapp_cloud,
    slack, signal, mattermost, matrix, homeassistant, email, sms, dingtalk,
    api_server, webhook, msgraph_webhook, feishu, wecom, wecom_callback, weixin,
    bluebubbles, qqbot, yuanbao, relay` + dynamic plugin members via `_missing_`.
  - `load_gateway_config()` → `GatewayConfig` with `.platforms`,
    `.multiplex_profiles` (l.897), `._is_platform_connected()` (l.943),
    `.get_home_channel()` (l.985).
  - `PORT_BINDING_PLATFORM_VALUES` (l.384): `{webhook, api_server,
    msgraph_webhook, feishu, wecom_callback, bluebubbles, sms, whatsapp_cloud,
    line}`. `platform_binds_port()` (l.404) handles Feishu's mode-conditional
    binding.
  - `PlatformConfig` (l.564), `HomeChannel`.
- `hermes_cli.config` (imported at `web_server.py` l.60): `save_env_value`,
  `remove_env_value`, `load_env`, `get_env_path`, `redact_key`,
  `write_platform_config_field`, `load_config`, `OPTIONAL_ENV_VARS`.
- `gateway.status` (l.89): `read_runtime_status()` (reads
  `gateway_state.json`), `get_running_pid()`, `get_runtime_status_running_pid()`.
- `gateway.pairing.PairingStore`.
- `gateway.channel_directory` (l.471): `load_directory()`,
  `resolve_channel_name()`, `lookup_channel_type()`.
- `gateway.platform_registry.platform_registry.plugin_entries()` (l.266).

## 2. What Brain4All has / lacks

Brain4All layers (`AGENTS.md`, `plans/001_kanban_foundation/README.md`):

| Layer | File | Channels status |
|---|---|---|
| Routes (only assembly point) | `brain4all/routes/setup.py` | **No** `/gateway`, `/channels`, `/messaging`, or `/pairing` routes. Zero coverage. |
| Handlers | `brain4all/handlers/api.py` | `APIHandlers.dispatch` routes by `route.name` into an `operations` dict (l.48). No channel operations. |
| Services | `brain4all/services/` | `kanban.py`, `platform.py`, `portability.py`. **No** `channels.py`. |
| Integrations | `brain4all/integrations/` | `hermes.py`, `config.py`, `nine_router.py`, `kanban.py`, `runtime.py`. **No** `gateway.py`. |
| Models | `brain4all/models/api.py` | No channel/gateway/pairing models. |
| Frontend | `src/` | `components/ConnectionsView.tsx` is **model-provider** connections (9router/API keys), **not** channels. `hooks/useConnections.ts` likewise. No channels page, hook, or `api/channels.ts`. |

Brain4All already proves the pattern this plan reuses: each **agent is a Hermes
profile** (`integrations/hermes.py::_profile_dir`, `_native_profile_dir`,
`create_agent`), and it already reads/writes a profile's `config.yaml`
atomically (`integrations/config.py::GlobalConfigManager._atomic_write`,
`_write_config`) with snapshot-before-mutation (`AGENTS.md` §Persistence). The
missing piece is a channel-configuration surface over the profile's `.env` +
`config.yaml` `platforms` section, plus gateway lifecycle control.

## 3. The profile ↔ channel ↔ multiplex question

**Fact:** every Hermes messaging endpoint above is profile-scoped (`?profile=`
or `body.profile`, resolved by `_profile_scope`, `web_server.py` l.15190).
Channel enablement and tokens are per-profile: written to that profile's `.env`
and `config.yaml`. So Brain4All naturally maps `agent_id → profile`.

**The multiplex constraint** (`gateway.multiplex_profiles`, verified at
`web_server.py::_multiplex_port_binding_conflict` l.9469 and
`gateway/config.py` l.384–412):

- When `multiplex_profiles` is **on**, the **default** profile owns the single
  shared HTTP listener and serves every profile via a `/p/<profile>/` prefix
  (one gateway process for all agents).
- A **secondary** profile may therefore **not** enable a *port-binding*
  platform (`PORT_BINDING_PLATFORM_VALUES`: `webhook, api_server,
  msgraph_webhook, feishu(webhook-mode), wecom_callback, bluebubbles, sms,
  whatsapp_cloud, line`), because only the default profile owns the listener.
  Hermes already rejects this with **409** before writing config.
- **Client-type** channels — Telegram, Discord, Slack, Signal, Matrix,
  Mattermost, WhatsApp (web multi-device), DingTalk, WeCom(bot), QQ, etc. — use
  outbound long-poll / websocket / bridge connections and do **not** bind a
  port, so they multiplex cleanly per agent.

**Consequence for Brain4All:** the common case (phone chat via Telegram /
Discord / Slack / Signal / WhatsApp) works per-agent under a single multiplexed
gateway. Port-binding webhook-style channels are a restricted case that the
Brain4All service must surface honestly (mirror Hermes' 409) rather than hide.
See `approaches.md` §1 for the multiplexed-vs-per-agent decision and the
recommendation (single multiplexed gateway; enable multiplex on the default
profile; restrict port-binding channels to the default agent).

## 4. Compatibility APIs to pin & test (Phase 0)

The compatibility test (`validation.md` §1) must assert, against the pinned
artifact in a temp `HERMES_HOME`, that:

1. `gateway.config.Platform` exists and contains at least `telegram, discord,
   slack, signal, whatsapp`; `PORT_BINDING_PLATFORM_VALUES` and
   `platform_binds_port` exist; `load_gateway_config().multiplex_profiles` and
   `._is_platform_connected` are callable.
2. `hermes_cli.config` exports `save_env_value, remove_env_value, load_env,
   redact_key, write_platform_config_field, load_config, OPTIONAL_ENV_VARS`.
3. `gateway.pairing.PairingStore` exposes `list_pending, list_approved,
   approve_code, revoke, clear_pending`.
4. `gateway.status.read_runtime_status`, `get_running_pid` exist.
5. The native FastAPI app registers the routes in §1a–1d (assert by app route
   table or a live `TestClient`): `/api/gateway/{start,stop,restart,drain}`,
   `/api/messaging/platforms`, `/api/messaging/platforms/{id}` (PUT/test),
   `/api/messaging/telegram/onboarding/*`, `/api/messaging/whatsapp/onboarding/*`,
   `/api/pairing`, `/api/pairing/{approve,revoke,clear-pending}`.

If any assertion fails, Brain4All readiness must fail with **one** concise
remediation line (per `plans/001_kanban_foundation/README.md` Phase 0 §5). If a
needed capability turns out to be Hermes-private (e.g. no public gateway-start
entrypoint reachable without the private `web_server` spawn helper), expose a
small public hook upstream in `hermes_cli.gateway` — **do not** copy the spawn
logic.

## 5. Credential-security notes

- Tokens live only in the profile `.env` (values) and `config.yaml`
  (`platforms.<id>.enabled`, `home_channel`) — Hermes' native storage. **Do not
  invent a Brain4All credential file.**
- Reads must return **only** `redacted_value` + `is_set`, never raw tokens —
  Hermes' `_messaging_platform_payload` already redacts (`redact_key`); the
  Brain4All service must preserve that and never add a raw-value field.
- Writes accept a token in the request body once, persist it, and echo back
  only `is_set`/redacted state — never the value. Snapshot the profile's
  `.env` + `config.yaml` **before** the write (`AGENTS.md` §Persistence).
- Audit-log **names only, never values** (mirror `web_server.py` l.9570). Never
  log, trace, or put in an error body: bot tokens, allowed-user lists, pairing
  codes, provider keys, prompts.
- Pairing codes are short-lived secrets; return them only to the approving UI,
  never log them. Hermes locks out platforms after repeated failed approvals
  (429) — surface that, don't retry-loop.
- The portability bundle export (`services/portability.py`,
  `/api/brain/v1/bundles/export`) must **not** include a profile's channel tokens.
  Verify the `.env` channel keys are excluded or redacted in exports
  (`validation.md` §security check).

## 6. Risks

1. **Copying Hermes internals.** The temptation is to re-derive the platform
   payload / multiplex check. Mitigation: ride the native endpoints or the
   public `gateway.config` primitives; keep only thin projection in the service.
2. **Gateway-start reachability in-process.** The native start endpoint spawns
   `hermes gateway start`. Brain4All must drive lifecycle the same public way
   (CLI subcommand or the native endpoint), not by importing private
   `_spawn_hermes_action`. Confirm a public path in Phase 0.
3. **Multiplex misconfiguration bricking all agents.** Enabling a port-binding
   channel on a secondary profile can kill the shared gateway for everyone.
   Mitigation: enforce Hermes' 409 guard in the service **before** any write
   and never bypass it.
4. **Secret leakage.** Tokens in responses/logs/bundles. Mitigation: §5 rules +
   a dedicated security test (`validation.md`).
5. **Loopback auth.** If the adapter rides native HTTP endpoints, it must pass
   Hermes' auth gate; a naive loopback call may 401. Mitigation: prefer the
   in-process public-Python adapter for reads/config-writes (`approaches.md` §3).
6. **Onboarding depends on an external Telegram setup service.** Network
   failures must degrade to the manual bot-token path, not a hard error.

## 7. Open questions

1. Should Brain4All auto-enable `gateway.multiplex_profiles` on the default
   profile at first channel enablement, or require the user to opt in? (Recommend
   auto-enable with a one-time notice; see `approaches.md`.)
2. Is there a public `hermes_cli.gateway` start/stop entrypoint callable
   in-process without the private `web_server` spawn helper? Confirm in Phase 0;
   if not, ride the native `/api/gateway/*` endpoints or upstream a small hook.
3. Does the pinned Hermes auth middleware allow an in-process loopback call from
   Brain4All's own handler, or must the adapter use the public Python APIs?
   Decide in Phase 0 (drives `approaches.md` §3).
4. Which channels should the first UI release surface as "guided" (Telegram,
   WhatsApp have native onboarding) vs "manual token" (all others)?
5. Should per-agent channel state be cached, or read live from Hermes on each
   request? (Recommend live reads; no Brain4All-owned channel state.)
