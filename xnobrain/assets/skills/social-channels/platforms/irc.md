# IRC

Connection: the server hostname, channel list, and bot nickname, plus optional
server and NickServ passwords, entered only through the connect card
(`xnobrain_integration_connect` with `kind="channel"` and `platform="irc"`).
Never ask for a password in chat. Follow the Hermes IRC guide for the server,
TLS, and NickServ settings; the adapter uses only Python's standard library.

## Tools

- `send_channel_message` with `platform="irc"` and `thread_type="dm"` or
  `"group"` sends plain text. One message is limited to 450 characters and
  long replies are split across several `PRIVMSG` lines.
- `quote_message_id` and `mentions` are Zalo-only and are refused for IRC.
- There are no IRC history, search, contact, or moderation tools. This channel
  has parity with the Hermes IRC adapter only.

## Limits

- A channel recipient is `#channel` or `&channel`; a direct-message recipient
  is the person's nick. A nick matches
  `^[A-Za-z0-9_.\-\[\]\\`^{}|]{1,64}$`, and a channel is the same with a
  leading `#` or `&`.
- IRC has no message threading, voice, image, file, reaction, or typing support.
  Plain text only.
- In channels the bot answers only when addressed with its nick prefix
  (`nick:`, `nick,`, or `nick `). Direct messages need no prefix.
- Sends go only to a paired or allowlisted nick, or to an owner-approved
  channel. Every channel must be approved first.
- The adapter holds a scoped lock per server and nick, so two profiles cannot
  share one IRC identity.
