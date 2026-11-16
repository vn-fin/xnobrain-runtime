# Feishu / Lark

Connection: the app ID and app secret, plus the domain (`feishu` for Feishu
China or `lark` for Lark international), entered only through the connect card
(`xnobrain_integration_connect` with `kind="channel"` and
`platform="feishu"`). Never ask for the app secret in chat. This channel always
uses the outbound WebSocket transport; webhook mode is not supported. Follow the
Hermes Feishu / Lark guide for creating the app, enabling the Bot capability,
granting the message scopes, and publishing a version.

## Tools

- `send_channel_message` with `platform="feishu"` and `thread_type="dm"` or
  `"group"` sends text. One message is limited to 8000 characters.
- `quote_message_id` and `mentions` are Zalo-only and are refused for Feishu.
- There are no Feishu history, search, contact, or administration tools. This
  channel has parity with the Hermes Feishu adapter only.

## Limits

- A chat recipient is a chat id such as `oc_xxx`; a user id is `ou_xxx` or
  `on_xxx`. Both match `^(?:oc|ou|on|cli)_[A-Za-z0-9]{1,200}$`.
- Sends go only to a paired or allowlisted direct message, or to an
  owner-approved group. Every group must be approved first.
- In groups the bot answers only when @mentioned; direct messages need no
  mention. The adapter's own group policy is not used to approve groups;
  Runtime's per-group approval is authoritative.
- Media and message cards are delivered by the adapter. There is no separate
  Feishu media tool.
