# Matrix

Connection: the homeserver URL and an access token, entered only through the
connect card (`xnobrain_integration_connect` with `kind="channel"` and
`platform="matrix"`). Never ask for the token in chat. Hermes uses token auth
only here; password login and a bot user id are not part of this channel.
End-to-end encryption is off by default, so no crypto store path is configured.
Follow the Hermes Matrix guide for creating the bot account and copying the
access token from Element.

## Tools

- `send_channel_message` with `platform="matrix"` and `thread_type="dm"` or
  `"group"` sends text. One message is limited to 16000 characters.
- `quote_message_id` and `mentions` are Zalo-only and are refused for Matrix.
- There are no Matrix history, search, contact, or moderation tools. This
  channel has parity with the Hermes Matrix adapter only.

## Limits

- A room recipient is a room id such as `!abc123:matrix.org`; a user id is
  `@alice:matrix.org`. Both match
  `^[@!][A-Za-z0-9._=\-]{1,190}:[A-Za-z0-9.\-]{1,190}(?::[0-9]{1,5})?$`.
- Sends go only to a paired or allowlisted direct message, or to an
  owner-approved room. Every room must be approved first.
- In rooms the bot answers only when @mentioned by default. Direct messages
  need no mention. Threads keep the room id as the recipient.
- E2EE is off; encrypted rooms are not decrypted by this channel. Ask the owner
  to disable encryption for the bot room.
- Media is delivered natively by the Hermes gateway when the reply contains
  `MEDIA:<path>`; there is no separate Matrix media tool.
