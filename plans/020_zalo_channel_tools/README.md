# 020 — Zalo channel tools (personal account and Zalo Bot)

Status: P-A and P-B done (2026-11-12); Track B done (B1/B3 done, B2 done 2026-11-14).

Remaining:
- Inbound media download (needs plan 019 attachment forwarding).
- Live verification against QR-connected personal Zalo and Zalo Bot accounts.
- `getGroupChatHistory` paging (zca-js 2.2.0 accepts `(groupId, count)` only).

Builds on `plans/015_zalo_personal_channel/` (bridge, adapter, gateway hosting)
and `plans/019_gateway_channel_turns/` (channel turns run in the main Runtime
process). Today the agent has only `xnobrain_integration_connect` and
`send_channel_message` (personal Zalo, DM, text). This plan gives the agent
tools to read and act on Zalo conversations, so it can, for example, summarize
a group chat or send a report into a group.

Three tracks:

| Track | Goal |
|---|---|
| **P-A** Personal, step 1 | Feature parity with OpenClaw's `zalouser` plugin |
| **P-B** Personal, step 2 | Full toolset that zca-js can support, including history |
| **B** Zalo Bot | Store conversations from connect time, plus tools on top of that store |

## Verified facts (2026-11-11)

| Fact | Where |
|---|---|
| Agent tools for Zalo: `xnobrain_integration_connect`, `send_channel_message` (platform `zalo` only, DM, text ≤ 2000, confirmation in chat per send) | `xnobrain/integrations/channel_tools.py`, `integration_connect_tools.py` |
| Personal bridge handles DMs only; group messages are dropped | `runtime/hermes-plugins/platforms/zalo/bridge/bridge.js` (header, `ThreadType.User` checks) |
| Bridge endpoints: `/health`, `/login`, `/messages`, `/send`; inbound history `history.jsonl`, 30-day retention | `bridge.js`, `bridge/lib.js` |
| Runtime reaches the bridge on `127.0.0.1` (gateway shares the Runtime network namespace) | `docker-compose.dev.yaml` `runtime-gateway.network_mode`, `zalo_channels.bridge_health` |
| zca-js 2.2.0 exposes `getGroupChatHistory(groupId, count)` → `{groupMsgs, more, lastActionId}`, `listener.requestOldMessages`, `getAllGroups`, `getAllFriends`, `getGroupInfo`, `getGroupMembersInfo`, ~150 methods | `bridge/node_modules/zca-js/dist/apis/` |
| Channel turns auto-deny every approval request | plan 019, "Approval-gated tools are non-interactive" |
| Zalo Bot official API: `getMe`, `getUpdates`, `setWebhook`, `deleteWebhook`, `getWebhookInfo`, `testWebhook`, `sendMessage`, `sendPhoto`, `sendSticker`, `sendVoice` (DM only, `.aac` URL), `sendChatAction`. No history method, no group management | Zalo Bot Platform docs (context7 mirror) |
| Vendored bot adapter calls ~17 group endpoints absent from the docs (`kickGroupMember`, `createPoll`, `pinGroupMessage`, …) | `runtime/hermes-plugins/platforms/zalo_bot/adapter.py`, `group_manager.py` |
| Bot history `_ChatHistorySync` writes only after 500 buffered messages, only for `/follow` groups, only authorized senders; nothing reads it | `zalo_bot/adapter.py` (`_ChatHistorySync`, step 6 in group handling) |
| Group approval with daily turn limit exists for `zalo_bot` only | `xnobrain/services/channels.py` (`_group_platform`, `approve_group`) |
| `zalo_bot` registers a `standalone_sender_fn` | `zalo_bot/adapter.py` `register()` |

OpenClaw `zalouser` (reference, docs only, not code-verified): agent tool
`zalouser` with actions `send`, `image`, `link`, `friends`, `groups`, `me`,
`status`; channel supports groups with mention gating, inbound/outbound media,
reactions, typing and seen receipts, 2000-char chunking, multi-account. It has
no history-reading tool.

## Shared decisions

### D1 — Tools live in Runtime, one module per platform

- Add `xnobrain/integrations/zalo_tools.py` (personal) and
  `xnobrain/integrations/zalo_bot_tools.py` (bot), registered in toolset
  `xnobrain_channels` and bound per run the same way as
  `channel_tools.bind_run` (feature flag `FEATURE_AGENT_CHANNELS`, plugin
  registered, platform enabled in the profile).
- One tool per action with a strict JSON schema
  (`additionalProperties: false`). No generic "call any method" tool;
  zca-js `custom` is never exposed.
- Errors return the existing `{"success": false, "error": {"code": "channel_*"}}`
  envelope with content-free codes.

### D2 — Personal: bridge is the only zca-js caller

- New bridge endpoints, one per tool family (below). Runtime calls them with
  `urllib` like `bridge_health`.
- New endpoints expose message content, so the bridge requires a bearer token:
  random, generated at bridge start, written to `<HERMES_HOME>/zalo/bridge.token`
  (`0600`), read by Runtime per request. `/health` stays open.
- The bridge validates every argument (thread IDs `^[0-9]{1,32}$`, counts,
  lengths) before calling zca-js.

### D3 — Approval policy

| Class | Examples | Web run | Channel-originated run |
|---|---|---|---|
| Read | list chats, read history, group info | no approval | **current thread only** |
| Send / react | send text, image, link, react | no approval card; the agent confirms the recipient and exact payload in chat | **current thread only** |
| Group utility | poll, note, reminder, pin | no approval card; in-chat confirmation | denied |
| Admin | add/remove member, roles, settings | no approval card; in-chat confirmation, warning text | denied |
| Never exposed | delete chat, disperse group, bank, profile, cookies | — | — |

No Zalo or Zalo Bot tool shows an approval card: before calling a send or
group-settings tool the agent shows the user the recipient (or group) and the
exact payload (or new values) and waits for explicit confirmation in chat;
every group must still be approved first.

The channel-turn restriction matters: every channel turn runs with the
owner's authority (019), so without it any paired Zalo user could ask the bot
to read another chat's history or send into another chat.

### D4 — External content is data

- Every message returned to the model is wrapped as
  `{"trust": "untrusted_external", "sender_id", "sender_name", "text", "ts", "type"}`
  inside a tool result, never in the system prompt.
- Caps: `limit` ≤ 200 messages per call, text truncated per message (2000
  chars), total result ≤ 64 KB.
- No message text or sender names in logs or traces (same rule as 019's log
  filter).

### D5 — Scope of readable chats

- Personal: DMs from senders allowed by `dm_policy`, and groups the owner has
  approved. Extend the existing group approval service from `zalo_bot` to
  `zalo` (`_group_platform` accepts both). Unapproved groups are refused with
  `channel_group_not_approved`.
- Bot: same rule, using the existing `zalo_bot` group approval.

### D6 — Rate limiting (personal account ban risk)

- Bridge-side token bucket per account: reads 30/min, sends 20/min,
  `getGroupChatHistory` 10/min. Over the limit → `channel_rate_limited`.
- Cache list results (friends, groups) for 5 minutes.

## Track P-A — Personal, OpenClaw parity

Channel behavior:

1. **Group turns**: bridge forwards group messages; adapter applies mention
   gating (`requireMention` default true, quoting the bot counts) and the
   group approval + daily limit (extend D5). Skipped group messages are kept
   (max 50) and attached to the next processed message, like OpenClaw.
2. **Inbound media**: images/files/videos downloaded after sender and mention
   checks, size capped by `extra.media_max_mb` (default 10); failed download →
   "attachment unavailable" note. Requires 019 to forward attachments; until
   then, media is text-noted only.
3. **Typing and seen**: `sendTypingEvent` before a reply, `sendSeenEvent` /
   `sendDeliveredEvent` after processing (best effort).
4. **Outbound chunking**: replies over 2000 chars split into chunks.

Tools:

| Tool | Bridge endpoint | zca-js |
|---|---|---|
| `zalo_status` | `GET /health` | — |
| `zalo_me` | `GET /me` | `fetchAccountInfo`, `getOwnId` |
| `zalo_list_friends` | `GET /friends` | `getAllFriends` |
| `zalo_list_groups` | `GET /groups` | `getAllGroups`, `getGroupInfo` |
| `send_channel_message` (+ `thread_type: dm\|group`) | `POST /send` | `sendMessage` |
| `zalo_send_image` | `POST /send-media` | `uploadAttachment` + `sendMessage` |
| `zalo_send_link` | `POST /send-link` | `sendLink` |
| `zalo_react` | `POST /react` | `addReaction` |

Done when: an approved group can talk to the agent with mention gating; the
agent can list friends and groups and send text, image and link to a DM or an
approved group after the user confirms in chat, within the current thread for
channel runs; unit tests and bridge tests cover each endpoint.

## Track P-B — Personal, full toolset

Read (the part needed for summaries and reports, ship first):

| Tool | zca-js / source |
|---|---|
| `zalo_list_conversations` | `getAllGroups`, `getAllFriends`, `getPinConversations`, `getArchivedChatList` |
| `zalo_read_history` (DM) | `history.jsonl` + `listener.requestOldMessages(User)` |
| `zalo_read_group_history` | `getGroupChatHistory(groupId, count)`, paging via `more` |
| `zalo_search_messages` | search over stored history (zca-js has no search) |
| `zalo_group_info` | `getGroupInfo`, `getGroupMembersInfo` |
| `zalo_user_info` | `getUserInfo`, `findUser`, `findUserByUsername` |
| `zalo_unread` | `getUnreadMark` |
| `zalo_list_reminders`, `zalo_list_notes` | `getListReminder`, `getListBoard` |
| `zalo_poll_detail` | `getPollDetail` |

Bridge also starts storing outbound and group messages in `history.jsonl`
(per thread, 30-day retention, `0600`) so search covers both sides.

Send:

| Tool | zca-js |
|---|---|
| quote reply and @mention (fields on `send_channel_message`) | `sendMessage` with `quote`, `mentions` |
| `zalo_send_file`, `zalo_send_video`, `zalo_send_voice` | `uploadAttachment`, `sendVideo`, `sendVoice` |
| `zalo_send_sticker` | `searchSticker`, `sendSticker` |
| `zalo_send_contact_card` | `sendCard` |
| `zalo_forward` | `forwardMessage` |
| `zalo_undo_message` (own messages only) | `undo` |

Group utilities: `zalo_create_poll` (`createPoll`, `addPollOptions`,
`lockPoll`), `zalo_create_note` (`createNote`, `editNote`),
`zalo_create_reminder` (`createReminder`, `editReminder`, `removeReminder`),
`zalo_pin_conversation`, `zalo_mute`, `zalo_labels`, `zalo_mark_unread`.

Admin (approval with warning; only in groups where the account is admin):
`zalo_group_members` (`addUserToGroup`, `removeUserFromGroup`),
`zalo_group_settings` (`changeGroupName`, `changeGroupAvatar`,
`updateGroupSettings`), `zalo_group_roles` (`addGroupDeputy`,
`removeGroupDeputy`), `zalo_group_pending` (`getPendingGroupMembers`,
`reviewPendingMemberRequest`), `zalo_group_link`, `zalo_create_group`,
`zalo_friend_requests`, `zalo_friend_alias`, `zalo_quick_reply`.
`changeGroupOwner` is left out (irreversible).

Never exposed: `deleteChat`, `deleteMessage` (others'), `disperseGroup`,
`leaveGroup`, `removeFriend`, `blockUser`, bank and catalog APIs, profile and
account settings, `getCookie`, `getContext`, `getListDevice`,
`setHiddenConversations`, `updateAutoDeleteChat`, `custom`.

Order inside P-B: read tools → send extras → group utilities → admin. Each
group is its own PR.

Done when: the agent can summarize the last N messages of an approved group
(including messages from before the bridge connected, as far as
`getGroupChatHistory` reaches) and every tool above has a schema test, a
bridge validation test and a send-scope or approval test as applicable.

### P-B progress

- **Phase 1 — read tools: done 2026-11-12.** Tool -> bridge endpoint -> zca-js:
  `zalo_list_conversations` -> `GET /conversations` -> `getAllFriends`,
  `getAllGroups`, `getGroupInfo`, `getPinConversations`,
  `getArchivedChatList`; `zalo_read_history` -> `POST /history` ->
  `history.jsonl` + `listener.requestOldMessages(User)`;
  `zalo_read_group_history` -> `POST /group-history` ->
  `getGroupChatHistory`; `zalo_search_messages` -> `POST /search` -> stored
  history; `zalo_group_info` -> `POST /group-info` -> `getGroupInfo`,
  `getGroupMembersInfo`; `zalo_user_info` -> `POST /user-info` ->
  `getUserInfo`, `findUser`, `findUserByUsername`; `zalo_unread` ->
  `GET /unread` -> `getUnreadMark`; `zalo_list_reminders` ->
  `POST /reminders` -> `getListReminder`; `zalo_list_notes` ->
  `POST /notes` -> `getListBoard`; `zalo_poll_detail` ->
  `POST /poll-detail` -> `getPollDetail`.
- The bridge now stores inbound and outbound direct and group messages in
  `history.jsonl` (30-day retention, `0600`, trimmed at start and every six
  hours) and enforces the read bucket (30/min) plus a
  `getGroupChatHistory` bucket (10/min).
- Deferred: `getGroupChatHistory` paging. zca-js 2.2.0 accepts only
  `(groupId, count)`, so the tool reports `more` and `last_action_id` but cannot
  request the next page.
- **Phase 2 — send extras: done 2026-11-12.** Tool -> bridge endpoint ->
  zca-js: quote reply and @mention -> `POST /send` ->
  `sendMessage` (`quote`, `mentions`); `zalo_send_file` -> `POST /send-media`
  (`kind: file`) -> `sendMessage` attachment + `uploadAttachment` ("others");
  `zalo_send_video` -> `POST /send-media` (`kind: video`) -> `sendMessage`
  attachment + `uploadAttachment` ("video", MP4 only); `zalo_send_voice` ->
  `POST /send-media` (`kind: voice`) -> `sendMessage` attachment (delivered as
  an audio file, see below); `zalo_send_sticker` -> `POST /send-sticker` ->
  `searchSticker`, `sendSticker`; `zalo_send_contact_card` -> `POST /send-card`
  -> `sendCard`; `zalo_forward` -> `POST /forward` -> `forwardMessage`;
  `zalo_undo_message` -> `POST /undo` -> `undo`.
  - Quote and mention ids are resolved from live listener messages and, for
    groups, from `getGroupChatHistory`; the bridge resolves the quoted message
    inside the target thread only, and mention positions are computed from
    `@DisplayName` occurrences in the text, so a send never rewrites it.
  - `zalo_bot` rejects `quote_message_id` and `mentions` with
    `channel_field_not_supported`.
  - Deviations from the table: `sendVideo`/`sendVoice` need a URL that Zalo's
    servers fetch, so a workspace file goes through `sendMessage` with an
    attachment instead; a voice recording therefore arrives as an audio file
    attachment, not a recorded voice note. `forwardMessage` forwards text only,
    so a media-only message is refused. Undo is limited locally to messages
    this account sent within 24 hours and needs the message's `cliMsgId`, which
    the bridge keeps from live events (or group history).
  - The bridge now stores outbound image, file, video, voice, sticker, card,
    and forward messages in `history.jsonl` and reads the attachment message id
    from the send response.
- **Phase 3 (group utilities): done 2026-11-12.** Tool -> bridge endpoint ->
  zca-js: `zalo_create_poll` -> `POST /poll-create` -> `createPoll`;
  `zalo_create_note` -> `POST /note-create` -> `createNote`;
  `zalo_create_reminder` -> `POST /reminder-create` -> `createReminder`;
  `zalo_pin_conversation` -> `POST /pin` -> `setPinnedConversations`;
  `zalo_mute` -> `POST /mute` -> `setMute`; `zalo_labels` -> `GET /labels` ->
  `getLabels`, and `POST /labels` -> `getLabels` + `updateLabels`;
  `zalo_mark_unread` -> `POST /unread-mark` -> `addUnreadMark` /
  `removeUnreadMark`.
  - All seven are always refused in a channel-originated run
    (`channel_thread_out_of_scope`); polls and notes are group-only, while
    reminders, pin, mute, labels, and unread marks accept an approved group or
    a direct message allowed by `dm_policy`. Every write uses the bridge send
    bucket, and no poll text, note title, reminder title, or label name is
    logged.
  - zca-js 2.2.0 `createNote` accepts a title and `pinAct` only, so
    `zalo_create_note` has no note body. `updateLabels` replaces the whole
    label list, so the bridge re-reads labels, changes only the requested
    label's `conversations` (tokens `u<id>`/`g<id>`, matching the pin API's
    conversation form), and sends the read `version` back.
  - Not in this phase: `addPollOptions`, `lockPoll`, `editNote`, `editReminder`,
    and `removeReminder` are not exposed.
- **Phase 4 (admin): done 2026-11-12.** Tool -> bridge endpoint -> zca-js:
  `zalo_group_members` -> `POST /group-members` -> `getGroupInfo`,
  `getGroupMembersInfo`, `addUserToGroup`, `removeUserFromGroup`;
  `zalo_group_settings` -> `POST /group-settings` -> `changeGroupName`,
  `updateGroupSettings`; `zalo_group_roles` -> `POST /group-roles` ->
  `addGroupDeputy`, `removeGroupDeputy`; `zalo_group_pending` ->
  `POST /group-pending` -> `getPendingGroupMembers`,
  `reviewPendingMemberRequest`; `zalo_group_link` -> `POST /group-link` ->
  `getGroupLinkDetail`, `enableGroupLink`, `disableGroupLink`;
  `zalo_create_group` -> `POST /create-group` -> `createGroup`;
  `zalo_friend_requests` -> `POST /friend-requests` ->
  `getSentFriendRequest`, `getFriendRecommendations` (only `recommType` 2,
  received requests), `acceptFriendRequest`, `rejectFriendRequest`,
  `sendFriendRequest`, `undoFriendRequest`; `zalo_friend_alias` ->
  `POST /friend-alias` -> `getAliasList`, `changeFriendAlias`,
  `removeFriendAlias`; `zalo_quick_reply` -> `POST /quick-reply` ->
  `getQuickMessageList`, `addQuickMessage`, `updateQuickMessage`,
  `removeQuickMessage`.
  - All nine tools are always refused in a channel-originated run
    (`channel_thread_out_of_scope`), group actions require an owner-approved
    group, member and join-request writes take 1-20 ids, lists are capped at
    100, reads use the read bucket, writes use the send bucket, and every write
    description demands in-chat confirmation with a warning that it affects
    real people or the account and may not be reversible. No names, ids, or
    quick-reply text are logged.
  - `updateGroupSettings` writes every supported key, so the bridge merges the
    requested keys over the group's current settings; only the keys the caller
    passed change. `addMemberOnly` is not part of zca-js 2.2.0's options.
  - Skipped: `changeGroupAvatar` (avatar changes stay unexposed, per the
    never-exposed list in this plan). zca-js 2.2.0 has no API that lists
    received friend requests directly, so `list_received` filters
    `getFriendRecommendations` to `recommType 2`. `createGroup` keeps its
    zca-js behaviour of naming the group from `name` only, with no avatar.
  - **Track P-B is complete (2026-11-12):** read tools, send extras, group
    utilities, and the personal admin tools above have a strict schema, a
    bridge validation test, and a scope/approval test.

## Track B — Zalo Bot

### B1 — Store conversations from connect time

> Verified 2026-11-11 with the dev bot: Zalo delivers a group message to a bot
> only when the message mentions the bot. Untagged group messages never reach
> the adapter, so the bot store holds complete DMs but, for groups, only
> tagged messages and the bot's replies. A full group summary needs the
> personal channel (P-B, `getGroupChatHistory`).

Replace `_ChatHistorySync` in the vendored adapter:

- Write each message as one JSONL line on receipt (no 500-message buffer) to
  `<HERMES_HOME>/zalo_bot/history/<chat_id>.jsonl`, directory `0700`, files
  `0600`, `chat_id` passed through `_safe_path_component`.
- Store from the moment the bot connects: DMs from senders allowed by
  `dm_policy`, and every message in approved groups (all members, not only
  authorized senders, so a summary is complete). Unapproved groups are never
  stored.
- Store the bot's own replies too (`direction: out`).
- Record fields: `message_id`, `chat_id`, `chat_type`, `direction`,
  `sender_id`, `sender_name`, `type`, `text`, `ts`, `trust: untrusted_external`.
  Media is stored as a type marker and URL, not downloaded.
- Retention `extra.history_retention_days` (default 30), trimmed at adapter
  start and daily; max 50 MB per
  profile (oldest chats trimmed first).
- `/follow` becomes a per-group opt-out (default on for approved groups);
  revoking a group or disconnecting the channel deletes that history.
- Document the change in `zalo_bot/VENDORED.md`.

Runtime reads the files through the shared profile volume via a new
`ChannelRepository.bot_history(profile, chat_id, since, limit)`; no adapter
IPC.

### B2 — Tools

| Tool | Source | Confirmation |
|---|---|---|
| `zalo_bot_status` | `getMe`, `getWebhookInfo` | no |
| `zalo_bot_list_chats` | stored history index + approved groups | no |
| `zalo_bot_read_history` | stored history, `since` / `limit` | no |
| `zalo_bot_search_messages` | stored history | no |
| `zalo_bot_contacts` | `_CRMContacts` data | no |
| `send_channel_message` (+ `platform: zalo_bot`) | `standalone_sender_fn` → `sendMessage` | in chat |
| `zalo_bot_send_photo` | `sendPhoto` | in chat |
| `zalo_bot_send_sticker` | `sendSticker` | in chat |
| `zalo_bot_send_voice` (DM only) | `sendVoice` | in chat |
| `zalo_bot_warn_user` | adapter warn store | in chat |
| `zalo_bot_group_settings` (follow, silent, welcome) | adapter group settings | in chat |

Sends go only to chat IDs that appear in the store or the approved group list;
in a channel-originated run they may target only that run's own thread, and
group settings may change only that run's own group. D3 and D4 apply unchanged.

### B3 — Verify the undocumented group endpoints

With a dev bot token, call each group endpoint the adapter uses. Keep those
that respond; remove the slash commands and `group_manager.py` methods for the
rest, and record the result in `VENDORED.md`.

Done when: after connecting, messages in a DM and an approved group are on
disk within one second of receipt; the agent in a web run can summarize the
last day of an approved group; a channel-originated run can read only its own
thread; tests cover storage, retention, revoke deletion, scope checks and each
tool.

## Consumers to update

- UI: approval card already renders `command` / `description`; add i18n
  labels for new `pattern_key`s. Extend the group approval screen to `zalo`
  (P-A). Show the history retention note on the channel settings card (B1).
- `docs/api.md`: group endpoints accept `zalo`.
- Release notes: new tools are behind `FEATURE_AGENT_CHANNELS`.

## Tests

Run inside the Runtime dev container:

- `pytest tests/ -k "zalo or channel"` for tools, scope, approval, storage.
- `node --test runtime/hermes-plugins/platforms/zalo/bridge/*.test.mjs` for
  bridge validation, token check and rate limits.
- Manual: QR-connected dev account (P-A, P-B) and dev bot token (B), recorded
  in `plans/VERIFICATION.md`.

## Delivery order

1. B1 + B2 read tools + B3 (no ban risk, unblocks the Zalo report demo).
2. P-A.
3. P-B read tools.
4. P-B send extras, group utilities, admin.

## Decisions from review (2026-11-11)

1. Owner approval of a group is enough consent to store every member's
   messages. No in-group announcement.
2. Retention defaults to 30 days and is configurable per agent and platform:
   `extra.history_retention_days` (1–365) in the channel config, editable on
   the channel settings card. Applies to the bot store and the personal
   `history.jsonl`.
3. In channel-originated runs, read tools are limited to the current thread
   (D3). A request for any other chat returns `channel_thread_out_of_scope`.
   Web runs by the owner can read every approved chat.

## Security fixes from the live test (2026-11-11)

The Zalo Bot live test with Big Brother found two holes. F1 must be closed
before more tools ship; F2 was dropped by product decision.

### Findings

- **S1: sends bypass approval.** After `send_channel_message` was denied or
  timed out, the agent ran `agent send --to zalo_bot:<chat> "text"` in its
  terminal and the message was delivered. The path is `runtime/agent-cli.sh` →
  `capacity_cli` → `hermes send` → `send_message_tool` → plugin standalone
  sender, with no approval. The agent had written this recipe into a
  self-authored profile skill (`skills/operations/messaging-channels`).
  XNOBrain profiles run with `approvals.mode: off`, so terminal commands are
  never gated.
- **S2: channel turns run with the full toolset.** `run_internal_turn` sends
  no toolset restriction, so a run started by any member of an approved group
  gets terminal, file, code execution, `session_search` (every session in the
  profile, and other profiles by id), memory, skills, delegation and the
  owner's MCP servers (a group turn read Notion results). The current-thread
  rule (decision 3) only covers the Zalo tools.

### Fixes

- **F1: channel turns get an allowlist (done 2026-11-12, closes S2).** `run_internal_turn`
  passes `toolsets` (`xnobrain_channels`, `web`, `vision`, `tts`, `todo`) in the
  run body. Background channel turns run the native in-process agent rather than
  the `--toolsets` CLI path, so `conversation_runner` applies the body's
  `toolsets` to the built agent and rebuilds its tool list from the registry,
  and skips the profile MCP install for that turn. Excluded: `terminal`, `file`,
  `code_execution`, `session_search`, `memory`, `skills`, `delegation`,
  `cronjob`, `kanban`, `browser`, `computer_use`, `image_gen`, every MCP
  toolset. This is the real boundary: without terminal and file tools the run
  cannot read the bot token, the bridge token or other sessions. Tests assert
  that the run body carries the allowlist for group and DM turns, and that a
  channel run's tool list excludes MCP tools.
- **F2: dropped by product decision (S1, web runs).** Sends via `agent send`
  are allowed, and the confirmation requirement moves to the skill: the agent
  shows the user the recipient and the exact payload and waits for explicit
  confirmation in chat before sending. See the replacement guard below.
- **F2 replacement: channel-origin send scope.** In a channel-originated run,
  every send tool (`send_channel_message`, the personal Zalo image, link and
  reaction tools, and the Zalo Bot photo, sticker and voice tools) may target
  only that run's own thread. The check compares the resolved thread id and
  platform (`channel_origin` / `ensure_thread_in_scope`) and returns
  `channel_thread_out_of_scope` otherwise. Web runs may send to any allowed
  recipient. `zalo_bot_group_settings` also applies directly after in-chat
  confirmation and may change only the run's own group.
- **F3: remove the learned recipe.** Rewrite the Big Brother profile skill
  `messaging-channels` to point at `send_channel_message` and drop the
  `agent send --to` lines (profile data, done with the owner's consent).
  Mention in the `send_channel_message` description that it is the only
  supported way to send on Zalo.
- **F4: approval card timeout.** In test 5 the web approval request was
  emitted and no response reached Runtime before the 300 s timeout. Check the
  UI card for the new `pattern_key`s (`zalo_bot.send_message`,
  `zalo.send_group_message`) and the approve/deny round trip.

Out of scope here: duplicate rows in `state.db` for one web session (123
rows, 79 distinct), tracked separately.
