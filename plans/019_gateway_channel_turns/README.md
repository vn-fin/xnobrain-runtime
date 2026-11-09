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
- The endpoint refuses agents whose channel DM policy is not `allowlist` or
  `pairing`, and refuses any allow-all configuration.
- One conversation is bound to `(platform, agent_id, thread_id)` in
  `<profile>/zalo/threads.json` (atomic, `0600`).
- The turn uses `PlatformService.start_conversation_run` with
  `run_mode: background` and idempotency key `<platform>:<message_id>`, then
  drains the durable run events. This is the same path as web chat, including
  managed MCP, the connect tool, policies, run records, and usage accounting.
- Approval-gated tools are non-interactive. The event drain resolves each
  `approval.request` as `deny` through the existing run approval service, so
  the model gets the normal blocked-tool result instead of waiting 300 seconds.
- `install_runtime_channel_delegation()` in `xnobrain/integrations/gateway_cli.py`
  wraps `GatewayRunner._run_agent_inner` for `zalo`/`zalo_bot`, posts to the
  endpoint with `RUNTIME_INTERNAL_SERVICE_TOKEN`, keeps a typing keepalive when
  the adapter exposes `send_typing`, and falls back to the native gateway agent
  on any failure. The switch `RUNTIME_GATEWAY_CHANNEL_DELEGATION` defaults off.

## Limits

- Grant freshness: managed MCP is present only while the owner's Control grant
  is fresh (about two hours from the owner's last web request). If it expires,
  the sender still gets a normal reply without MCP tools.
- Authority: every allowed sender's turn executes with the owner's held
  grant. Operators must use `dm_policy: allowlist` or `pairing`; allow-all is
  rejected by the endpoint.
- Text only: attachments, media URLs, stickers, and typing-rich replies are not
  forwarded. The reply is the final stored text.
- Conversations created for channel threads are personal and unowned; they are
  not attributed to the web owner and are not part of the authenticated web
  conversation list.
- Run cancellation follows the existing durable run service; a second message
  for a busy thread returns `409`.
