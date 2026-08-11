# OSS-03: outbound device connector

Priority P1 after `docs/contracts/device-command-v1.md` freezes. Own new `services/deviceconnector` and `internal/device` packages plus focused tests. The feature is disabled unless a cloud endpoint is configured or the user explicitly pairs a device.

## Responsibilities

- Generate and protect the local device key.
- Register an anonymous possession identity and optionally claim it later.
- Maintain one bounded outbound port-443 connection with exponential backoff and jitter.
- Refresh short-lived credentials through proof of key possession.
- Receive, validate, journal, and acknowledge commands.
- Route accepted commands to shared services, never direct filesystem functions.
- Send heartbeats, capabilities, execution status, and redacted diagnostics.
- Receive compact missed-run summaries and create local notifications.
- Revoke/unpair locally and erase only cloud credentials, never profile data.

## Package boundaries

`internal/device` owns keys, token storage, canonical signature verification, journal, and transport DTOs. `services/deviceconnector` owns lifecycle, reconnect, command dispatch, health, and service adapters. A small interface allows a fake control plane in tests.

Use bounded queues and contexts. Backpressure must not create unbounded goroutines or memory. A command is durably journaled before acceptance. Status reporting retries independently from execution and uses the same idempotency key.

## User experience

Local operation never requires pairing. Cloud Scheduler, Cloud Backup, or Remote Management initiates pairing. Anonymous mode shows a recovery code and explains that loss means no remote recovery. Account claim transfers control-plane ownership without rotating the device identity. The UI always exposes last connected time, queued commands, cloud endpoint, and Unpair.

## Acceptance criteria

- Works behind NAT with no inbound port and through a standard HTTPS proxy.
- Fake-server tests cover register, token rotation, reconnect cursor, duplicate, expiry, revocation, and key rotation.
- Device offline never blocks local API or local cron.
- Invalid commands cannot call Hermes or mutate profiles.
- No secrets or command plaintext appear in logs, traces, crash output, or health APIs.
- Network loss during every state transition converges correctly after reconnect.
