# Vendored Hermes platform plugin: Zalo Bot

This directory is a vendored copy of the third-party `hermes-plugin-zalo`
Hermes platform plugin (Zalo Bot API adapter). It is bundled with the Runtime
so a managed workspace can expose a `zalo_bot` platform next to the XNOBrain
personal-account `zalo` plugin in `../zalo/`.

- Upstream repository: https://github.com/moken627-hub/hermes-plugin-zalo
- Pinned commit: `e3cdc3e9bd85d2d2bffc77207253852e97c7ef0d`
- Upstream version: `2.2.2` (kept in `plugin.yaml`)
- License: MIT, Copyright (c) 2026 Moken (see `LICENSE`)
- Date vendored: 2026-11-09

Only these upstream files were taken: `zalo/__init__.py`, `zalo/adapter.py`,
`zalo/group_manager.py`, `zalo/plugin.yaml`, and the repository `LICENSE`.
The upstream root `__init__.py`, `examples/`, `.github/`, and `docs/` were not
vendored.

## Local modifications

Everything except the changes below is byte-identical to the pinned upstream
commit. The local patches under "Local patches" below also modify behavior;
the rest of the networking, authentication, pairing, admin, anti-spam,
history, and webhook logic is unchanged.

1. Platform key and label
   - `adapter.py`: `register()` now registers `name="zalo_bot"` and
     `label="Zalo Bot"`; the adapter builds `Platform("zalo_bot")`; the
     configuration example and the pairing hints name the `zalo_bot` platform.
   - `plugin.yaml`: `name: zalo_bot`, `label: Zalo Bot`. `kind`, `version`,
     `repository`, and `author` are unchanged.
2. Environment variable namespace (`ZALO_` -> `ZALO_BOT_`, except the shared
   token variable `ZALO_BOT_TOKEN`)
   - `ZALO_ALLOWED_USERS` -> `ZALO_BOT_ALLOWED_USERS`
   - `ZALO_ALLOW_ALL_USERS` -> `ZALO_BOT_ALLOW_ALL_USERS`
   - `ZALO_HOME_CHANNEL` -> `ZALO_BOT_HOME_CHANNEL` (also
     `cron_deliver_env_var`)
   - `ZALO_HOME_CHANNEL_NAME` -> `ZALO_BOT_HOME_CHANNEL_NAME`
   - `ZALO_CRM_DIR` -> `ZALO_BOT_CRM_DIR`
   - `ZALO_HISTORY_DIR` -> `ZALO_BOT_HISTORY_DIR`
   - `ZALO_SETTINGS_DIR` -> `ZALO_BOT_SETTINGS_DIR`
   - `ZALO_DM_POLICY` -> `ZALO_BOT_DM_POLICY` (bot-only variable, prefixed for
     consistency with the rest of the namespace)
3. Default data directories are prefixed to avoid sharing state with the
   personal plugin: `<HERMES_HOME>/zalo-bot-crm`, `zalo-bot-history`, and
   `zalo-bot-settings`. The matching `plugin.yaml` descriptions were updated.
4. Interactive setup removed. `interactive_setup()` and its local
   `hermes_cli.setup` imports are deleted, and `register()` no longer passes
   `setup_fn`. Credentials come from `ZALO_BOT_TOKEN` in the environment, which
   the platform supplies; there are no interactive prompts.
   `_resolve_zalo_secret()` and its lazy `agent.secret_scope` import remain
   because they resolve the token for the adapter, requirement checks, and
   cron delivery outside the setup flow.

## Local patches

Security hardening applied on top of the vendored upstream code on 2026-11-09:

1. H1 — Group admin authorization fails closed.
   - `adapter.py`: new `_is_live_group_admin()` performs a live
     `getGroupInfo` member-role lookup; any lookup error, malformed response,
     missing member entry, or non-`owner`/`admin` role denies. The previous
     allowlist fallback and the `chat_type == "GROUP"` bypass are removed, so
     admin-only commands (`/kick`, `/promote`, `/poll`, `/pin`, `/noi-quy`, and
     local settings commands) always require a live platform role.
2. H2 — Bot token redaction in logs and errors.
   - `adapter.py`: `httpx` and `httpcore` loggers are raised to `WARNING` at
     adapter/client init so request URLs (which embed `/bot{token}`) are not
     logged at INFO. `_ZaloClient._post()` scrubs the token from exception
     text and from parsed API data; `connect()` scrubs the token from
     verification-failure logs and fatal-error messages.
3. H3 — DM policy enforcement and pairing hardening.
   - `adapter.py`: `dm_policy` only accepts `disabled`, `allowlist`, or
     `pairing`; any other value (including legacy `open`) is coerced to
     `disabled`. `_passes_allowlist()` denies under `allowlist` when no
     allowlist is configured and denies every DM under `disabled`.
   - Pairing captcha state is keyed by sender user id instead of chat id,
     wrong answers never echo the expected code, attempts are capped at
     `PAIRING_MAX_ATTEMPTS` (5) followed by a 15-minute lockout, and
     `_pending_approvals` is bounded to `MAX_PENDING_APPROVALS` (500) with the
     oldest entry evicted.
4. M1 — Config type coercion.
   - `adapter.py`: `_coerce_bool()` treats `"false"`/`"0"`/`"no"`/`"off"` as
     false and `_coerce_allowed_users()` comma-splits a string allowlist
     instead of iterating it character by character.
5. `_dm_policy` alias.
   - `adapter.py`: `self._dm_policy` is set to the normalized `dm_policy` at
     construction because Hermes gateway authorization reads the alias.
6. `plugin.yaml` optional environment.
   - Added `ZALO_BOT_DM_POLICY` with the supported values and default.
7. M2 — Group authorization before side effects.
   - `adapter.py`: group messages are checked with `_group_member_allowed()`
     before slash-command handling, anti-spam tracking, name-trigger replies,
     and history writes. Name-trigger auto-replies, history writes, and
     join/member-joined welcome sends require the user to be explicitly
     authorized by the adapter (allow-all or a configured allowlist), because
     those paths never reach Hermes gateway authorization. Agent delivery still
     defers to Hermes core when no adapter allowlist exists, so legitimate
     group messages are not dropped.
8. M3 — Bounded dedupe on real provider IDs.
   - `adapter.py`: long-polling and webhook delivery pass the provider
     `message_id`/`update_id` into one bounded, ordered `_seen_message_ids`
     set (`MAX_SEEN_MESSAGE_IDS`). Duplicate delivery is dropped before any side
     effect. A missing provider ID is never replaced with a timestamp; the
     message is preserved and simply not deduped.
   - `_dispatch_message()` now forwards the real message ID in `MessageEvent`
     instead of `int(time.time() * 1000)`.
9. M4 — Untrusted channel content separation.
   - `adapter.py`: tracked group history is tagged
     `trust: untrusted_external`; sender names and chat history remain separate
     metadata and are never concatenated into agent instructions.
10. M5 — Webhook dedupe parity.
    - `adapter.py`: webhook mode reuses `_handle_incoming()` and the same
      bounded dedupe set as polling, including `update_id` when the message
      itself carries no ID.

Focused regression tests live in
`xnobrain/tests/test_zalo_bot_plugin_hardening.py`.

## Update policy

Update this plugin only by re-vendoring a reviewed upstream commit into this
directory and recording the new commit SHA above. Never fetch plugin source at
image build time, container start, or runtime.

- DM denial is logged at INFO with the sender ID only (no text), so an operator can find the ID to allowlist.
