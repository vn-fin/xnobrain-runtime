# DingTalk

Connection: the client ID (app key) and client secret, entered only through the
connect card (`xnobrain_integration_connect` with `kind="channel"` and
`platform="dingtalk"`). Never ask for the client secret in chat. Hermes uses
DingTalk Stream Mode only: the bot opens an outbound WebSocket and needs no
public URL or callback endpoint. Follow the Hermes DingTalk guide to create the
app, add the Robot capability, and select Stream Mode.

## Tools

- `send_channel_message` with `platform="dingtalk"` and `thread_type="dm"` or
  `"group"` sends text. One message is limited to 20000 characters.
- `quote_message_id` and `mentions` are Zalo-only and are refused for DingTalk.
- There are no DingTalk history, search, contact, or administration tools. This
  channel has parity with the Hermes DingTalk adapter only.

## Limits

- A conversation recipient is a conversation id such as `cid...==`; a sender is
  the DingTalk staff id. Both match `^[A-Za-z0-9_@.$=\-:]{1,200}$`.
- Sends go only to a paired or allowlisted direct message, or to an
  owner-approved group. Every group must be approved first.
- In groups the bot answers only when @mentioned; direct messages need no
  mention.
- AI Cards, emoji reactions, and media delivery are handled by the adapter.
  There is no separate DingTalk media tool and no webhook URL is required.
