# E04 — Architecture

End-to-end design: browser → OSS thin proxy → enterprise voice gateway →
provider, with entitlement checks, metering, and failure modes. Cross-links:
[README](README.md) · [findings](findings.md) · [approaches](approaches.md) ·
[implementation](implementation.md) · [validation](validation.md).

## 1. Topology

```
 user device (OSS runtime, this repo)                 brain4all-enterprise (Go)              voice provider
┌─────────────────────────────────────┐             ┌───────────────────────────────┐      ┌──────────────┐
│ Browser (src/)                  │             │ voice gateway (internal/voice)│      │ ElevenLabs / │
│  Play btn · mic · settings          │             │  POST /voice/v1/speak         │      │ STT provider │
│    │ gated by `voice` capability    │             │  POST /voice/v1/transcribe    │      │ (Phase 0)    │
│    ▼                                │  HTTPS 443  │  GET  /voice/v1/voices        │      └──────▲───────┘
│ FastAPI thin proxy                  │  outbound   │   ├ entitlement check (voice) │  keys only  │
│  /api/brain/v1/voice/*          ├────────────►│   ├ caps (size/duration/text) │  here ──────┘
│  integrations/enterprise_voice.py   │  device/user│   ├ key vault (encrypted)     │
│  NO provider keys anywhere          │  token      │   ├ provider adapters         │
│                                     │             │   ├ transient buffers only    │
│ non-voice features: zero dependency │             │   └ metering → PostgreSQL     │
│ on anything to the right            │             │      voice_usage + rollups    │
└─────────────────────────────────────┘             └───────────────────────────────┘
```

Provider keys exist **only** inside the gateway's key vault. The OSS runtime
and browser handle audio bytes and text, never credentials. Content is
transient at every hop; only metering metadata persists (§5).

## 2. Sequence diagrams

### 2.1 Speak (TTS)

```
 Browser(ChatArea)      OSS proxy (:8642)              enterprise gateway               provider
    | click Play             |                                |                            |
    |-- POST /api/brain/v1/voice/speak {text, voice_id} ->|                            |
    |                        | capability flag cached? no ────┼─ 403 capability_unavailable|
    |                        | yes: attach device/user token  |                            |
    |                        |-- POST /voice/v1/speak -------->                            |
    |                        |                                | authenticate token          |
    |                        |                                | entitlement: voice? ── 403  |
    |                        |                                | cap: text <= MAX_TTS_CHARS  |
    |                        |                                | decrypt tenant TTS key      |
    |                        |                                |-- provider TTS call ------->|
    |                        |                                |<- audio bytes (mp3) --------|
    |                        |                                | measure secs/chars          |
    |                        |                                | INSERT voice_usage (async-  |
    |                        |                                |  safe, before response ack) |
    |                        |<- 200 audio/mpeg bytes --------|  discard buffer             |
    |<- 200 audio/mpeg ------|  (streamed through, unbuffered |                            |
    | blob → object URL      |   where possible; never on disk)                            |
    | new Audio(url).play()  |                                |                            |
```

### 2.2 Transcribe (STT)

```
 Browser(mic)           OSS proxy (:8642)              enterprise gateway               provider
    | MediaRecorder stop     |                                |                            |
    |-- POST /api/brain/v1/voice/transcribe  ------------>|                            |
    |   multipart: audio blob + mime (webm/mp4)               |                            |
    |                        | size <= 25 MiB else 413        |                            |
    |                        |-- POST /voice/v1/transcribe -->|                            |
    |                        |   multipart passthrough        | auth + entitlement + caps  |
    |                        |                                | decrypt tenant STT key     |
    |                        |                                |-- provider STT call ------>|
    |                        |                                |<- {transcript, duration} --|
    |                        |                                | INSERT voice_usage         |
    |                        |<- 200 {transcript, seconds} ---|  discard audio buffer      |
    |<- 200 envelope --------|                                |                            |
    | transcript → composer textarea; user reviews and sends (normal chat path, per 006)  |
```

Design carried over from `plans/006_voice_io/architecture.md`: transcription
fills the composer for review, never auto-sends.

### 2.3 Voices list

```
 Browser(settings) → OSS proxy GET /api/brain/v1/voice/voices
                   → gateway GET /voice/v1/voices (auth + entitlement)
                   → provider voices catalog (e.g. ElevenLabs /v1/voices, xi-api-key server-side)
                   ← {voices:[{voice_id, name, label}]}   — never a key, mirrors 006's rule
```

## 3. Enterprise gateway internals (`brain4all-enterprise`)

Go package `internal/voice/` (paths per that repo's layout; raw SQL, no ORM
per `docs/enterprise-extension.md`):

- **`handler.go`** — the three routes. Middleware order: authenticate
  (device/user token from E01) → resolve tenant → entitlement check
  (`pkg/edition.Policy` capability `voice`; absent → 403
  `capability_unavailable`) → caps → adapter call → meter → respond.
- **`adapter.go`** — provider-agnostic interface:

  ```go
  type TTSAdapter interface {
      Speak(ctx context.Context, req SpeakReq) (SpeakResult, error) // audio, mime, seconds, chars
      Voices(ctx context.Context) ([]Voice, error)
  }
  type STTAdapter interface {
      Transcribe(ctx context.Context, req TranscribeReq) (TranscribeResult, error) // text, seconds
  }
  ```

  `internal/voice/providers/elevenlabs.go` first; the STT adapter file for
  the Phase-0-chosen provider beside it. Adapters receive the decrypted key
  per call and never log request/response bodies.
- **`vault.go`** — reads/writes `voice_provider_keys` (§4); envelope
  encryption (AES-256-GCM data key wrapped by a master key from KMS/env —
  reuse E01's secret-management choice; Phase 0 confirms it).
- **`caps.go`** — request caps, enforced before any provider call:
  - speak text: `MAX_TTS_CHARS = 5_000` per request (*verify* vs provider
    limits, findings Q3);
  - transcribe upload: `25 MiB` (Hermes parity, 006's
    `_MAX_TRANSCRIPTION_UPLOAD_BYTES`) → 413 over;
  - transcribe duration: 10 minutes per clip (*verify*), enforced from
    provider-reported duration post-hoc for metering sanity;
  - per-request timeout 60 s; per-tenant concurrent-request limit.
- **`meter.go`** — writes `voice_usage` (§4) inside the request, keyed by
  the request UUID (idempotent on retry). A failed metering write fails the
  request (billing-grade: no un-metered audio leaves the gateway).
- **Transient processing** — audio and text live only in request-scoped
  memory buffers; no temp files where avoidable, and any spill file is
  unlinked in a `defer`; nothing written to the database except §4 metadata.

## 4. Metering schema (PostgreSQL, the E02 database)

New migration in `brain4all-enterprise` (extends, never rewrites, the E01/E02
migration history):

```sql
-- org provider keys, encrypted at rest
CREATE TABLE voice_provider_keys (
    tenant_id       UUID        NOT NULL REFERENCES tenants (id),
    provider        TEXT        NOT NULL,              -- 'elevenlabs', ...
    kind            TEXT        NOT NULL CHECK (kind IN ('tts', 'stt')),
    key_ciphertext  BYTEA       NOT NULL,              -- AES-256-GCM envelope
    key_version     INTEGER     NOT NULL DEFAULT 1,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, provider, kind)
);

-- one row per gateway voice request (server-observed, idempotent)
CREATE TABLE voice_usage (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id           UUID          NOT NULL REFERENCES tenants (id),
    user_id             UUID          REFERENCES users (id),      -- null until device claimed
    device_id           UUID          NOT NULL REFERENCES devices (id),
    request_id          UUID          NOT NULL,                   -- idempotency key
    kind                TEXT          NOT NULL CHECK (kind IN ('speak', 'transcribe')),
    provider            TEXT          NOT NULL,
    chars               INTEGER       NOT NULL DEFAULT 0,         -- speak: input text length
    seconds_synthesized NUMERIC(10,3) NOT NULL DEFAULT 0,
    seconds_transcribed NUMERIC(10,3) NOT NULL DEFAULT 0,
    cost_estimate_usd   NUMERIC(12,6),                            -- estimate, never invoice
    pricing_version     TEXT,
    status              TEXT          NOT NULL,                   -- 'ok' | 'provider_error' | 'capped'
    created_at          TIMESTAMPTZ   NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX voice_usage_request_uq ON voice_usage (tenant_id, request_id);
CREATE INDEX voice_usage_rollup_ix ON voice_usage (tenant_id, user_id, device_id, created_at);

-- rollups: extend E02's daily rollup table (shared dashboard read path)
ALTER TABLE usage_rollups_daily
    ADD COLUMN voice_seconds_synthesized NUMERIC(12,3) NOT NULL DEFAULT 0,
    ADD COLUMN voice_seconds_transcribed NUMERIC(12,3) NOT NULL DEFAULT 0,
    ADD COLUMN voice_cost_estimate_usd   NUMERIC(12,6) NOT NULL DEFAULT 0;
```

The E02 rollup job gains one aggregation query over `voice_usage` grouped by
`(tenant_id, user_id, device_id, day)`. Rationale for a separate raw table +
shared rollup: [approaches](approaches.md) Decision B. **No audio, no text,
no transcript column exists anywhere** — that is the retention policy encoded
in the schema (§5).

## 5. Retention policy (content never at rest)

- Server-side: audio and transcript/text are processed **transiently** in
  memory; never written to the database, object storage, or persistent disk;
  request logs record metadata only (request_id, tenant/device ids, kind,
  provider, sizes, durations, status, latency). A redaction test asserts no
  text/audio bytes appear in logs ([validation](validation.md) §5).
- Provider-side: where the provider offers a no-retention/zero-retention
  flag, the adapter sets it (*verify per provider*, findings Q/§6).
- Device-side: the browser holds audio in blobs/object URLs for playback;
  nothing is persisted by Brain4All. Standard non-goal: no audio storage.

## 6. Entitlement check path

1. **Issuance (E01):** the entitlement document gains capability flag
   `voice` (boolean; absent/false = unavailable per entitlements-v1 —
   unknown fields ignored by older clients, so adding the flag is
   backward-compatible).
2. **Authoritative check (gateway):** every `/voice/v1/*` request resolves
   the principal's entitlements via `pkg/edition.Policy`; no `voice` → 403
   body `{"error": "capability_unavailable"}`.
3. **Edge check (OSS proxy):** the runtime caches the signed-in account's
   entitlement document (E01 plumbing); voice handlers return the same 403
   without a network round-trip when the flag is absent. Defense in depth
   plus a fast, offline-correct answer for the UI.
4. **UI gating:** the frontend reads capability flags from the extended
   `limits` payload (§8) and mounts voice components only when
   `capabilities.voice === true`.

## 7. OSS route table and handler ops (this repo)

All registered only in `brain4all/routes/setup.py`, handled in
`brain4all/handlers/api.py`, thin: validate → forward via
`brain4all/integrations/enterprise_voice.py` → translate errors. No service
rules beyond gating and size checks — policy lives in the gateway.

| Method | Path | Operation | Body | Returns |
| --- | --- | --- | --- | --- |
| POST | `/api/brain/v1/voice/speak` | `voice_speak` | JSON `{text, voice_id?}` | raw `audio/mpeg` bytes (`special` raw-response route, `response_model=None`, like `workspace_upload`) |
| POST | `/api/brain/v1/voice/transcribe` | `voice_transcribe` | multipart `audio` file + `mime_type` | envelope `{transcript, seconds}` |
| GET | `/api/brain/v1/voice/voices` | `voice_voices` | — | envelope `{voices: [{voice_id, name, label}]}` |

Error translation at the proxy (stable codes, entitlements-v1 aligned):

| Condition | OSS response |
| --- | --- |
| Not signed in / no `ENTERPRISE_API_URL` | 403 `capability_unavailable` (routes exist but are gated; UI never shows them in this state) |
| Entitlement lacks `voice` | 403 `capability_unavailable` |
| Upload > 25 MiB (checked locally before forwarding) | 413 `audio_too_large` |
| Enterprise API unreachable/timeout | 503 `enterprise_unreachable` (drives the banner) |
| Gateway reports provider failure | 502 `voice_provider_error` (provider-agnostic; no provider error text passthrough) |

## 8. Frontend gating (how the browser learns capabilities)

Chosen ([approaches](approaches.md) Decision C): extend the existing limits
payload. `brain4all/handlers/api.py` line 97 (`"limits"` op, route
`GET /api/brain/v1/limits` at `brain4all/routes/setup.py` line 40) currently
returns a static dict; it gains a `capabilities` object populated from the
cached entitlement document when signed in, `{}` otherwise:

```json
{ "plan_id": "self-hosted", "local_features_unlimited": true, "agents": -1,
  "teams": -1, "mcp_servers": -1, "cron_jobs": -1,
  "capabilities": { "voice": true } }
```

Flow: `src/api/client.ts` → a `useCapabilities()` hook (or an extension
of whatever hook already consumes limits — Phase 3 checks) → `ChatArea.tsx`
mounts Play/mic only when `capabilities.voice`. A capability present but the
enterprise API currently unreachable → controls render disabled with the
degraded banner (§9), not hidden — the user's entitlement did not change,
connectivity did.

## 9. Failure modes

| Failure | Behavior | User sees |
| --- | --- | --- |
| Enterprise API down | OSS proxy 503 `enterprise_unreachable`; capability cache keeps last value | Voice buttons disabled + banner "Voice is temporarily unavailable (enterprise connection lost)"; **all non-voice features unaffected** (no shared dependency) |
| Provider down / provider error | Gateway 502 `voice_provider_error`, provider-agnostic message, no provider body leaked | Toast "Voice service failed, try again"; metering row written with `status='provider_error'`, zero seconds |
| Oversize upload | 413 at OSS edge (pre-forward) and again at gateway (authoritative) | "Recording too large" message |
| Over-long text | 400 at gateway (`MAX_TTS_CHARS`); UI truncation hint | Localized message |
| Unentitled direct API call | 403 `capability_unavailable` at both layers | (API clients only; UI never offers the action) |
| Metering DB write fails | Request fails (5xx); no un-metered audio served | Retryable error |
| Mic permission denied / no mic | Browser-side handling per 006 (clean localized error) | Localized message, no crash |
