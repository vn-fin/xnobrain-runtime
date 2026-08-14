# 005 — Validation

How to prove the feature works and is safe. Ties to `implementation.md` phases
and the `findings.md` §4 pinned contract. Nothing here is "done" without the
listed evidence (`plans/CHECKLIST.md` legend).

## 1. Compatibility test (Phase 0 gate)

File: `xnobrain/tests/test_channels_compat.py`. Runs against the **pinned**
Hermes artifact in a temp `HERMES_HOME`.

- [ ] Imports of `gateway.config` (`Platform`, `PORT_BINDING_PLATFORM_VALUES`,
  `platform_binds_port`, `load_gateway_config`, `PlatformConfig`) succeed.
- [ ] `Platform` contains `telegram, discord, slack, signal, whatsapp`.
- [ ] `load_gateway_config().multiplex_profiles` is a bool;
  `GatewayConfig._is_platform_connected` is callable.
- [ ] Imports of `hermes_cli.config` writers/readers succeed (`save_env_value`,
  `remove_env_value`, `load_env`, `redact_key`, `write_platform_config_field`,
  `load_config`, `OPTIONAL_ENV_VARS`).
- [ ] `gateway.pairing.PairingStore` exposes `list_pending, list_approved,
  approve_code, revoke, clear_pending`.
- [ ] `gateway.status.read_runtime_status`, `get_running_pid` import.
- [ ] Native app route table contains `/api/gateway/{start,stop,restart,drain}`,
  `/api/messaging/platforms`, `/api/messaging/platforms/{platform_id}` (PUT +
  `/test`), `/api/messaging/telegram/onboarding/*`,
  `/api/messaging/whatsapp/onboarding/*`, `/api/pairing`,
  `/api/pairing/{approve,revoke,clear-pending}`.
- [ ] Chosen public gateway-lifecycle path (Phase 0 §3) is exercised once.

Evidence: test output naming the pinned commit; a failing assertion prints the
one-line remediation and fails startup readiness.

## 2. Unit tests (service + integration, mocked runtime)

File: `xnobrain/tests/test_channels.py` (unit section) + handler tests in
`xnobrain/tests/test_fastapi.py`.

- [ ] `agent_id → profile` resolution; unknown agent → 404 before any Hermes
  call.
- [ ] Env-key allow-list: an env key not in `channel_env_keys(platform_id)` →
  400, no write performed.
- [ ] Multiplex guard: enabling a port-binding platform (e.g. `whatsapp_cloud`,
  `webhook`) on a non-default profile with `multiplex_profiles=on` → 409 with
  guidance; enabling the same on the **default** profile → allowed; disabling a
  port-binding platform on a secondary profile → allowed.
- [ ] Client-type channel (telegram) enable on any profile → allowed.
- [ ] Snapshot-before-write: a snapshot of `.env` + `config.yaml` exists before
  the write is applied (assert snapshot call ordering / artifact).
- [ ] Redaction: every `env_vars[]` entry in every response carries
  `redacted_value`/`is_set` and **no** raw value key; a set token never appears
  in the response body.
- [ ] Handler envelope: success → `{success:true,data,…}`; service errors →
  `failure(...)` with stable code + status.
- [ ] Pydantic validation: malformed `ChannelUpdate`/`PairingApprove`/onboarding
  bodies → 422.

## 3. Integration tests (real pinned Hermes, temp `HERMES_HOME`)

File: `xnobrain/tests/test_channels.py` (integration section, mirror
`test_kanban.py`).

- [ ] List channels for a fresh profile: catalog non-empty, all disabled,
  `configured:false`.
- [ ] Enable telegram + set `TELEGRAM_BOT_TOKEN`/`TELEGRAM_ALLOWED_USERS`:
  reload shows `enabled:true`, `configured:true`; the profile `.env` and
  `config.yaml` (`platforms.telegram.enabled=true`) reflect it; the native
  `GET /api/messaging/platforms` reports the same.
- [ ] Rotate credential: new token persisted, response still redacted.
- [ ] Clear credential + disable: `.env` key removed, `enabled:false`.
- [ ] Gateway status read reflects `get_running_pid()`.
- [ ] Pairing: seed a pending code via `PairingStore`, approve through the
  XNOBrain route, verify it moves to approved; revoke; clear-pending.
- [ ] Telegram onboarding happy path with the external setup service mocked at
  the network boundary only: start → status(ready) → apply persists the token +
  enables telegram + requests a restart.
- [ ] Native visibility: a channel enabled via the Hermes CLI/dashboard is
  visible through the XNOBrain API without sync, and vice-versa.

## 4. `make` targets & manual

- [ ] `make check` passes (backend + frontend + lint/type).
- [ ] `make test` (backend) green including the new files.
- [ ] `npm test -- ChannelsView` and `npm run build` pass.
- [ ] `make smoke-api`: the new routes respond (no 404) with the success
  envelope; smoke creates/reads **real** state, no synthetic channel records.
- [ ] `make run` boots the combined image; readiness passes with the pinned
  Hermes; no channel route regresses chat streaming, stop, approvals, telemetry,
  or 9router behavior.

### Manual end-to-end (real Telegram)

1. `make run`; open the app, select an agent, open its **Channels** page.
2. Either paste a real bot token from @BotFather into the Telegram card and set
   an allowed user id, **or** run the guided onboarding (scan the QR / open the
   deep link).
3. Enable Telegram; confirm the card shows `pending_restart`.
4. Click Restart gateway; wait for `connected`.
5. From the phone, message the bot; confirm the agent replies.
6. In the pairing panel, approve any pending code if prompted; confirm the user
   becomes approved.

Evidence: a screen recording or step log showing the reply on the phone, plus
the card transitioning disabled → pending_restart → connected.

## 5. Security check (mandatory)

- [ ] **Responses:** grep every channel/gateway/pairing/onboarding response body
  in the test suite for the known test token value → **zero** hits. Only
  `redacted_value`/`is_set` appear.
- [ ] **Logs:** run the enable/rotate/onboarding paths with log capture; assert
  the token, allowed-user values, and pairing codes never appear; only key
  **names** and error categories are logged (mirror `web_server.py` l.9570).
- [ ] **Error bodies:** force failures (invalid key, 409 multiplex, 429 pairing
  lockout); assert no secret leaks into `message`/`error`.
- [ ] **Bundles:** export a bundle for an agent with channels configured
  (`/api/brain/v1/bundles/export`, `services/portability.py`); assert the profile
  `.env` channel keys (`TELEGRAM_BOT_TOKEN`, etc.) are excluded or redacted in
  the archive. Add a regression test.
- [ ] **Traces/telemetry:** confirm no token/allowed-user/code is attached to
  OpenTelemetry spans or run events.

## 6. Acceptance-criteria checklist

- [ ] Phase-0 compatibility test passes against the pinned Hermes commit
  (recorded in `docs/development.md`).
- [ ] Every route in `implementation.md` §Phase 5 has success, validation (422),
  not-found (404), and — where applicable — conflict (409) / lockout (429)
  coverage.
- [ ] A channel enabled + credentialed through XNOBrain is visible to the
  native Hermes dashboard and `hermes` CLI without sync; and vice-versa.
- [ ] Enabling a port-binding channel on a secondary agent under multiplex is
  rejected with Hermes' guidance; client-type channels work per agent.
- [ ] Gateway start/stop/restart/drain and status work through the public
  gateway path; XNOBrain never re-implements dispatch/session/delivery.
- [ ] Every credential mutation snapshots `.env` + `config.yaml` before writing.
- [ ] No raw token/allowed-user/pairing code appears in any response, log,
  error body, trace, or exported bundle (all of §5 green).
- [ ] No new database, second API process, XNOBrain credential file, or copied
  Hermes internals were introduced.
- [ ] Manual Telegram round-trip succeeds (message → reply) with the card
  reaching `connected`.
- [ ] `make check`, `make test`, and `make smoke-api` pass; `make run` boots
  clean with no regression to existing features.
- [ ] Docs updated: `docs/architecture.md`, `docs/api.md`, `docs/development.md`
  (pinned Hermes version, channel data ownership, gateway lifecycle, multiplex
  rule, routes, troubleshooting). OpenAPI stays runtime-generated (no static
  `docs/openapi.yaml`).
</content>
