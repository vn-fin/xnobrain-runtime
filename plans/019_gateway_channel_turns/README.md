# 019 — Gateway channel turns

Status: implemented on `feat/zalo-bot-platform`.

## Goal

A message from the `zalo` or `zalo_bot` gateway, after the gateway's own
authorization has passed, runs as a normal conversation turn in the main
Runtime process. The gateway receives the reply text and sends it through its
normal delivery path. No credential is duplicated into the gateway and no
second agent loop is introduced.

## Design

- `POST /xnobrain/api/runtime/v1/internal/channels/turn` accepts one authorized
  message, checks the workspace internal token and `FT_ENABLE_AGENT_CHANNELS`,
  and validates the agent, platform IDs, thread type, and text.
- The endpoint requires the selected platform to be enabled, requires a
  persisted owner binding whose `agent_id` matches the profile, and refuses
  agents whose channel DM policy is not `allowlist` or `pairing`; allow-all is
  always refused.
- One conversation is bound to `(platform, agent_id, thread_id)` in
  `<profile>/zalo/threads.json` (atomic, `0600`).
- The turn uses `PlatformService.start_conversation_run` with
  `run_mode: background` and idempotency key `<platform>:<message_id>`, then
  drains the durable run events. This is the same path as web chat, including
  managed MCP, the connect tool, policies, run records, and usage accounting.
- A verified channel enable/patch writes the owner binding to
  `<profile>/.xnobrain/channels/owner.json` (atomic, `0600`, path-checked).
  The turn requires that binding and builds an explicit `HostAuthority` from
  it; the conversation and run actor are attributed to the binding instead of
  an unowned personal backfill. Before a turn, Runtime mints an owner-bound
  Control MCP grant, validates its `user_id`/`tenant_id` against that binding,
  caches it in memory by profile/agent/platform until about two minutes before
  expiry, and passes it as `start_conversation_run(mcp_grant=...)`. That
  explicit host-owned value does not require a synthetic signed
  `TrustedRequestContext`.
- Approval-gated tools are non-interactive. The event drain resolves each
  `approval.request` as `deny` through the existing run approval service, so
  the model gets the normal blocked-tool result instead of waiting 300 seconds.
- `install_runtime_channel_delegation()` in `xnobrain/integrations/gateway_cli.py`
  wraps `GatewayRunner._run_agent_inner` for `zalo`/`zalo_bot`, posts to the
  endpoint with `RUNTIME_INTERNAL_SERVICE_TOKEN`, and keeps a typing keepalive
  when the adapter exposes `send_typing`. Once a request has been sent it never
  runs the native gateway agent as a second turn: timeout, non-200, or an
  invalid envelope returns one sanitized retry message and relies on the
  `message_id` idempotency key. Native execution remains only for delegation
  OFF, an unselected platform, or a request with no internal token. A missing
  provider message ID on a delegated platform returns the sanitized failure so
  retries cannot fork into a native turn. The switch
  `RUNTIME_GATEWAY_CHANNEL_DELEGATION` defaults off. The launcher also installs
  a `gateway.run` log filter that replaces pinned upstream INFO ingress lines
  containing user payloads or display names with a redacted event.

## Limits

- Owner binding: managed MCP is available only while the profile has a verified
  `.xnobrain/channels/owner.json` binding and Control issues a grant whose
  `user_id`/`tenant_id` match it. The binding is written by a verified
  enable/patch and removed only when no channel remains enabled. A missing
  binding is refused before the turn starts. A channel turn also requires a
  fresh owner-bound per-run grant; a Control failure, identity mismatch,
  expired grant, or missing grant refuses the turn before it starts with
  `503 channel_mcp_grant_unavailable` instead of reusing a profile-scoped
  bearer from another turn. Accepted runs bind their grant through the run
  `ContextVar`; the managed MCP session overlays it only for that server
  request, so concurrent web turns and other identities keep their own scope.
  Grants stay in memory and never enter run records, channel state, traces, or
  logs. The local `HostAuthority` is not a Control-signed principal.
- Authority: every allowed sender's turn executes with the owner's owner-bound
  grant and is attributed to the persisted owner binding. Operators must use
  `dm_policy: allowlist` or `pairing`; allow-all is rejected by the endpoint.
  A missing or conflicting binding fails closed.
- Text only: attachments, media URLs, stickers, and typing-rich replies are not
  forwarded in the turn API; the reply is the final stored text. For personal
  `zalo`, the adapter downloads authorized inbound media into the agent
  workspace (`zalo-inbound/<thread>/<message>-<name>`) and passes the
  workspace-relative path in the turn text note.
- Non-web parity gaps remain: the gateway does not receive per-tool events,
  tool names, approval events, per-run usage attribution, streamed UI
  metadata, or attachments. It reports `api_calls: 1` and an empty `tools`
  list even when the Runtime run used tools.
- Channel conversations are personal and owner-attributed so the authenticated
  web owner can see them in normal history. Older unowned channel
  conversations are not backfilled; a fresh thread conversation is created
  when the stored binding does not match the current owner and platform.
- No channel-specific turn cap is imposed. The run uses the existing session
  timeout, admission, concurrency, and provider-turn budget policies; the
  gateway HTTP timeout is derived from that session timeout rather than a
  smaller arbitrary limit.
- Run cancellation follows the existing durable run service; a second message
  for a busy thread returns `409`.
