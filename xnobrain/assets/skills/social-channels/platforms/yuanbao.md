# Yuanbao

Connection: the Yuanbao App ID and App Secret, entered only through the connect
card (`xnobrain_integration_connect` with `kind="channel"` and
`platform="yuanbao"`). Never ask for the App Secret in chat. Follow the Hermes
Yuanbao guide to create a bot under **PAI → My Bot** and copy its credentials;
the WebSocket URL and API domain have built-in defaults.

## Tools

- `send_channel_message` with `platform="yuanbao"` and `thread_type="dm"` or
  `"group"` sends plain text. One message is limited to 4000 characters.
- `quote_message_id` and `mentions` are Zalo-only and are refused for Yuanbao.
- There are no Yuanbao history, search, contact, or administration tools. This
  channel has parity with the Hermes Yuanbao adapter only.

## Limits

- A direct-message recipient is `direct:<account>`; a group recipient is
  `group:<group_code>`. The sender id is the bare account. Both match
  `^(?:(?:direct|group):)?[A-Za-z0-9_.\-]{1,64}$`.
- Sends go only to a paired or allowlisted account, or to an owner-approved
  group. Every group must be approved first.
- Yuanbao delivers over a persistent WebSocket with HMAC authentication,
  heartbeat, deduplication, and reconnection. The adapter can also auto-set the
  first conversation as the home channel when none is configured.
- Media is delivered as native Yuanbao images, files, and voice; forwarded
  WeChat chat history is decoded into the conversation.
