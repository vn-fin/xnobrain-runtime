# E04 — Findings

What is already verified (mostly by plan 006), what the enterprise stack
provides, and what remains open. Cross-links: [README](README.md) ·
[architecture](architecture.md) · [approaches](approaches.md) ·
[implementation](implementation.md) · [validation](validation.md).

Sources: `plans/006_voice_io/findings.md` (Hermes ground truth, read from the
pinned source under `.tools/hermes-agent/`), `docs/contracts/entitlements-v1.md`,
`docs/plans.md`, `docs/enterprise-extension.md`, `plans/enterprise/README.md`
(E02 schema frame), and direct reads of `xnobrain/handlers/api.py` and
`xnobrain/routes/setup.py` in this repo.

## 1. What plan 006 verified about Hermes audio (reused, not re-derived)

All of the following was read directly from the pinned Hermes source by plan
006 (`plans/006_voice_io/findings.md` §1); cited here because it defines what
the *raw Hermes* voice path can do and what E04 deliberately does not use:

- **`POST /api/audio/speak`** (`web_server.py::speak_text`, ~line 4448) —
  `{text}` in, `{ok, data_url, mime_type, provider}` out (base64 data URL,
  mime from extension: `.mp3→audio/mpeg`, `.ogg/.opus→audio/ogg`, `.wav`,
  `.flac`). Calls `tools.tts_tool.text_to_speech_tool(text)` in an executor;
  reads `tts:` from the root `config.yaml`; temp file unlinked after encode.
- **`POST /api/audio/transcribe`** (`transcribe_audio_upload`, ~line 4269) —
  `{data_url, mime_type}` in; requires `data:<audio-mime>;base64,...`,
  rejects > 25 MiB (`_MAX_TRANSCRIPTION_UPLOAD_BYTES = 25 * 1024 * 1024`).
  Calls `tools.voice_mode.transcribe_recording(temp_path)` (the
  hallucination-filtered wrapper over `tools.transcription_tools.
  transcribe_audio`); returns `{ok, transcript, provider}`; temp file always
  unlinked.
- **`GET /api/audio/elevenlabs/voices`** (~line 4380) — reads
  `ELEVENLABS_API_KEY` from env, calls `https://api.elevenlabs.io/v1/voices`
  with an `xi-api-key` header **server-side only**; returns
  `{available, voices:[{voice_id, name, label}]}`; key never returned.
- **`WebSocket /api/audio/speak-stream`** (~line 4533) — streaming int16 PCM
  TTS. Out of scope here (README non-goals) but noted for v2.
- **Registries:** `agent/tts_registry.py::list_providers` (builtins: `edge,
  elevenlabs, openai, minimax, xai, mistral, gemini, neutts, kittentts,
  piper, deepinfra`) and `agent/transcription_registry.py::list_providers`
  (builtins: `local, local_command, groq, openai, mistral, xai, elevenlabs,
  deepinfra`). There is **no** `/api/audio/providers` endpoint; option lists
  live in `CONFIG_SCHEMA` in `web_server.py`.
- **Per-agent config design (006 §4):** the Hermes tools read the **root**
  `config.yaml` via `hermes_cli.config.load_config()`; 006 designed per-agent
  `tts:`/`stt:` sections in `DATA_DIR/profiles/<agent-id>/config.yaml` and an
  additive upstream `tts_config`/`stt_config` override kwarg (its approach
  B2). E04 reuses only the *shape* of the per-agent preference (a voice id
  per agent) — synthesis no longer happens in Hermes, so the override kwarg
  is **not needed** here.
- **UI touchpoints (006 §2):** assistant-message action row in
  `src/components/ChatArea.tsx` (Copy/ThumbsUp/ThumbsDown, ~line 574) is
  the home for the Play button; composer in the same file hosts the mic; no
  `MediaRecorder`/`getUserMedia`/`new Audio` usage exists yet; 7 locales
  under `src/locales/{en,de,es,fr,ja,vi,zh}.json`; envelope-aware client
  in `src/api/client.ts` (`request`, `requestRaw`, `requestMultipart`,
  `buildApiUrl`). (006 wrote these as `src/api/...`; the tree root is
  `src/` — same files.)

**What E04 keeps from all this:** the browser-side capture/playback design,
the 25 MiB upload cap value (adopted as gateway parity), the mime map, the
key-custody rule (keys server-side, never in responses), and the per-agent
voice-preference shape. **What E04 does not use:** the Hermes tools,
registries, and `/api/audio/*` routes — in enterprise mode the provider call
happens in the central gateway, not on the device
([approaches](approaches.md) Decision A).

## 2. The entitlements capability mechanism

From `docs/contracts/entitlements-v1.md`:

- An entitlement document carries **capability flags** alongside numeric
  limits; `voice` becomes a new capability flag. Unknown fields are ignored
  by older clients; unknown *required* capabilities fail closed.
- Error semantics are fixed: unavailable plan capability → **403** with
  stable code `capability_unavailable`; upload size → **413**; missing login
  → **401**; exhausted consumable quota → **429** (`quota_exhausted` — used
  only if voice minute quotas are added later; v1 meters without quota
  enforcement, see [approaches](approaches.md) Decision B note).
- Telemetry and headers are never accounting state — metering rows in
  PostgreSQL are the record.

E01 owns issuing the entitlement document (`pkg/edition.Policy` per
`docs/enterprise-extension.md`); E04 only adds the `voice` flag and enforces
it at the gateway **and** at the OSS proxy edge (defense in depth; the
gateway check is authoritative).

## 3. The metering hook into E02

From `plans/enterprise/README.md` (E02 frame): the control plane's PostgreSQL
holds `usage_session_snapshots`, `usage_rollups_daily`, and the
`devices · users · tenants` identity tables; PostgreSQL is the billing source
of truth; ingest is idempotent (upsert with idempotency keys).

Voice metering differs from session snapshots in one important way: voice
events are **server-observed at the gateway**, not client-reported — the
gateway measures seconds/chars itself while proxying, so there is no outbox,
no watermark, no client trust needed. The gateway writes a `voice_usage` row
(tenant/user/device, kind, provider, seconds, chars, cost estimate) in the
same database, keyed by a per-request UUID for idempotency, and the E02 daily
rollup job is extended to fold voice columns into `usage_rollups_daily` so
dashboards read one rollup table. Exact DDL in
[architecture](architecture.md) §4; shape decision in
[approaches](approaches.md) Decision B.

## 4. Key custody rationale (why central, stated plainly)

- `docs/enterprise-extension.md`: the enterprise project owns **secret
  management**; "never attach provider keys, prompts, memory, or other
  credentials to spans". A gateway that holds keys encrypted at rest and
  calls providers server-side satisfies both by construction.
- Distributing org keys to N user machines (env vars in N containers/PCs)
  means any one compromised device leaks a billable org credential, and
  rotation requires touching the fleet. Central custody: one vault row,
  instant rotation, zero fleet churn.
- Metering honesty: with central proxying the org bills from what its own
  gateway measured, not from client-reported counters.
- Trade-off admitted: every voice request takes an extra hop
  (device → gateway → provider). For click-to-play TTS and stop-then-
  transcribe STT this adds one WAN round-trip to an operation already
  dominated by provider synthesis/transcription time — acceptable; realtime
  streaming (where it would not be) is a non-goal. Full trade table in
  [approaches](approaches.md) Decision A.

## 5. The AGENTS.md tension and its resolution (verified wording)

`AGENTS.md` "Deployment boundaries": *"Local OSS access is unlimited.
Enterprise behavior is optional; an Enterprise API outage must not restrict
local features."* And `docs/plans.md`: *"Self-hosted local Hermes features:
Unlimited"* on every plan row.

Resolution (also in [README](README.md)):

1. Voice was never an OSS XNOBrain feature — plan 006 was a design, not an
   implementation (`grep` confirms no voice routes, no `integrations/voice.py`,
   no voice UI exist in this repo). E04 therefore restricts nothing; it adds.
2. OSS builds ship no voice UI/routes. Entitled + connected → UI appears.
   Enterprise down → voice controls degrade with a banner; all non-voice
   features are untouched (they share no code path with the gateway client).
3. Raw Hermes' native `/api/audio/*` with user-supplied keys keeps working
   for anyone who configures it by hand — unsupported by XNOBrain but not
   blocked. "Self-hosted local Hermes features: Unlimited" stays literally
   true.

## 6. Provider landscape (facts from plan 006 only; everything else "verify")

From 006 (verified in Hermes source): ElevenLabs exposes a voices catalog at
`https://api.elevenlabs.io/v1/voices` authenticated via `xi-api-key`; Hermes'
cloud TTS roster is `elevenlabs, openai, xai, minimax, mistral, gemini` and
cloud STT `groq, openai, xai, elevenlabs`; ElevenLabs STT model ids seen:
`scribe_v2`, `scribe_v1`.

**Not verified — Phase 0 "verify" items (no new provider claims made here):**

- Deepgram STT API shape, auth header, and duration reporting — *verify*.
- ElevenLabs TTS pricing unit (per character?), rate limits, max input
  length per request — *verify*.
- Whether each chosen provider returns authoritative audio duration in its
  response (needed for metering accuracy) or whether the gateway must decode
  and measure the audio itself — *verify per provider*.
- Provider-side data-retention defaults (some providers store audio unless
  opted out) — *verify and set the no-retention flags where offered*.

The v1 gateway ships with **one TTS adapter (ElevenLabs) and one STT adapter
(provider chosen in Phase 0 after verification; Deepgram is the candidate
named by the product decision)** behind a provider-agnostic interface, so the
roster can grow without contract changes.

## 7. What this repo has today (verified by direct read)

- `xnobrain/routes/setup.py` line 40: `Route("GET", "/api/brain/v1/limits",
  "limits", tags=("System",))` — the limits route exists.
- `xnobrain/handlers/api.py` line 97: the `"limits"` operation returns a
  static dict `{"plan_id": "self-hosted", "local_features_unlimited": True,
  "agents": -1, ...}`. This is the natural place to surface capability flags
  when signed in ([approaches](approaches.md) Decision C).
- `xnobrain/integrations/` contains `analytics.py, config.py, hermes.py,
  kanban.py, nine_router.py, runtime.py` — **no enterprise client of any
  kind yet**; `ENTERPRISE_API_URL` appears only in
  `xnobrain/tests/test_fastapi.py`. `enterprise_voice.py` will be the first
  enterprise integration in this repo — its connection/auth plumbing should
  be shaped so E02's usage reporter can share it (Phase 0 check with E02's
  implementation state).
- Frontend: `src/api/client.ts` has `request`, `requestRaw`,
  `requestMultipart` — multipart upload and raw (non-envelope) responses are
  already supported client-side, which Decision D relies on.

## 8. Open questions

- **Q1 — Audio formats.** Which container/codec does each provider accept
  for STT and emit for TTS? Browser `MediaRecorder` produces `audio/webm`
  (Opus) on Chrome/Firefox and `audio/mp4` on Safari (006 risk R7). Does the
  chosen STT provider accept both natively, or must the gateway transcode
  (adds a dependency — prefer providers that accept webm/mp4 directly)?
  *Verify in Phase 0.*
- **Q2 — Browser codec support for playback.** `audio/mpeg` decodes
  everywhere; if a provider emits Opus/OGG, Safari support must be checked.
  Default: request MP3 output from the TTS provider. *Verify.*
- **Q3 — Max payload sizes.** Gateway caps proposed in
  [architecture](architecture.md) §3 (25 MiB upload for Hermes parity;
  5,000-char speak text) must be reconciled with actual provider request
  limits — *verify* — and with any proxy/body-size limits in the enterprise
  ingress.
- **Q4 — Where the per-agent voice preference lives.** Options: OSS-side in
  the agent profile (006's `tts:`/`stt:` shape, minus providers/keys — just
  `voice_id`), or centrally per user. Leaning OSS-side profile storage since
  agents are device-local; decided in [approaches](approaches.md) Decision D
  note and finalized in Phase 3.
- **Q5 — Cost estimation.** `cost_estimate_usd` needs per-provider pricing
  tables; pricing is unverified (§6) and changes — store the estimate with a
  `pricing_version` and treat it as an estimate, never an invoice line.
  *Verify pricing at build time.*
