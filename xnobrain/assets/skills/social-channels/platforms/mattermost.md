# Mattermost

Connection: the server URL and a bot or personal-access token, entered only
through the connect card (`xnobrain_integration_connect` with
`kind="channel"` and `platform="mattermost"`). Never ask for the token in chat.
Create a bot account on the Mattermost server, copy its token when shown, and
add the bot to every channel it should answer in. Follow the Hermes Mattermost
guide for the server settings, token retrieval, and optional thread reply mode.

## Tools

- `send_channel_message` with `platform="mattermost"` and `thread_type="dm"` or
  `"group"` posts plain text. One message is limited to 4000 characters.
- `quote_message_id` and `mentions` are Zalo-only and are refused for
  Mattermost.
- There are no Mattermost history, search, contact, or administration tools.
  This channel has parity with the Hermes Mattermost adapter only.

## Limits

- A direct-message recipient is the DM channel id, not the person's user id. A
  channel recipient is the channel id. Mattermost ids are 26 lower-case
  alphanumeric characters and match `^[a-z0-9]{26}$`.
- Sends go only to a paired or allowlisted user's DM, or to an owner-approved
  channel. Every channel must be approved first.
- In channels the bot answers only when @mentioned unless the channel is a
  configured free-response channel. Direct messages need no mention.
- Thread replies depend on the server's reply mode; the recipient stays the
  channel id.
- Media is delivered natively by the Hermes gateway when the reply contains
  `MEDIA:<path>`; there is no separate Mattermost media tool.
