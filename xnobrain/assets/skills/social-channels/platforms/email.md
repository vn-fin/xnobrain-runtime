# Email

Connection: the agent's mailbox address, password or app password, IMAP host,
and SMTP host, entered only through the connect card
(`xnobrain_integration_connect` with `kind="channel"` and `platform="email"`).
Never ask for the password in chat. Use a dedicated mailbox, not a personal
one. Follow the Hermes Email guide for provider-specific steps: enable IMAP,
create an app password when the provider requires one, and use
`imap.gmail.com` / `smtp.gmail.com` for Gmail.

## Tools

- `send_channel_message` with `platform="email"` and `thread_type="dm"` sends a
  plain-text email. One message is limited to 50000 characters.
- `quote_message_id` and `mentions` are Zalo-only and are refused for Email.
- There are no Email history, search, contact, or mailbox-management tools. This
  channel has parity with the Hermes Email adapter only; the Himalaya skill is a
  separate terminal-based capability.

## Limits

- Email has direct messages only. The recipient is the sender's email address,
  matching `^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,63}$`.
- Sends go only to an allowlisted address. Email has no pairing flow: access
  is `disabled`, `allowlist`, or `open`, and a pairing request is refused. Open
  policy controls who may start a direct message; it does not let
  `send_channel_message` start an outbound conversation with a stranger.
  Addresses are matched exactly.
- The adapter polls the inbox every 15 seconds by default and ignores
  automated, no-reply, bulk, and self-addressed mail.
- Replies keep the email subject and thread headers. Attachments in the reply
  are delivered natively by the Hermes gateway when it contains
  `MEDIA:<path>`; there is no separate Email media tool.
