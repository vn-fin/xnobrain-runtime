# Device command protocol v1

Status: design contract for OSS-03, ENT-02, and ENT-03. Any incompatible change creates `v2`; do not silently reinterpret fields.

## Transport and identity

The local runtime initiates outbound TLS over port 443 using WebSocket or long polling with HTTP polling fallback. No inbound listener is required. On first connection the runtime generates an Ed25519 key pair, registers the public key, proves possession through a challenge, and receives a device ID plus short-lived access token. Anonymous Free devices are possession identities and may later be claimed by an account without changing the device key.

Private keys never leave `DATA_DIR/device/`. Tokens rotate, device revocation stops new commands, and server commands are signed by a pinned control-plane signing key. Production never disables TLS verification.

## Command envelope

```json
{
  "version": 1,
  "command_id": "cmd_01...",
  "device_id": "dev_01...",
  "type": "cron.execute",
  "agent_id": "abc123",
  "resource_id": "cron_01...",
  "reservation_id": "res_01...",
  "scheduled_at": "2026-07-20T02:00:00Z",
  "issued_at": "2026-07-20T02:00:01Z",
  "expires_at": "2026-07-20T02:15:00Z",
  "attempt": 1,
  "payload_ciphertext": "base64...",
  "payload_sha256": "hex...",
  "signature": "base64..."
}
```

The cloud may see scheduling metadata but a local job prompt can remain encrypted to the device. The signature covers every field except `signature` using canonical JSON. The runtime validates version, device, signature, expiration, payload hash, agent ownership, reservation, and command replay state before execution.

## Delivery semantics

Delivery is at-least-once. `command_id` is the idempotency key. The local journal records `received`, `accepted`, `running`, and terminal state before acknowledging transitions. A duplicate returns the recorded state and never starts Hermes again.

Statuses are `accepted`, `running`, `completed`, `failed`, `expired`, `rejected`, and `cancelled`. A completion includes timestamps, non-sensitive error code, and resource usage summary; it never includes credentials or unrestricted profile content.

## Offline and missed occurrences

The connector supplies its last acknowledged cursor when reconnecting. The server returns commands still inside their TTL and one compact missed-occurrence summary. Missed occurrences are not execution commands and consume no run quota.

Supported misfire policies are `skip`, `notify`, `run_latest`, `ask`, and `replay_bounded`. Default is `notify`. `replay_bounded` requires a positive maximum. Expired intervals are aggregated by cron ID with count, first time, and last time rather than sending one notification per tick.

## Security acceptance tests

- Invalid signature, wrong device, unknown agent, expired TTL, replay, and altered payload are rejected.
- Reconnect delivers no duplicate execution.
- Revocation closes the stream and rejects token refresh.
- Logs and spans do not contain the access token, ciphertext plaintext, prompt, or signing material.
- A fake server can drive the complete register, connect, dispatch, acknowledge, reconnect, and revoke sequence.
