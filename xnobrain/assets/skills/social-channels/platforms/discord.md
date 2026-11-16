# Discord

Connection: a bot token from the Discord Developer Portal, entered only through
the connect card (`xnobrain_integration_connect` with `kind="channel"` and
`platform="discord"`). Never ask for the token in chat. Follow the Hermes
Discord guide for the setup steps: create the application and bot, then enable
the **Server Members Intent** and the **Message Content Intent**. Without the
Message Content intent the bot connects but cannot read message text.

## Tools

- `send_channel_message` with `platform="discord"` and `thread_type="dm"` or
  `"group"` sends plain text. One message is limited to 2000 characters.
- `quote_message_id` and `mentions` are Zalo-only and are refused for Discord.
- There are no Discord history, search, contact, or moderation tools. This
  channel has parity with the Hermes Discord adapter only.

## Limits

- A direct-message recipient is the numeric Discord user id (a snowflake such
  as `123456789012345678`). A server channel or thread recipient is the
  channel's own numeric snowflake id; a thread keeps its own id, not the
  parent channel's.
- Both id shapes match `^[0-9]{1,20}$`.
- Sends go only to a paired or allowlisted direct message, or to an
  owner-approved channel or thread. Every group must be approved first.
- In server channels the bot answers only when it is @mentioned or replied to
  by default. Messages without a mention are ignored unless the channel is a
  configured free-response channel. Direct messages need no mention.
- The bot sees only the messages Discord delivers to it; it cannot read earlier
  channel history.
- Media is delivered natively by the Hermes gateway when the reply contains
  `MEDIA:<path>`; there is no separate Discord media tool.
