# Google Chat

Connection: the service account JSON object, GCP project id, and Pub/Sub
subscription path, entered only through the connect card
(`xnobrain_integration_connect` with `kind="channel"` and
`platform="google_chat"`). Never ask for the service account key in chat. The
JSON is stored compacted in the profile environment and is never returned by the
channel API. Hermes uses Pub/Sub streaming pull only; no HTTP events URL or
webhook is configured. Follow the Hermes Google Chat guide to create the Chat
app, service account, and subscription.

## Tools

- `send_channel_message` with `platform="google_chat"` and `thread_type="dm"` or
  `"group"` sends text. One message is limited to 4000 characters.
- `quote_message_id` and `mentions` are Zalo-only and are refused for Google
  Chat.
- There are no Google Chat history, search, contact, or administration tools.
  This channel has parity with the Hermes Google Chat adapter only.

## Limits

- A space recipient is a resource name such as `spaces/AAAA` (or `users/123`); a
  sender is the user's email. Both match
  `^(?:(?:spaces|users)/[A-Za-z0-9_-]{1,200}|[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,63})$`.
- Sends go only to a paired or allowlisted email, or to an owner-approved space.
  Every space must be approved first.
- Google Chat has no @mention gate in this adapter. Direct messages and spaces
  both reach Runtime, which enforces pairing, allowlists, and group approval.
- Native file attachments require each user to run `/setup-files` once in their
  own DM so the bot can use that user's OAuth token. Text replies need no OAuth.
