# QQ Bot

Connection: the App ID and App Secret from the Official QQ Bot API (v2),
entered only through the connect card (`xnobrain_integration_connect` with
`kind="channel"` and `platform="qqbot"`). Never ask for the App Secret in chat.
Follow the Hermes QQ Bot guide to register the application, enable the C2C and
group @-message intents, and use sandbox mode while testing.

## Tools

- `send_channel_message` with `platform="qqbot"` and `thread_type="dm"` or
  `"group"` sends plain text. One message is limited to 4000 characters.
- `quote_message_id` and `mentions` are Zalo-only and are refused for QQ Bot.
- There are no QQ history, search, contact, or moderation tools. This channel
  has parity with the Hermes QQ Bot adapter only.

## Limits

- A direct-message recipient is the sender's OpenID. A group recipient is the
  group's OpenID. Both are opaque alphanumeric identifiers matching
  `^[A-Za-z0-9_.\-]{1,64}$`.
- Sends go only to a paired or allowlisted user, or to an owner-approved group.
  Every group must be approved first.
- Group messages must @mention the bot. Direct messages need no mention.
- Guild channels are treated as group conversations and use the guild or
  channel OpenID as the recipient.
- Voice messages are transcribed by QQ's built-in ASR, with an optional
  configured STT provider. There is no separate QQ Bot media tool.
