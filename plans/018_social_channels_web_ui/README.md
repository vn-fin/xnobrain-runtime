# 018 — Social channels in the web UI (generic framework, Zalo first)

Status: Phase 2 Runtime pairing-management API implemented. The generic UI
framework, Zalo Bot card, overview route, and Big Brother connect card remain
UI work. Depends on the Runtime channel routes for `zalo_bot` (in progress on
`feat/zalo-bot-platform`) and on plan
[017](../017_plugin_capabilities_over_hermes/README.md) for the later move of
the bot token to Control. UI code lives in `xnobrain-ui`; no React source
belongs in Runtime.

## Goal

A user connects a social channel to an agent from the web app, without a
terminal and without typing a credential into chat. Zalo comes first, in two
forms: the **personal account** (QR login, already shipped) and the **Zalo Bot**
(official Bot API, token). The structure must take the next provider
(Telegram, Slack, ...) as a small addition, not a new page.

## Findings

Verified in `xnobrain-ui` (branch `feat/zalo-channels`) and Runtime:

- The only channel UI is `AgentChannelsSection` (282 lines), mounted in
  `AgentInspectorSettings` (agent settings), driven by `useZaloChannel`
  (266 lines) and `src/api/channels.ts` (177 lines). Everything is Zalo-only:
  `ChannelState.platform: 'zalo'`, `ZaloChannelStatus`, `ZaloQrCode`,
  `setZaloEnabled`, and `channels.zalo.*` strings in `en`, `vi`, `zh`.
- The hook owns one snapshot per agent, aborts in-flight requests, and polls
  only while the bridge shows a QR code, with a bounded restart wait.
- Messaging channels are behind an experimental feature flag
  (`src/featureFlags.ts`, comment "stay hidden until explicitly enabled").
- Runtime exposes per-platform routes: `GET /agents/{id}/channels`,
  `PUT|PATCH /agents/{id}/channels/zalo`, `GET .../zalo/qr`,
  `POST .../zalo/logout`. The `zalo_bot` equivalents (without QR; with a
  write-only `bot_token`, a `token_set` flag, and statuses `needs_token` and
  `configured`) are being added.
- All channels of a workspace share one gateway; the snapshot carries its
  `running` and `pending_restart` state.
- The Big Brother connect card (plan 016) already shows credential and OAuth
  entry inside chat without the secret touching the conversation.

## Design

### Principle: one generic framework

The channels UI is a framework for any social or messaging provider. The shared
layer (API types, hook, card, registry) has no Zalo-specific names, strings or
branches; Zalo personal and Zalo Bot are only the first registered descriptors.
A new provider adds a descriptor, its own i18n keys under
`channels.<provider>.*`, and, if it needs a new auth step, one small component.
A genericity test injects a fake provider to prove the shared code needs no
change, and a text check forbids `=== 'zalo'` comparisons in shared files.

### Provider descriptors, not per-provider components

Add `src/features/channels/providers.ts`: one descriptor per provider.

| Field | Zalo personal | Zalo Bot |
| --- | --- | --- |
| `id` | `zalo` | `zalo_bot` |
| `auth` | `qr` | `token` |
| statuses | starting, needs_login, qr, connected, closed | needs_token, configured |
| settings | dm policy, allowed users, home channel | same, plus pairing approvals |
| risk notice | unofficial personal-account session | none (official API) |

A single `ChannelCard` renders any descriptor: header with status badge,
enable switch, auth step, settings form, gateway banners. The auth step is the
only provider-specific part (`QrLogin`, `TokenForm`). The current Zalo behaviour
and copy stay unchanged in Phase 0. A server-side descriptor endpoint is
deferred until a third provider exists (YAGNI).

### Screens

1. **Agent settings → Channels** (existing placement): one card per provider,
   Zalo personal and Zalo Bot first. Shows only for agents the user owns.
2. **Workspace Channels overview** (new route, e.g. `/channels`): a table of
   agents × providers with status, gateway health, and a link into each card.
   Read-only plus quick enable/disable.
3. **Chat entry point:** Big Brother can request a channel connection with the
   same connect card used for MCP (new `kind: channel`); the card opens the
   provider form. The token is never typed in chat.

### Zalo Bot flow

1. Enable the channel (status `needs_token`).
2. Paste the bot token in a password field (`autocomplete="off"`, not echoed,
   cleared from state right after the request, never placed in a URL, log or
   error text). After saving only "Token saved" with Replace and Remove
   actions is shown; the value is never read back.
3. Choose DM policy (`pairing` default, `allowlist`) and allowed users; `open`
   is not offered.
4. Status becomes `configured` once the gateway has restarted. Pairing
   approvals appear as a list with Approve and Deny. The Runtime endpoint exists
   (Phase 2): it lists sanitized pending/approved senders and supports approve,
   per-request deny, and revoke through the pinned Hermes `PairingStore`.
5. Logout clears the token and disables the channel after a confirmation.

Phase 2 notes: Runtime reuses `gateway.pairing.PairingStore` in the same scope
the gateway uses. API and gateway mutations share one per-store cross-process
flock at `<pairing-dir>/.xnobrain-pairing.lock`; the launcher wraps pinned
gateway methods and the constructor's legacy-directory migration before
startup, and the lock fails closed on timeout. The
pinned engine has no public per-request deny primitive; Runtime's adapter
removes exactly one pending request under the same file lock and the store's
own `_lock`/atomic save, never a clear-all or approve-then-revoke sequence. A
future engine `deny_request` plus process-shared store lock should replace the
adapter. Approve/deny are not replay-idempotent because the engine does not
retain consumed request history; `revoke` is idempotent.

### Statuses and states to cover

`disabled`, `needs_token`, `needs_login`, `qr`, `starting`, `connected`,
`configured`, `closed`, `pending_restart`, `gateway_down`, loading, error,
feature off, organization context (channels hidden), and a permission-denied
state. Every status has copy in `en`, `vi` and `zh`.

## Phases

| Phase | Scope | Size | Needs |
| --- | --- | --- | --- |
| 0 | Generic framework: provider registry, generic API types and hook (`useChannel`), descriptor-driven `ChannelCard`; remove Zalo-named shared exports; Zalo personal stays pixel- and behaviour-identical | S–M | nothing |
| 1 | Zalo Bot card: token form, status, policy and allowed users, logout | M | Runtime `zalo_bot` routes |
| 2 | Pairing approvals list (Runtime list/approve/deny/revoke endpoints and UI) | M | Runtime API done; UI remains |
| 3 | Workspace Channels overview page and navigation entry | M | Phase 0 |
| 4 | Big Brother channel connect card (`kind: channel`) | S–M | plan 016 code |
| 5 | Next provider (token-based, e.g. Telegram) as a descriptor plus fields | S–M each | provider plugin in the image |
| 6 | Move the bot token to the Control proxy; UI unchanged | S (UI) | plan 017 Phase 1 |

## Tests

- Component tests per status and per auth step; descriptor-driven rendering;
  keyboard and screen-reader behaviour (labels, status announcements).
- Secret handling: the token input is cleared after submit, absent from the DOM
  after saving, from any error text, from `console` output, and from the request
  URL; password manager and autofill attributes verified.
- Hook tests: abort on unmount, QR polling stops on any non-QR status, bounded
  restart wait, stale responses from a previous agent ignored.
- Locale parity for `en`, `vi`, `zh` (no missing keys).
- Manual run in the dev stack with a real test bot and a personal account.

## Out of scope

Group management and CRM screens of the upstream plugin, message history
browsing, channel analytics, billing for channels, organization-level shared
channels.

## Open questions

1. Is the experimental flag removed for Zalo Bot, or does it keep its own flag?
2. Should channels be hidden in organization context like extensions, or is an
   organization-level channel a goal?
3. Who owns the personal-account risk wording and the final copy in `vi`?
4. Pairing approvals: a Runtime list endpoint, or read the engine's pairing
   store through the existing operation layer?
5. Does the overview page belong in the main navigation or inside Settings?
