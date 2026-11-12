# Zalo Bot

Connection: an official Zalo Bot token, entered only through the connect card
(`xnobrain_integration_connect` with `kind="channel"` and
`platform="zalo_bot"`). Never ask for the token in chat.

## Tools

- `send_channel_message` with `platform="zalo_bot"` and `thread_type="dm"` or
  `"group"` sends plain text. One message is limited to 2000 characters.
- `zalo_bot_send_photo`, `zalo_bot_send_sticker`, and `zalo_bot_send_voice`
  send media. Photos and voice files use HTTPS URLs; voice files are `.aac` and
  direct-message only.
- `zalo_bot_status` reports channel and bot state; `zalo_bot_list_chats`,
  `zalo_bot_read_history`, `zalo_bot_search_messages`, and `zalo_bot_contacts`
  read what the bot stored.
- `zalo_bot_group_settings` changes follow, silent, or welcome settings for one
  approved group.

## Limits

- A chat id is an exact Zalo Bot chat id matching `^[A-Za-z0-9_-]{1,64}$`.
- Sends go only to a paired or allowlisted direct message, or to an
  owner-approved group. Every group must be approved by the owner first.
- Zalo delivers a group message to a bot only when the message mentions the
  bot. Group history therefore contains only those mention messages and the
  bot's own replies, not the full group transcript. A full group view needs the
  personal account, which is a separate channel.
- The bot cannot send voice messages to a group.
