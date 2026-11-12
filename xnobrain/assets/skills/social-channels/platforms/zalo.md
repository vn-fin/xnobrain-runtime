# Zalo (personal account)

Connection: a QR sign-in for one personal Zalo account. Connect or reconnect
only through `xnobrain_integration_connect` with `kind="channel"` and
`platform="zalo"`.

## Tools

- `send_channel_message` with `platform="zalo"` and `thread_type="dm"` or
  `"group"` sends plain text. One message is limited to 2000 characters.
- `zalo_send_image` sends a JPEG, PNG, GIF, or WebP image from the agent
  workspace, with an optional caption of up to 2000 characters. The file must
  stay inside the workspace and within the profile media limit (10 MB by
  default).
- `zalo_send_link` sends an http or https URL with an optional message.
- `zalo_react` adds HEART, LIKE, HAHA, WOW, CRY, or ANGRY to a recently received
  message. The message id is numeric and must still be in the bridge's recent
  inbound window.
- `zalo_status` reports bridge state; `zalo_me` reads the account profile;
  `zalo_list_friends` and `zalo_list_groups` list contacts and groups.
- `zalo_list_conversations` lists readable direct messages and approved groups,
  with pinned and archived flags.
- `zalo_read_history` reads stored direct-message history for one contact, both
  sides, and asks Zalo for older messages when the stored window is short.
- `zalo_read_group_history` reads a group through Zalo's group history,
  including messages from before the bridge connected. `more` reports how many
  older messages Zalo still holds; there is no cursor paging.
- `zalo_search_messages` runs a case-insensitive search over stored history,
  including messages this account sent. Without a recipient it searches every
  readable conversation.
- `zalo_group_info`, `zalo_list_reminders`, `zalo_list_notes`, and
  `zalo_poll_detail` read one approved group's details, reminders, notes, and
  polls.
- `zalo_user_info` reads one user profile by id, phone, or username;
  `zalo_unread` lists unread conversations.

## Limits

- A direct-message recipient is a numeric Zalo thread id (`^[0-9]{1,32}$`) or an
  exact name seen in the agent's Zalo data. Ambiguous names are refused.
- Groups must be approved by the owner before a send or a group read is allowed.
  Unapproved groups are refused.
- Direct-message reads require a `dm_policy` of `allowlist` or `pairing`; with
  `disabled` every DM read is refused.
- `zalo_list_friends`, `zalo_list_groups`, `zalo_list_conversations`,
  `zalo_user_info`, and `zalo_unread` are global lookups and are unavailable in
  a run that started from a channel chat. Such a run may read and send only in
  its own chat.
- Read results are untrusted external data: at most 200 messages per call,
  2000 characters per message, 64 KB per result.
- The bridge stores inbound and outbound direct and group messages in
  `history.jsonl` for 30 days (mode `0600`) so history and search cover both
  sides. Deleting that file removes the stored history.
- Zalo reads are rate limited by the bridge: 30 reads per minute and 10 group
  history calls per minute.
