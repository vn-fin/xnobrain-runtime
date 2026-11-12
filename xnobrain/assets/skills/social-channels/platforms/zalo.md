# Zalo (personal account)

Connection: a QR sign-in for one personal Zalo account. Connect or reconnect
only through `xnobrain_integration_connect` with `kind="channel"` and
`platform="zalo"`.

## Tools

- `send_channel_message` with `platform="zalo"` and `thread_type="dm"` or
  `"group"` sends plain text. One message is limited to 2000 characters.
- `zalo_send_image` sends a JPEG, PNG, GIF, or WebP image from the agent
  workspace, with an optional caption of up to 2000 characters. The file must
  stay inside the workspace and within the profile media limit (10 MB by
  default).
- `zalo_send_link` sends an http or https URL with an optional message.
- `zalo_react` adds HEART, LIKE, HAHA, WOW, CRY, or ANGRY to a recently received
  message. The message id is numeric and must still be in the bridge's recent
  inbound window.
- `zalo_status` reports bridge state; `zalo_me` reads the account profile;
  `zalo_list_friends` and `zalo_list_groups` list contacts and groups.

## Limits

- A direct-message recipient is a numeric Zalo thread id (`^[0-9]{1,32}$`) or an
  exact name seen in the agent's Zalo data. Ambiguous names are refused.
- Groups must be approved by the owner before a send is allowed. Unapproved
  groups are refused.
- `zalo_list_friends` and `zalo_list_groups` are unavailable in a run that
  started from a channel chat. Such a run can act only on its own chat.
- The current personal toolset has no message-history reader.
