# E04 — Voice I/O as an enterprise capability

Part of the [enterprise program](../README.md). Cross-links:
[findings](findings.md) · [architecture](architecture.md) ·
[approaches](approaches.md) · [implementation](implementation.md) ·
[validation](validation.md).

## Goal

Give agents a voice — TTS playback of replies and STT dictation into the
composer — delivered as an **enterprise capability**: provider keys
(ElevenLabs, Deepgram, …) live only in the central control plane, all
synthesis/transcription is proxied through an enterprise **voice gateway**
(Go, `brain4all-enterprise`), access is gated by the `voice` capability flag
from `docs/contracts/entitlements-v1.md`, and voice seconds are **metered
into the same central usage database** that E02 builds
(`plans/enterprise/E02_usage_collection/`).

Why enterprise, stated honestly:

- Voice provider keys are **org-managed secrets**. Distributing them to every
  user machine multiplies the blast radius of one leaked laptop; central
  custody keeps them inside the org boundary (`docs/enterprise-extension.md`:
  the enterprise project owns secret management; never attach provider keys
  to spans).
- Voice minutes are **metered and billable**. Central proxying gives
  billing-grade accounting in PostgreSQL — the billing source of truth —
  instead of trusting client-reported counters.
- The control plane already has the machinery (device identity, entitlements,
  usage rollups); voice slots in as one more metered capability.

## Relocation note (supersedes plan 006 for delivery)

This plan **supersedes `plans/006_voice_io/` as the delivery vehicle** by
product decision (see `plans/enterprise/README.md`, build-map item 4, and
`plans/LOCAL_FEATURES_CHECKLIST.md`). Plan 006 is **not deleted**: it remains
the authoritative **Hermes-capability reference** — its `findings.md`
verified the Hermes `/api/audio/*` endpoints, the `tts_registry` /
`transcription_registry`, the per-agent `tts:`/`stt:` config design, and the
UI touchpoints, all of which this plan reuses (see [findings](findings.md)).
006's frontend component design (play button, mic recorder, voice settings)
is reused nearly verbatim; only the backend it talks to changes.

### The AGENTS.md tension, resolved by scoping

`AGENTS.md` says: *local OSS access is unlimited; an Enterprise API outage
must not restrict local features.* Shipping voice as enterprise-only does not
violate this because voice is scoped as **additive enterprise value, never a
restriction of an existing local feature**:

- OSS simply **does not ship** voice UI or Brain4All voice routes. There is
  no local voice feature being taken away or gated — it never existed in the
  Brain4All contract (006 was planned, not implemented).
- When the user is entitled (`voice` capability present) and connected, the
  voice UI appears. When the enterprise API is down, voice buttons degrade
  gracefully with a clear banner while **every non-voice feature keeps
  working** — nothing local depends on the gateway.
- **No DRM pretense:** a self-hosted user who configures TTS/STT providers
  directly in their raw Hermes `config.yaml` (with their own keys in `.env`)
  can still use Hermes' native `/api/audio/*` endpoints. Brain4All does not
  remove Hermes capabilities; it just does not surface them in the OSS UI.
  This is documented plainly as unsupported-but-possible
  ([approaches](approaches.md) Decision E).

## Non-goals

- No realtime streaming voice conversations (duplex "conversation mode") —
  v2 at the earliest. Hermes' `/api/audio/speak-stream` WebSocket is not
  bridged.
- No on-device or local voice models in the enterprise path (Hermes' `neutts`
  / `piper` / local Whisper stay a raw-Hermes concern, per the no-DRM note).
- No voice cloning.
- No storing audio or transcripts server-side: content is processed
  transiently and never persisted; only metadata is logged and metered
  (retention policy in [architecture](architecture.md) §5).
- No short-lived key issuance to devices (weighed and rejected in
  [approaches](approaches.md) Decision A).

## Priority and ordering

**P2 — after E01 and E02.** Hard dependencies:

- **E01** (control-plane foundation): device/user/tenant identity, auth,
  entitlement issuance with capability flags, `pkg/edition.Policy`.
- **E02** (central usage collection): the PostgreSQL usage schema and daily
  rollups that voice metering writes into.

E03 (fleet management) is independent of this plan.

## Phases

- **Phase 0 — Preconditions + verification.** Confirm the entitlement
  document can carry a `voice` capability end to end (E01), confirm the E02
  schema/rollup job is in place to extend, verify provider API facts marked
  "verify" in [findings](findings.md) §6, and re-verify plan-006 Hermes facts
  only if the local-fallback approach is ever revisited.
- **Phase 1 — Enterprise voice gateway** (`brain4all-enterprise`, Go):
  key vault, provider adapters, `POST /voice/v1/speak`,
  `POST /voice/v1/transcribe`, `GET /voice/v1/voices`, entitlement
  enforcement, caps, metering writes into `voice_usage`.
- **Phase 2 — OSS thin proxy** (this repo, Python):
  `brain4all/integrations/enterprise_voice.py`, three proxied routes under
  `/api/brain/v1/voice/*`, 403 `capability_unavailable` without
  entitlement, capability flags exposed via the `limits` payload.
- **Phase 3 — Gated frontend.** Reuse plan 006's UI design (play button in
  `ChatArea.tsx`, mic recorder in the composer, voice settings) rendered only
  when the `voice` capability flag is present; degradation banner.
- **Phase 4 — Hardening + acceptance.** Redaction tests (no text/audio in
  gateway logs), metering-accuracy tests, storage scan, e2e evidence per
  [validation](validation.md).

## Definition of done

- [ ] An **entitled, signed-in** user clicks Play on an assistant reply and
      hears it, and dictates a message via the mic that lands in the composer
      as text — with **no voice provider key ever present on the device**
      (grep of `DATA_DIR` and the runtime environment proves it).
- [ ] An **unentitled** user sees **no voice UI at all**; a direct API call
      to the OSS voice routes returns `403 capability_unavailable`
      (entitlements-v1 semantics).
- [ ] **Voice minutes appear in the central usage dashboard**: N seconds
      synthesized/transcribed on a device show up as N (± rounding) in the
      E02 PostgreSQL rollups for that tenant/user/device.
- [ ] With the enterprise API **down**, voice controls show a clear degraded
      banner and every non-voice feature works unchanged.
- [ ] No audio or transcript content is persisted server-side; gateway logs
      contain metadata only (redaction test + storage scan evidence).

Full checklist with evidence in [validation.md](validation.md).
