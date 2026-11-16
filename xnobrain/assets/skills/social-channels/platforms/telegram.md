# Telegram

Connection: a bot token from @BotFather, entered only through the connect card
(`xnobrain_integration_connect` with `kind="channel"` and
`platform="telegram"`). Never ask for the token in chat. Follow the Hermes
Telegram guide for the setup steps: create the bot with @BotFather, then choose
the privacy mode for groups.

## Tools

- `send_channel_message` with `platform="telegram"` and `thread_type="dm"` or
  `"group"` sends plain text. One message is limited to 4096 characters.
- `quote_message_id` and `mentions` are Zalo-only and are refused for Telegram.
- There are no Telegram history, search, contact, or group administration
  tools. This channel has parity with the Hermes Telegram adapter only.

## Limits

- A direct-message recipient is a positive Telegram user id matching
  `^[0-9]{1,20}$`. A group or supergroup recipient is a negative chat id such
  as `-1001234567890`, matching `^-?[0-9]{1,20}$`.
- Sends go only to a paired or allowlisted direct message, or to an
  owner-approved group. Every group must be approved by the owner first.
- The bot receives a group message only when BotFather privacy mode is off, or
  when the message mentions the bot or replies to one of its messages. It
  cannot read earlier group history or the full transcript.
- Media is delivered natively by the Hermes gateway when the reply contains
  `MEDIA:<path>`; there is no separate Telegram media tool.
