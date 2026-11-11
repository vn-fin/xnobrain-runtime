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
`zalo/plugin.yaml`, and the repository `LICENSE`. The upstream root
`__init__.py`, `group_manager.py`, `examples/`, `.github/`, and `docs/` were
not kept.

## Local modifications

Everything except the changes below is byte-identical to the pinned upstream
commit. The local patches under "Local patches" below also modify behavior;
the rest of the networking, authentication, pairing, moderation, CRM, and
webhook logic is unchanged. The upstream chat-history sync is replaced by the
local conversation store in patch 13 below, and the undocumented group
management API is removed in patch 16.

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
   - `ZALO_SETTINGS_DIR` -> `ZALO_BOT_SETTINGS_DIR`
   - `ZALO_DM_POLICY` -> `ZALO_BOT_DM_POLICY` (bot-only variable, prefixed for
     consistency with the rest of the namespace)
3. Default data directories are prefixed to avoid sharing state with the
   personal plugin: `<profile home>/zalo-bot-crm` and
   `<profile home>/zalo-bot-settings`. The conversation store does not use an
   environment variable: it always lives below the profile home that owns the
   bot (see patch 13). The matching `plugin.yaml` descriptions were updated.
   `ZALO_BOT_CRM_DIR` and `ZALO_BOT_SETTINGS_DIR` still override these paths;
   the profile-scoped default resolution is described in patch 14.
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
   - `adapter.py`: `_is_live_group_admin()` never grants a role. Any
     admin-only adapter command is denied with no allowlist fallback and no
     `chat_type == "GROUP"` bypass. Since B3 (patch 16) it returns `False`
     directly because the Bot API has no role lookup.
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
   - The upstream adapter-level 6-digit pairing captcha is removed: under
     `pairing`, unknown DM senders go straight to Hermes core pairing, and
     the owner approves them from the Runtime channel pairing API.
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
     before slash-command handling, anti-spam tracking, and name-trigger
     replies. Name-trigger auto-replies and join/member-joined welcome sends
     require the user to be explicitly authorized by the adapter (allow-all or
     a configured allowlist), because those paths never reach Hermes gateway
     authorization. Agent delivery still defers to Hermes core when no adapter
     allowlist exists, so legitimate group messages are not dropped. Group
     conversation storage is intentionally not gated per sender (patch 13).
8. M3 — Bounded dedupe on real provider IDs.
   - `adapter.py`: long-polling and webhook delivery pass the provider
     `message_id`/`update_id` into one bounded, ordered `_seen_message_ids`
     set (`MAX_SEEN_MESSAGE_IDS`). Duplicate delivery is dropped before any side
     effect. A missing provider ID is never replaced with a timestamp; the
     message is preserved and simply not deduped.
   - `_dispatch_message()` now forwards the real message ID in `MessageEvent`
     instead of `int(time.time() * 1000)`.
9. M4 — Untrusted channel content separation.
   - `adapter.py`: stored conversation records are tagged
     `trust: untrusted_external`; sender names and chat content remain separate
     metadata and are never concatenated into agent instructions.
10. M5 — Webhook dedupe parity.
    - `adapter.py`: webhook mode reuses `_handle_incoming()` and the same
      bounded dedupe set as polling, including `update_id` when the message
      itself carries no ID.
11. Group slash-command and empty-text handling.
    - `adapter.py`: after the bot mention is stripped in a group, a remaining
      adapter `SLASH_COMMANDS` command is still handled, but any other text
      that starts with `/` is dropped because Hermes slash commands are
      DM-only. Empty text after mention stripping is also dropped so a group
      message can never fall back to the native gateway agent.
12. Per-profile runtime connection state.
    - `adapter.py`: `connect()`, the polling loop, the webhook keep-alive, and
      `disconnect()` atomically write `<profile>/zalo_bot_state.json` (mode
      `0600`, temp file plus `os.replace`) with only `state`
      (`connecting`/`connected`/`disconnected`), `updated_at`, and `pid`.
      `get_hermes_home()` resolves the profile home at connect time, so under
      `gateway.multiplex_profiles` each profile writes its own file. The
      polling and webhook loops refresh `connected` every 30 seconds while
      healthy. Write failures are logged at debug level and never interrupt
      the adapter. The file deliberately holds no token, bot ID, or user
      content; the Runtime channels service reads it for `connected` status.
13. B1 — Conversation store from connect time (plan 020).
    - Live check (2026-11-11): Zalo only delivers group messages that
      mention the bot, so group files contain tagged messages and bot
      replies, not every member message. DMs are stored completely.
    - `adapter.py`: the upstream buffered `_ChatHistorySync` store and its
      `ZALO_BOT_HISTORY_DIR` variable are removed and replaced by
      `_BotHistoryStore`. One JSONL file per chat is written to
      `<profile home>/zalo_bot/history/<chat_id>.jsonl`, where `<profile home>`
      is resolved with `get_hermes_home()` at connect time. The `zalo_bot` and
      `history` directories are `0700`; each file is created `0600` with
      `os.open(O_WRONLY|O_APPEND|O_CREAT)`. Writes through a symlinked directory
      or file are skipped. Each message is one JSON line written immediately
      (no buffer), `ensure_ascii=False`, with `message_id`, `chat_id`,
      `chat_type` (`dm`/`group`), `direction` (`in`/`out`), `sender_id`,
      `sender_name`, `type` (`text`/`image`/`sticker`/`unsupported`), `text`
      (truncated to 4000 chars), `ts`, `trust: untrusted_external`, and
      `media_url` for images/stickers only (URL, never downloaded). Storage
      failures are logged at debug level without content and never interrupt
      message handling.
    - Inbound scope: every member's message is stored in a group approved for
      `zalo_bot` by Runtime's `<profile home>/zalo/groups.json`, unless the
      group's `follow` setting is off; storage happens before per-sender
      authorization, anti-spam, and mention gating so a summary is complete.
      DMs are stored only for senders allowed by `dm_policy` (allowlist, allow
      all, or an owner-approved gateway pairing). Slash commands and the bot's
      own messages are never stored. Outbound replies are stored with
      `direction: out` after a successful send in approved followed groups or
      in chats that are already in scope.
    - `/follow` is now a per-group opt-out: it defaults to on for approved
      groups, and the command reply says whether history storage is on or off.
    - Retention: `extra.history_retention_days` (default `30`, clamped to
      `1..365`) is applied at the end of a successful `connect()` and then once
      per day from a background task cancelled by `disconnect()`
      (`HISTORY_TRIM_INTERVAL_SECONDS = 86400`). Trimming drops records older
      than the window (malformed lines too) with an atomic temp-file +
      `os.replace` rewrite, deletes an emptied file, and then enforces a 50 MB
      per-profile cap by deleting whole chat files oldest mtime first.
    - Runtime deletes the stored history when it revokes or denies a group,
      logs the channel out, or revokes a `zalo_bot` pairing.
14. Profile-scoped settings and CRM directories (plan 020 review fix).
    - `adapter.py`: `_GroupSettings` and `_CRMContacts` no longer resolve
      `ZALO_BOT_SETTINGS_DIR` / `ZALO_BOT_CRM_DIR` (or a default below
      `get_hermes_home()`) at import time, which made every multiplexed profile
      share one directory. Both keep their environment override when set and
      otherwise resolve `<profile home>/zalo-bot-settings` and
      `<profile home>/zalo-bot-crm` lazily from the adapter's
      `_resolve_state_home()` (falling back to `get_hermes_home()`) and rebuild
      those objects at `connect()`. Directories are created on first write with
      mode `0700`; settings and CRM files are written atomically with mode
      `0600`.
15. B2 — Standalone Bot API helper for Runtime tools (plan 020).
    - `adapter.py`: `_ZaloClient` gains `send_sticker()` and `send_voice()`.
      `send_voice()` posts `{"chat_id", "voice_url"}` (the field name verified
      against the live API on 2026-11-11; `voice`, `url`, `audio`, and
      `audio_url` are rejected). The module function
      `_standalone_api(pconfig, action, **kwargs)` exposes a fixed allowlist
      (`get_me`, `send_photo`, `send_sticker`, `send_voice`) for the Runtime
      tool layer. It resolves the token the same way as `_standalone_send`,
      closes the client, is never allowed to fall back to text, and returns
      content-free errors so no token or message content is echoed.
16. B3 — Undocumented group endpoints removed (2026-11-11).
    - Verified against the live Zalo Bot API (dev bot, fake group ids, `POST
      https://bot-api.zaloplatforms.com/bot<token>/<method>`, 2026-11-11).
      Every group endpoint and `getWebhookInfo` returns `{"ok": false,
      "error_code": 404, "description": "Not Found"}`, identical to a made-up
      method name. Bots also cannot be group admins.
    - Confirmed API surface: `getMe` ok; `sendPhoto {chat_id, photo}` and
      `sendSticker {chat_id, sticker}` accept their fields; `sendVoice` needs
      `voice_url`; a fake chat id returns `410 "chat_id is invalid"`.
    - Removed from `adapter.py`: the `_GroupManager` class and
      `self._group_manager`; every endpoint wrapper (`createGroup`,
      `getGroupInfo`, `listGroupMembers`, `kickGroupMember`,
      `promoteGroupAdmin`, `demoteGroupAdmin`, `inviteGroupMember`,
      `setGroupAnnouncement`, `createPoll`, `pinGroupMessage`,
      `unpinGroupMessage`, `muteGroup`, `unmuteGroup`, `setGroupName`,
      `setGroupAvatar`, `getGroupInvites`, `getGroupLink`, `enableGroupLink`,
      `disableGroupLink`); `_ZaloClient.get_webhook_info()`; and the slash
      commands that only called them: `/info`, `/kick`, `/promote`, `/demote`,
      `/invite`, `/poll`, `/pin`, `/unpin`, `/noi-quy`, `/mute`, `/unmute`.
      Menu and `/huong-dan` help text no longer list them.
    - `_is_live_group_admin()` now returns `False` directly. Local admin-only
      commands (`/warn`, `/unwarn`, `/report`, `/silent`, `/welcome`,
      `/welcome-text`, `/follow`, `/name-trigger`) remain registered so their
      code is kept, but they stay denied because the API cannot prove a
      platform role.
    - `group_manager.py` is deleted from this vendored directory.

Focused regression tests live in
`xnobrain/tests/test_zalo_bot_plugin_hardening.py`,
`xnobrain/tests/test_zalo_bot_history_store.py`, and
`xnobrain/tests/test_zalo_bot_tools.py`.

## Update policy

Update this plugin only by re-vendoring a reviewed upstream commit into this
directory and recording the new commit SHA above. Never fetch plugin source at
image build time, container start, or runtime.

- DM denial is logged at INFO with the sender ID only (no text), so an operator can find the ID to allowlist.
