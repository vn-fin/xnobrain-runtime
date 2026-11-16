# Slack

Connection: two tokens from the Slack app, entered only through the connect
card (`xnobrain_integration_connect` with `kind="channel"` and
`platform="slack"`). Never ask for the tokens in chat. The channel needs both
the bot token (`xoxb-...`) and the app-level token (`xapp-...`, scope
`connections:write`) because Hermes uses Socket Mode and never needs a public
URL. Follow the Hermes Slack guide for the setup steps and the required bot
scopes; the most commonly missed ones are `channels:history`,
`groups:history`, and `files:read`.

## Tools

- `send_channel_message` with `platform="slack"` and `thread_type="dm"` or
  `"group"` sends plain text. One message is limited to 39000 characters.
- `quote_message_id` and `mentions` are Zalo-only and are refused for Slack.
- There are no Slack history, search, contact, or administration tools. This
  channel has parity with the Hermes Slack adapter only.

## Limits

- A channel recipient is a Slack channel id such as `C0123ABCDEF`, `G0123ABCDEF`
  (private channel), or `D0123ABCDEF` (direct message). A member id such as
  `U0123ABCDEF` or `W0123ABCDEF` (Enterprise Grid) is a sender, not a chat.
- Both shapes match `^[A-Z][A-Z0-9]{1,32}$`.
- A thread reply stays on its channel id: Slack threads are not separate
  channels, so the channel id is the recipient and the thread is tracked by the
  runtime.
- Sends go only to a paired or allowlisted direct message, or to an
  owner-approved channel. Every group must be approved first.
- In channels the bot answers only when it is @mentioned; once it has an active
  thread it follows replies in that thread without a new mention. Direct
  messages need no mention.
- Media is delivered natively by the Hermes gateway when the reply contains
  `MEDIA:<path>`; there is no separate Slack media tool.
