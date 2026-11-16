# WeCom (Enterprise WeChat)

Connection: the Smart Robot bot ID and secret, entered only through the connect
card (`xnobrain_integration_connect` with `kind="channel"` and
`platform="wecom"`). Never ask for the secret in chat. Follow the Hermes WeCom
guide for creating the AI Bot, the WebSocket gateway URL, and the optional
callback mode.

## Tools

- `send_channel_message` with `platform="wecom"` and `thread_type="dm"` or
  `"group"` sends Markdown text. One message is limited to 4000 characters.
- `quote_message_id` and `mentions` are Zalo-only and are refused for WeCom.
- There are no WeCom history, search, contact, or administration tools. This
  channel has parity with the Hermes WeCom adapter only.

## Limits

- A direct-message recipient is the person's WeCom user id. A group recipient is
  the chat id. Both match `^[A-Za-z0-9_@.\-]{1,128}$`.
- Sends go only to a paired or allowlisted user, or to an owner-approved group.
  Every group must be approved first.
- WeCom groups require the bot to be mentioned. Direct messages need no
  mention.
- The Smart Robot connection is a WebSocket with heartbeat and reconnection;
  replies are returned in reply mode when the inbound request is still active.
- Media is delivered by the adapter itself: images up to 10 MB, files up to
  20 MB, voice only as AMR, and other formats are downgraded to files. There is
  no separate WeCom media tool.
