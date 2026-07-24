# 006 — Voice I/O (text-to-speech and speech-to-text)

Priority: P2. Independent of the Kanban program (001–004) and messaging (005).
Does not block them and is not blocked by them. Depends only on the shared
Brain4All spine that already ships: `brain4all/routes/setup.py`,
`brain4all/handlers/api.py`, `brain4all/services/platform.py`,
`brain4all/integrations/`, and the pinned Hermes runtime.

## Goal

Give agents a voice. Two capabilities, both already present in the pinned
Hermes runtime but **not exposed through the Brain4All contract or UI**:

- **Text-to-speech (TTS)** — the agent speaks its replies. A user reads a chat
  message, presses a play button, and hears it. Real Hermes user story:
  voice-first use from a terminal or the desktop app.
- **Speech-to-text (STT)** — the user records a voice note in the browser and
  Brain4All transcribes it into text that becomes a chat message. Real Hermes
  user story: hands-free input and blind/low-vision accessibility.

The work is an **adapter + contract + UI** layer. All synthesis and
transcription stays inside Hermes. Brain4All rides the existing Hermes audio
capability, adds a stable versioned API, per-agent voice configuration, and a
minimal chat UI (mic button, play button, Voice settings panel).

## Mandatory constraints (obey; see `../../AGENTS.md`)

- No Go, PostgreSQL, ORM, or second API process. One FastAPI/Hermes process on
  `:8642` and one 9router process on `:20128`.
- Preserve the Hermes core. **Extend** from `brain4all/`; never copy or fork
  Hermes internals. Ride the Hermes native audio capability.
- Provider forced to 9router for LLM inference. Voice providers are a separate
  Hermes concern (Edge/ElevenLabs/neutts/etc.) and are configured, not forked.
- `brain4all/routes/setup.py` is the only route-assembly point. Handlers do
  HTTP, services own rules, repositories own atomic files, integrations adapt
  Hermes/9router, models are Pydantic.
- No application database. Per-agent voice config lives in each agent profile's
  `config.yaml` written atomically (temp → fsync → rename) with a snapshot
  before any persistence-promising mutation.
- **Never log or return provider credentials or keys** (`ELEVENLABS_API_KEY`,
  etc.). Voice config exposes only non-secret fields (provider name, voice id).
- Pin Hermes and add a compatibility test for every Hermes audio symbol used.
- No mock/demo audio data.

## Non-goals

- No new voice provider implementations. We use Hermes' existing providers.
- No always-on live "conversation mode" duplex loop in this plan. (Hermes has a
  `/api/audio/speak-stream` WebSocket; streaming TTS is an optional Phase 3,
  not required for done.)
- No telephony, no wake-word, no server-side microphone. Recording happens in
  the browser via `MediaRecorder`.
- No storage of audio blobs on the server beyond the transient temp files
  Hermes already creates and deletes during synthesis/transcription.
- No changes to 9router or LLM inference behavior.

## Scope

In scope:

1. A Brain4All integration adapter over the Hermes audio tools and registries.
2. Versioned Brain4All routes: list voice providers, list ElevenLabs voices,
   synthesize speech, transcribe audio, get/set per-agent voice config.
3. Per-agent voice config persisted natively in the agent profile `config.yaml`
   under the Hermes `tts:` / `stt:` keys.
4. A chat UI: a play button on assistant messages, a mic/record button in the
   composer, and a Voice settings panel per agent.
5. Compatibility, unit, integration, and manual verification.

Out of scope: everything under Non-goals.

## Phase overview

- **Phase 0 — Pin + compatibility gate.** Confirm the pinned Hermes exposes the
  audio endpoints, tools, and registries this plan uses; add a compatibility
  test. Decide the per-agent-config mechanism (see `approaches.md` — the one
  upstream-extension decision lives here). No feature code until this passes.
- **Phase 1 — Backend contract (TTS + STT + voices/providers).** Models,
  integration adapter, service, handlers, routes. Return audio as base64 data
  URLs (matches Hermes). Global/root voice config works end to end.
- **Phase 2 — Per-agent voice config + chat UI.** Get/set per-agent `tts:`/`stt:`
  config; play button on messages; mic recorder in composer that transcribes to
  a message; Voice settings panel.
- **Phase 3 (optional) — Streaming TTS.** Bridge `/api/audio/speak-stream` for
  low-latency playback. Not required for done.

## The six files

- `README.md` — this file: goal, constraints, scope, phases, done.
- `findings.md` — what Hermes provides (endpoints, tools, registries, shapes),
  what Brain4All has/lacks, the exact gap, credential handling, APIs to pin,
  risks, open questions.
- `architecture.md` — layering fit, TTS/STT data flow, where voice config
  lives, the new API contract with Pydantic names, adapter methods, UI surface,
  ASCII sequence diagrams.
- `approaches.md` — options and trade-offs, then the chosen approach.
- `implementation.md` — ordered, phased, file-by-file steps with real paths and
  exact `Route(...)` lines.
- `validation.md` — verification, tests, `make` targets, manual steps, a
  key-leak check, and an acceptance checklist.

## Definition of done

- [ ] Hermes is pinned; a compatibility test imports and exercises every audio
      symbol used (`text_to_speech_tool`, `transcribe_recording`/
      `transcribe_audio`, both registries' `list_providers`, and the
      `/api/audio/*` HTTP shapes) and fails startup readiness with one clear
      message if incompatible.
- [ ] `POST /agent-gateway/v1/voice/speak` returns synthesized audio for a text
      input using the configured provider.
- [ ] `POST /agent-gateway/v1/voice/transcribe` returns a transcript for a
      base64 audio recording.
- [ ] `GET /agent-gateway/v1/voice/providers` and
      `GET /agent-gateway/v1/voice/voices` return safe, non-secret data.
- [ ] `GET`/`PUT` `/agent-gateway/v1/agents/{agent_id}/voice` read and write
      per-agent voice config atomically with a snapshot, exposing no secrets.
- [ ] In the browser: a play button speaks an assistant reply and it is
      audible; recording a voice note yields a transcript that becomes a chat
      message.
- [ ] No provider key or credential appears in any response, log, or error, and
      a test proves it.
- [ ] `make check` and the plan's focused tests pass.

See `validation.md` for the full checklist with evidence.
