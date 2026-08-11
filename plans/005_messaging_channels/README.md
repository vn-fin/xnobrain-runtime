# 005 — Messaging channels

Priority: **P1**. This is Hermes' flagship capability and is entirely missing
from Brain4All today. Depends on the Kanban foundation discipline
(`plans/001_kanban_foundation/README.md`) for *how* we pin and ride Hermes, but
has no runtime dependency on Kanban. It can proceed in parallel with 006–009.

Read the five companion files in order:

1. `findings.md` — gap analysis: exactly what Hermes provides, what Brain4All
   lacks, the profile ↔ channel ↔ multiplex question, APIs to pin, credential
   and security notes, risks, open questions.
2. `architecture.md` — Brain4All layering fit, state ownership, the new
   `/api/brain/v1/...` route contract with Pydantic model names, adapter
   methods, React surface, and sequence diagrams.
3. `approaches.md` — options and trade-offs (multiplexed vs per-agent gateway;
   native-config vs Brain4All credential file; loopback-HTTP vs in-process
   Python adapter) and the chosen approach.
4. `implementation.md` — ordered, phased, file-by-file steps with exact new
   route paths and model names.
5. `validation.md` — compatibility test, unit/integration tests, `make`
   targets, manual Telegram round-trip, and the acceptance checklist.

## Goal

Let a local user connect their Brain4All agent to messaging platforms
(Telegram, Discord, Slack, WhatsApp, Signal, and every other channel Hermes
supports) so they can chat with their agent from a phone or app. Manage, per
agent: which channels are enabled, bot-token credentials stored safely, the
gateway process lifecycle, pairing/linking, and channel connection status.

Each Brain4All agent **is** a Hermes profile. Channel enablement and
credentials are therefore per-profile. The single Hermes gateway (or, where a
channel binds a port, the default profile's gateway) serves those channels.

## Non-goals

- No new database, ORM, Go service, or second API process. One FastAPI/Hermes
  process on `:8642`, one 9router on `:20128`.
- No copied or forked Hermes gateway: no re-implemented dispatch loop, session
  loop, platform adapter, pairing store, or channel SQL. Brain4All rides
  Hermes' native gateway/messaging/pairing APIs and its profile `config.yaml` /
  `.env`.
- No Brain4All-owned message routing, delivery, or per-message persistence.
  Messages live in Hermes; Brain4All manages *configuration and lifecycle*.
- No new credential store. Tokens live where Hermes already reads them: the
  agent's profile `.env` (values) and `config.yaml` (`platforms.<id>.enabled`).
- No provider change: the agent still runs on 9router; channels are transport,
  not model providers.
- No mock/demo channels or fabricated connection status in production paths.

## Scope

In scope:

- List the channel catalog Hermes advertises, per agent, with connection state.
- Enable/disable a channel per agent; set, rotate, and clear its credentials.
- Guided Telegram and WhatsApp onboarding (QR / bot-token pairing) reusing
  Hermes' native onboarding endpoints.
- Gateway lifecycle: start, stop, restart, drain, and status — driven through
  Hermes' public gateway API, never by re-implementing it.
- Pairing/linking: list pending, approve, revoke, clear pending.
- A React "Channels" surface per agent.
- A Phase-0 compatibility test pinning the exact Hermes gateway/messaging/
  pairing contract this feature rides.

Out of scope (may follow in later plans): in-browser OAuth channel flows,
per-channel message history views, channel-specific rich formatting settings,
and voice channels (see `plans/006_voice_io/`).

## Phase overview

- **Phase 0 — Pin & prove.** Pin the Hermes artifact and add a compatibility
  test asserting the gateway lifecycle endpoints, messaging catalog/update/test
  endpoints, onboarding endpoints, pairing endpoints, and the public
  `gateway.config` / `hermes_cli.config` primitives exist and behave. Fail
  startup readiness with one remediation line if the contract is incompatible.
- **Phase 1 — Read path.** Models, integration adapter (`integrations/gateway.py`),
  service (`services/channels.py`), handler operations, routes: list channels
  and gateway status per agent. No mutations yet.
- **Phase 2 — Enable/credentials.** Enable/disable a channel; set/rotate/clear
  credentials with snapshot-before-write; multiplex port-binding guard.
- **Phase 3 — Lifecycle & pairing.** Gateway start/stop/restart/drain; pairing
  list/approve/revoke/clear.
- **Phase 4 — Onboarding & UI.** Telegram/WhatsApp guided onboarding routes and
  the React Channels page.
- **Phase 5 — Harden & validate.** Full test matrix, security sweep, docs.

## Definition of done

A user opens an agent's Channels page, sees the Hermes channel catalog with
per-channel state, pastes a real Telegram bot token (or completes the guided
QR onboarding), enables Telegram, and the service writes the token to the
agent's profile `.env` and `platforms.telegram.enabled=true` in its
`config.yaml` (snapshotted first), restarts the gateway through Hermes' public
API, approves the pairing code, and then a message sent to that bot from a
phone is answered by that agent — with the bot token never appearing in any
API response, log line, error body, or exported bundle. `make check` and the
Phase-0 compatibility test pass, and the same channel state is visible to the
native Hermes dashboard and `hermes gateway` CLI without synchronization.
</content>
</invoke>
