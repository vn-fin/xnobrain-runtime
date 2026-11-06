# 015 — Zalo personal channel and gateway hosting

Priority: **P1** (demo: "collect today's information, build a report, send it
via Zalo"). Builds on `plans/005_messaging_channels/` (channel API, UI,
multiplexed gateway). This plan adds the two things 005 does not cover: a Zalo
channel, and how the Hermes gateway process is hosted in XNOBrain deployments.

Security hardening (Control-held secrets, recipient approval) is deliberately
out of scope for the first slice; see "Deferred".

## Goal

An agent can use a personal Zalo account the same way Hermes agents use
WhatsApp (Baileys):

1. The user scans a QR code once; the session survives restarts.
2. Messages from allowed Zalo users start an agent turn and get a reply.
3. A cron job with `deliver=zalo:<thread_id>` sends its output to Zalo.

## Non-goals

- No Zalo Official Account / Zalo Bot API adapter (possible sibling adapter
  later, sharing a behavior mixin like `whatsapp_common.py`).
- No XNOBrain-owned dispatch loop, session store, or message persistence.
  Hermes' gateway owns inbound/outbound flow (same rule as 005).
- No supervisor code inside the Runtime process.
- No user-installable platform plugins; the Zalo plugin ships in the image.
- No group chats, media, reactions in the first slice (text only).

## Verified facts (2026-11-06)

Hermes (installed at `/usr/local/lib/hermes-agent` in the Runtime image):

| Fact | Where |
|---|---|
| Platform plugins: `plugin.yaml` + `adapter.py`, `ctx.register_platform(...)`; no core change | `gateway/platforms/ADDING_A_PLATFORM.md` |
| Bundled `kind: platform` plugins auto-load (deferred); user-installed ones need `plugins.enabled` | `hermes_cli/plugins.py` (manifest `kind` docs, `discover_and_load`) |
| WhatsApp/Baileys pattern: Python adapter spawns detached `node bridge.js --port --session`, reuses it via pidfile + `/health` + `scriptHash`; inbound by polling `GET /messages` (~1 s); outbound `POST /send` | `plugins/platforms/whatsapp/adapter.py`, `scripts/whatsapp-bridge/bridge.js` |
| Bridge inbound queue is in-memory, max 100 | `bridge.js` `MAX_QUEUE_SIZE` |
| Reconnect watcher retries forever (30 s → 5 min cap); `NEEDS_ATTENTION` after 2 h of continuous failure | `gateway/run.py` `_platform_reconnect_watcher`, `_reconnect_backoff` |
| Token-less platforms are never dropped from the retry queue | `gateway/run.py` `_platform_has_bot_credential` |
| Cron delivery without a live gateway uses the plugin's `standalone_sender_fn` | `gateway/platform_registry.py`, `cron/scheduler.py` `_deliver_result` |
| Containers: run `hermes gateway run` as a main process and rely on the container restart policy; hosts with systemd get a `Restart=always` unit | `hermes_cli/gateway.py` (`is_container()` branch, unit template) |
| Multiplex: default profile serves all profiles; each profile needs its own account; allowlist via `gateway.multiplex_profile_allowlist` | `website/docs/user-guide/multi-profile-gateways.md` |
| Open platforms without an allowlist or explicit allow-all abort gateway startup | same doc, "Security configuration errors remain fatal" |

XNOBrain Runtime:

| Fact | Where |
|---|---|
| Runtime never starts `hermes gateway`; no channel code or routes exist | `xnobrain/app.py`, plan 005 README |
| One Runtime process per workspace container; agents are profiles under `/opt/data/agent` | `docs/architecture.md`, `docker-compose.dev.yaml` |
| Bundled Hermes plugins are trusted by existence (no scan/approval) | `xnobrain/integrations/extension_policy.py` (`bundled` branch) |
| The marketplace/extension installer rejects `kind != standalone`, stdio MCP and JS code | `xnobrain/integrations/extension_engine.py` `_check_native_package`, `plugin_formats.py` |
| Runtime cron fires `fire_due(job_id, adapters=None)` → standalone send path | `xnobrain/services/cron.py` |
| Node 22 is in the image; npm installs already run at build time | `Dockerfile.backend` |
| `API_SERVER_PORT=3000` is the Runtime API port (also the WhatsApp bridge default port) | `Dockerfile.backend` |

zca-js (`RFS-ADRENO/zca-js`, MIT, v2.2.0):

- `loginQR()` returns `{cookie, imei, userAgent}`; `login({cookie, imei, userAgent})`
  restores a session without a new QR.
- `listener.start({ retryOnClose })`: WebSocket to Zalo Web, ping keepalive,
  events `message`, `reaction`, `undo`, `group_event`, `closed`, `error`, ….
- Close code `3000 DuplicateConnection`: opening Zalo Web for the same account
  closes the listener. One listener per account.
- No automatic catch-up: `requestOldMessages(threadType, lastMsgId)` must be
  called explicitly after a reconnect.
- Unofficial; Zalo may lock the account.

## Decisions

### D1 — Gateway hosting: the platform's own supervisor, no Runtime code

Follow Hermes' own guidance per environment:

- **Docker (dev, `make dev`)**: a new compose service `runtime-gateway` per
  Runtime service, same image and volume (`xnobrain_runtime_data`, created as
  `xnobrain_xnobrain_runtime_data`), `entrypoint: ["hermes"]`,
  `command: ["gateway", "run"]`, `restart: unless-stopped`, no published
  ports, and `healthcheck: {disable: true}` (the image healthcheck probes the
  Runtime API on `:3000` and would always report unhealthy).
  `pid: "service:runtime"` so Runtime can read and signal the gateway PID.
- **Incus/native (systemd)**: ship `runtime/xnobrain-gateway.service` next to
  `xnobrain-runtime.service`, copying Hermes' unit directives
  (`Restart=always`, `RestartSec=5`, `KillMode=mixed`, `ExecReload=kill -USR1`)
  and the Runtime unit's hardening (`User=xnobrain`, `ProtectSystem=strict`,
  `ReadWritePaths=/opt/data /run/xnobrain-runtime`).
- **Restart contract (both environments):** Runtime never runs
  `hermes gateway restart` (nor the dashboard `/api/gateway/restart`, which
  spawns it). Without systemd that command stops the gateway and starts a new
  foreground gateway inside the *calling* container, so the restart policy
  then yields two gateways. Instead Runtime sends `SIGUSR1` to the PID in
  `<root HERMES_HOME>/gateway.pid` (JSON, `pid` field; the same signal as
  Hermes' `ExecReload`): the gateway drains, exits with code 75
  (`GATEWAY_SERVICE_RESTART_EXIT_CODE`), and Docker/systemd starts it again.
  The restart policy is mandatory: without it exit 75 leaves the gateway
  dead. A restart can wait for active turns up to
  `agent.restart_after_turn_timeout` (default 1800 s), so callers must not
  assume a fixed duration; report `pending_restart` until a new PID appears.
  Drain logs go to `$HERMES_HOME/logs/gateway.log`, not `docker logs`.
- Multiplex on the root profile (`big-brother`), with
  `multiplex_profile_allowlist` = profiles that have a channel enabled,
  **never including `big-brother` itself**. Hermes enumerates profiles under
  `<root>/profiles`, which is a symlink to `/opt/data/agent`, so the root is
  listed twice (`default` and `big-brother`, same home). Nothing dedupes a
  token-less device-session platform, so a duplicate would open a second Zalo
  listener and trigger `3000 DuplicateConnection`. Hermes does not read
  `HERMES_PROFILES_ROOT`; keep the symlink in packaging.
- Pin `platforms.api_server.enabled: false` in the root profile
  `config.yaml`. Any usable `API_SERVER_KEY` in the environment force-enables
  `api_server`, which binds `0.0.0.0:$API_SERVER_PORT` (3000). Runtime owns
  that port and uses `api_server` in-process.

Implementation: `runtime/gateway-entrypoint.sh` loads `RUNTIME_LLM_API_KEY`
from `RUNTIME_LLM_API_KEY_FILE` (never printing it) and `exec`s
`hermes gateway run`; `runtime/xnobrain-gateway.service` carries the same
hardening as the Runtime unit plus `Restart=always`, SIGUSR1 reload and
`KillMode=mixed`, and `Dockerfile.backend` installs and enables it. Compose
adds a `runtime-gateway` service with the Runtime image/volumes and
`pid: "service:runtime"`.

Rejected: a supervisor inside the Runtime process (new long-lived child
management with no precedent in Runtime; duplicates what Docker/systemd do).

### D2 — Packaging: bundled Hermes platform plugin

- Source: `xnobrain-runtime/runtime/hermes-plugins/platforms/zalo/`
  (`plugin.yaml`, `adapter.py`, `bridge/` with `package.json`, lockfile,
  `bridge.js`).
- `Dockerfile.backend`: copy into
  `/usr/local/lib/hermes-agent/plugins/platforms/zalo/` and run
  `npm ci --omit=dev` in `bridge/` at build time.
- Native install: same copy into the native Hermes tree
  (`deploy/incus/install-native-runtime.sh`).
- No change to the extension installer or marketplace.

### D3 — Adapter and bridge mirror WhatsApp

- Bridge (`bridge.js`, Express on `127.0.0.1` only): `GET /health`
  (`status`, `scriptHash`), `GET /messages`, `POST /send`, QR events on stdout.
  Session file under the profile: `<HERMES_HOME>/zalo/session.json`.
- Catch-up: persist `lastMsgId` per thread in `<HERMES_HOME>/zalo/cursor.json`;
  after `connected`, call `requestOldMessages` and enqueue unseen messages.
- Adapter: copy the WhatsApp bridge lifecycle (spawn detached, pidfile,
  stale-hash restart, exit detection) and registration:
  `allowed_users_env="ZALO_ALLOWED_USERS"`, `allow_all_env="ZALO_ALLOW_ALL_USERS"`,
  `cron_deliver_env_var="ZALO_HOME_CHANNEL"`, `standalone_sender_fn`.
- Bridge port: `extra.bridge_port`, default **3110 + profile index** —
  never 3000.

### D4 — Configuration lives in the profile (005 Decision 2)

`platforms.zalo.enabled` in `config.yaml`; `ZALO_ALLOWED_USERS`,
`ZALO_HOME_CHANNEL` in the profile `.env`. The zca-js session file stays in the
profile directory (device-session channel; it cannot be proxied through
Control).

## Phases

**Phase 0 — Prove (no product code).** Run on 2026-11-06; results below in
"Phase 0 results". Remaining:
- Real Zalo account: QR login, `login({cookie, imei, userAgent})` after a
  bridge restart, one DM answered, Zalo Web opened → listener closes and
  recovers. Needs a human to scan.

**Phase 1+2 — Plugin, bridge, gateway hosting.** Done (commit `4bae60e`):
plugin + bridge (login, `/send`, `/messages`, `/health`, cursor catch-up,
terminal 3000/3003, keepalive, cookie re-save, port 3110 + profile index),
`runtime/xnobrain-gateway.service`, compose `runtime-gateway`. Smoke run in
dev: plugin loads, stays disabled until enabled.

**Phase 3 — Gateway turns behave like Runtime turns (P0).**
Verified 2026-11-06: gateway calls are billed to the key owner but carry no
agent/conversation/run headers, so per-agent usage and budgets miss them;
with `multiplex_profiles` the per-turn secret scope is the profile `.env`
only, so the router key is invisible and calls fail with 401 (the cron bug
`conversation_credentials.py` already patches).
- Start the gateway through a small XNOBrain launcher that applies the same
  shims as `xnobrain/worker_cli.py` (router key in the profile secret scope,
  attribution headers) and then runs Hermes' gateway main.
- Pin `platforms.api_server.enabled: false` and `gateway.multiplex_profiles`
  in the root profile at prepare time.

**Phase 4 — UC2: send on request, capture replies (P0).**
Use case: in web chat the user asks "send Nam on Zalo: …"; Nam's later reply
is recorded in that same conversation. No agent turn is triggered by Nam.
- Bridge appends every inbound DM to `<HERMES_HOME>/zalo/history.jsonl`
  (capture is separate from replying; Hermes allowlist/pairing still decides
  who gets an agent reply).
- Runtime tool `send_channel_message(recipient, text)`: resolves the
  recipient from `history.jsonl` and `channel_directory.json`, asks for
  approval with the existing gateway approval path
  (`_await_gateway_decision`, UI events `approval.request/responded`), sends
  through the plugin's standalone sender, and records
  `{thread_id → agent, conversation_id}` in `<HERMES_HOME>/zalo/links.json`.
- Adapter: an inbound DM whose thread is in `links.json` is posted to a new
  internal Runtime route (guarded by `RUNTIME_INTERNAL_SERVICE_TOKEN`, as in
  `time_control._authorize`), which appends it with
  `SessionDB.append_message` as a clearly marked external message
  ("Zalo · Nam: …, not an instruction"). The UI shows it after reload; no
  live event in the first slice.

**Phase 5 — Product surface (P0).**
Channel API (list/status, enable/disable with snapshot + SIGUSR1 +
`pending_restart`, QR, allowlist/pairing, logout, home channel) and a Zalo
card on the agent page; cron target `zalo`; feature flag
`FT_ENABLE_AGENT_CHANNELS`; docs, release notes, third-party notices.
VM workspaces (Control's default instance type) use the native unit
`deploy/systemd/xnobrain-api.service` with data under `/srv/xnobrain-data`;
`runtime/xnobrain-gateway.service` covers container workspaces only, so add a
native gateway unit for VMs (installed by `deploy/incus/install-native-runtime.sh`).

Phase 5 channel API contract (prefix as other Runtime routes; all routes and
the `send_channel_message` tool are gated by `FT_ENABLE_AGENT_CHANNELS`,
default false; normal user auth like other `/agents/{agent_id}/…` routes):

| Method and path | Body | Result |
|---|---|---|
| `GET /agents/{agent_id}/channels` | — | `{channels: [ChannelState], gateway: {running, pid, pending_restart}}` |
| `PUT /agents/{agent_id}/channels/zalo` | `{enabled: bool}` | `ChannelState` |
| `PATCH /agents/{agent_id}/channels/zalo` | `{allowed_users?: [id], dm_policy?: "disabled"\|"allowlist"\|"pairing", home_channel?: id\|null}` | `ChannelState` |
| `GET /agents/{agent_id}/channels/zalo/qr` | — | `{status, image_base64: str\|null}` (PNG while `status == "qr"`) |
| `POST /agents/{agent_id}/channels/zalo/logout` | — | `ChannelState` |

`ChannelState = {platform: "zalo", enabled, status, own_id, allowed_users,
dm_policy, home_channel}`; `status` is one of `disabled`, `starting`, `qr`,
`connected`, `closed`, `pending_restart`, `gateway_down`. Status comes from
the bridge `/health` on `127.0.0.1:<port>`. Writes snapshot first, then
atomically update `config.yaml` (`platforms.zalo.enabled`, `extra.dm_policy`,
new channels default `dm_policy: disabled`) and `.env` (`ZALO_ALLOWED_USERS`,
`ZALO_HOME_CHANNEL`); keep the root profile's `gateway.multiplex_profiles`
and `multiplex_profile_allowlist` (every non-root profile with Zalo enabled,
never `big-brother`) in sync; then `SIGUSR1` the PID in
`<root>/gateway.pid` and report `pending_restart` until that PID changes.
Logout deletes `<profile>/zalo/session.json` and restarts. Cron delivery can
target `zalo` when the channel is enabled. Dev Compose runs the gateway with
`network_mode: service:runtime` so loopback matches the single-container
production layout.

Phase 5 status (2026-11-06): channel API `faf2786`, VM unit `ace42ea`, UI
`xnobrain-ui` `945dd58` (branch `feat/zalo-channels`), dev flag wiring in the
root repo. Dev end-to-end via the API on `plugin-qa`: enable → SIGUSR1
restart → bridge on 3111 in `needs_login` → QR only on request → disable
cleans the bridge and multiplex allowlist. Open: the API refuses channels on
Big Brother (`protected_agent`); decide whether Big Brother may own the
workspace Zalo number. Not done: UI checked only by tests, real QR scan.

**Phase 6 — Later (P1/P2).**
UC1 (owner commands Big Brother from Zalo through the Runtime pipeline);
showing gateway sessions in the UI; live event for appended replies; history
read/search tools; gateway activity lease so rebalance/rollout drains wait;
flush cursor on SIGTERM; contacts lookup via `getAllFriends`; owner
notifications. Account model (assistant account vs owner's own account) is
undecided; the design works for both.

## Phase 0 results (2026-11-06)

Probes used a scratch `HERMES_HOME` and `--rm` containers on `--network none`;
real profiles were not modified.

| Check | Result | Evidence / consequence |
|---|---|---|
| Gateway as its own container | PASS | `hermes gateway run` stays up with zero platforms ("continue running for cron"), binds no port. Needs `--entrypoint hermes`; image healthcheck must be disabled. |
| `hermes gateway restart` from Runtime | FAIL | Stops the gateway, then runs a new foreground gateway in the Runtime container (`hermes_cli/gateway.py` manual-restart branch, `run_gateway(verbose=0)`). Replaced by the `SIGUSR1` contract in D1. |
| `SIGUSR1` restart (shared PID namespace, `--restart unless-stopped`) | PASS | `kill -USR1` from the Runtime container: drain, exit 75 (`gateway/restart.py`), Docker restart, ready in ~2 s with no active turns; exactly one gateway and `gateway.pid` updated. Two signals 1 ms apart produce one restart. Handler: `gateway/run.py` `loop.add_signal_handler(signal.SIGUSR1, ...)`. |
| Multiplex profile discovery | PASS, with hazard | `profiles_to_serve()` scans `<root>/profiles` (symlink to `/opt/data/agent`); root appears twice. Allowlist must exclude `big-brother`. |
| `api_server` / port 3000 | PASS today, hazard | No profile enables platforms today. A usable `API_SERVER_KEY` force-enables `api_server` on `0.0.0.0:3000` (`gateway/config.py` api_server env block). Pin `enabled: false`. |
| Bundled platform plugin loading | PASS | Bundled `kind: platform` plugins register as deferred entries; Runtime's `cron_profile_scope` runs `discover_and_load` before `fire_due(adapters=None)`, so `standalone_sender_fn` is found. |
| Adapter contract | PASS | Abstract methods: `connect(*, is_reconnect=False)`, `disconnect()`, `send(chat_id, content, reply_to, metadata)`, `get_chat_info(chat_id)` (`gateway/platforms/base.py`). Registration fields: as WhatsApp `register()`. |
| Duplicate cron delivery | PASS | Runtime loop, desktop ticker and gateway ticker share `jobs.json`; `claim_job_for_fire` (file lock + `fire_claim`, 300 s TTL) and delivery inside the claim give at-most-once. Cost: three idle tick loops. |
| Node / zca-js | PASS | Node 22.23.1, npm 10.9.8; zca-js 2.2.0, `engines.node >= 18`; registry reachable from the Runtime network. |
| Real Zalo login | PENDING | Requires a human QR scan (see Phase 0 "Remaining"). |

## Tests

- Bridge (node test runner, like `whatsapp-bridge/*.test.mjs`): loopback-only
  host check, queue cap, `/send` validation, cursor catch-up dedupe.
- Adapter (pytest): bridge reuse vs stale restart, exit detection,
  allowlist gate, standalone sender error mapping.
- Runtime: cron delivery target list includes `zalo` when enabled.
- Manual: QR login, restart survival, reply round-trip, cron delivery,
  Zalo Web opened → `closed` → status and recovery.

## Risks

- Account lock by Zalo; use a dedicated account for demos.
- Opening Zalo Web for the account stops inbound until reconnect.
- In-memory bridge queue (100) and offline gaps can lose messages; catch-up
  covers only what `requestOldMessages` returns.
- zca-js breaks when Zalo changes its web protocol; pin the version.
- One Node process per enabled profile (RAM per workspace).
- Plan 005 text predates the current topology (mentions 9router and `:8642`);
  re-check its contracts before Phase 3.

## Deferred

- Secrets in Control (`docs/features/mcp-control-proxy.md` pattern) for
  webhook/REST channels; not applicable to device-session channels like this.
- Recipient approval for outbound messages; read-only "memory" mode where
  inbound messages are stored but do not trigger the agent.
- Zalo Official Account / Bot API sibling adapter.
