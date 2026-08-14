# E04 — Implementation

Ordered, phased, file-by-file steps across two repositories. Each phase ends
green before the next. Cross-links: [README](README.md) ·
[findings](findings.md) · [architecture](architecture.md) ·
[approaches](approaches.md) · [validation](validation.md).

Chosen approach ([approaches](approaches.md)): central proxy (A1), dedicated
`voice_usage` table + shared rollups (B1), capabilities via the limits
payload (C1), multipart upload / raw `audio/mpeg` response (D2), raw-Hermes
self-config documented not blocked (E2).

Prerequisites: E01 accepted (auth, entitlement issuance, `pkg/edition.Policy`),
E02's `voice_usage`-adjacent tables and rollup job exist
(`usage_rollups_daily`, `tenants/users/devices`).

---

## Phase 0 — Verification (no feature code until resolved)

1. **Entitlement flag plumbing (E01).** Verify the entitlement document can
   carry a new capability flag end to end: add `voice` to the capability set
   in the E01 policy code and confirm an issued document containing it
   reaches the OSS runtime's cached entitlements, and that clients ignoring
   unknown fields (entitlements-v1) are unaffected. Confirm where the OSS
   runtime caches the signed-in entitlement document (E01/E02 client code)
   so `enterprise_voice.py` and the `limits` handler read one cache, not two.
2. **E02 schema state.** Confirm `usage_rollups_daily` and the rollup job
   exist as E02 shipped them; confirm the migration numbering to extend.
3. **Provider verification (findings §6, Q1–Q3, Q5).** For ElevenLabs TTS
   and the candidate STT provider (Deepgram): auth mechanism, accepted
   upload formats (`audio/webm`, `audio/mp4`), output formats (MP3
   availability), max request sizes/lengths, duration reporting in
   responses, retention/no-store options, current pricing (recorded with a
   `pricing_version`). Everything marked *verify* in findings resolves here.
4. **Hermes facts — conditional.** The chosen design does **not** call
   Hermes audio, so no Hermes compatibility gate is needed. IF Decision A is
   ever revisited toward local-fallback/key-issuance (A2), first re-verify
   plan 006's Hermes endpoint facts against the then-pinned runtime
   (`.tools/hermes-agent/hermes_cli/web_server.py` `/api/audio/*` shapes,
   `tools/tts_tool.py`, `tools/voice_mode.py`, both registries) — 006's
   `implementation.md` Phase 0 is the checklist to rerun.
5. **Ingress body-size limits** on the enterprise deployment ≥ 25 MiB + 
   multipart overhead (findings Q3).

Exit: `voice` flag observed in a delivered entitlement document; provider
facts recorded in this file's §Provider notes (append); caps confirmed.

---

## Phase 1 — Enterprise track: the voice gateway (`xnobrain-enterprise`, Go)

Paths follow that repo's `internal/` layout (per
`docs/enterprise-extension.md`; adjust prefixes to the E01-established tree —
raw SQL, no ORM).

1. **Migration** — `migrations/NNNN_voice.sql`: `voice_provider_keys`,
   `voice_usage`, `usage_rollups_daily` voice columns — exact DDL in
   [architecture](architecture.md) §4. Extends the migration history, never
   rewrites it.
2. **Key vault** — `internal/voice/vault.go`: get/set per
   `(tenant, provider, kind)`, AES-256-GCM envelope encryption reusing E01's
   master-key mechanism; admin CRUD endpoints (or CLI) for org admins to set
   keys — write-only from the admin's perspective (a stored key is never
   readable back, only replaceable/deletable).
3. **Provider adapters** — `internal/voice/adapter.go` (interfaces per
   [architecture](architecture.md) §3), `internal/voice/providers/
   elevenlabs.go` (TTS + voices), `internal/voice/providers/<stt>.go`
   (Phase-0 choice). Adapters: context timeouts, no body logging, set
   provider no-retention flags where offered.
4. **Caps** — `internal/voice/caps.go`: `MAX_TTS_CHARS = 5000`,
   `MAX_UPLOAD_BYTES = 25 * 1024 * 1024` (Hermes-parity value from 006),
   clip-duration cap, per-tenant concurrency, 60 s request timeout.
5. **Metering** — `internal/voice/meter.go`: INSERT into `voice_usage` with
   `ON CONFLICT (tenant_id, request_id) DO NOTHING`; request fails if the
   write fails (no un-metered audio). Extend the E02 rollup job with the
   voice aggregation ([architecture](architecture.md) §4).
6. **Handlers + routes** — `internal/voice/handler.go` wiring
   `POST /voice/v1/speak`, `POST /voice/v1/transcribe`,
   `GET /voice/v1/voices` into the E01 router with middleware order:
   auth → tenant → entitlement (`voice` else 403 `capability_unavailable`)
   → caps → adapter → meter → respond. Speak responds `audio/mpeg` bytes;
   transcribe accepts multipart and responds JSON.
7. **Dashboard** — add voice columns/panels to the E02 usage dashboard
   (per-user/per-device seconds synthesized/transcribed, est. cost).
8. **Go tests with fake providers** — a fake TTS/STT provider `httptest`
   server (returns fixed audio bytes / fixed transcript with declared
   durations):
   - happy paths for all three routes;
   - 403 without the `voice` capability; 401 without auth;
   - 413 oversize; 400 over-long text; 502 mapped provider failure with a
     provider-agnostic body;
   - **metering accuracy**: fake provider declares N seconds → exactly one
     `voice_usage` row with N (± rounding to the NUMERIC(10,3) scale);
     retried request with the same `request_id` → still one row;
   - **redaction**: run requests with sentinel text/audio; assert gateway
     log output contains neither the sentinel text nor audio bytes nor any
     vault plaintext;
   - vault round-trip: ciphertext at rest, decrypt-per-call only.

Exit: gateway green against fakes; one manual run against a real provider in
a staging tenant (evidence for [validation](validation.md)).

---

## Phase 2 — OSS track: thin proxy (this repo, Python)

1. **Integration client** — `xnobrain/integrations/enterprise_voice.py`
   (NEW; first enterprise integration in `xnobrain/integrations/` —
   findings §7). Follows the lazy/typed-error style of
   `xnobrain/integrations/kanban.py`:
   - `class EnterpriseVoiceUnavailable(RuntimeError)` (no
     `ENTERPRISE_API_URL`, not signed in, or no `voice` capability — carries
     a stable code) and `class EnterpriseUnreachable(RuntimeError)`.
   - `async def speak(text, voice_id=None) -> tuple[bytes, str]` — POST
     `{ENTERPRISE_API_URL}/voice/v1/speak` with the device/user token
     (shared E01/E02 auth plumbing from Phase 0 item 1); returns
     `(audio_bytes, mime)`.
   - `async def transcribe(audio: bytes, mime_type: str) -> dict` —
     multipart POST; returns `{transcript, seconds}`.
   - `async def voices() -> dict`.
   - `def capability_enabled() -> bool` — reads the cached entitlement
     document; never a network call on the hot path.
   - Never logs bodies; timeouts mapped to `EnterpriseUnreachable`.
2. **Handlers** — `xnobrain/handlers/api.py`:
   - `voice_transcribe` and `voice_voices` as normal operations in the
     `operations` dict (envelope path); `voice_speak` as a dedicated raw
     method (returns `Response(content=..., media_type="audio/mpeg")`,
     registered `response_model=None`) — same pattern as
     `workspace_upload`/`sandbox_*`.
   - Every voice op first checks `capability_enabled()`; else 403
     `capability_unavailable`. Local 25 MiB pre-check → 413. Error mapping
     table in [architecture](architecture.md) §7.
   - **Extend the `limits` op (line 97)**: add
     `"capabilities": <flags from the cached entitlement doc or {}>` to the
     returned dict.
3. **Routes** — `xnobrain/routes/setup.py`, a `("Voice",)` tag group:

   ```python
   Route("POST", "/api/brain/v1/voice/speak", "voice_speak", VoiceSpeakRequest, special="voice_speak", tags=("Voice",)),
   Route("POST", "/api/brain/v1/voice/transcribe", "voice_transcribe", special="voice_transcribe", tags=("Voice",)),
   Route("GET",  "/api/brain/v1/voice/voices", "voice_voices", tags=("Voice",)),
   ```

   (`special` markers per the established raw/multipart registration
   mechanics in `setup.py` — transcribe needs multipart handling, speak a
   raw response; match the exact `special`/`response_model=None` plumbing
   the file already uses for uploads/streams.)
4. **Models** — `xnobrain/models/api.py`: `VoiceSpeakRequest`
   (`text: 1..5_000`, `voice_id: str | None ≤ 256`) — mirroring the gateway
   cap; export via `xnobrain/models/__init__.py`.
5. **Per-agent preference** (Decision D note): store `tts.enabled`,
   `stt.enabled`, `voice_id` in the agent profile `config.yaml` via the
   existing `PlatformService.update_agent_config` snapshot+atomic path;
   reuse 006's GET/PUT `/api/brain/v1/agents/{agent_id}/voice` route
   design with a model that accepts **only** those fields (never provider
   keys — 006's secret-stripping rule).
6. **Python tests** — `xnobrain/tests/test_enterprise_voice.py` with a
   fake enterprise gateway (`httpx.MockTransport` or a local ASGI fake):
   403 without capability; 413 oversize; 503 on unreachable; happy paths;
   limits payload contains `capabilities`; a redaction check that no token
   or audio bytes appear in logs; config PUT rejects key-like fields.

Exit: `make test` green; OSS builds with no `ENTERPRISE_API_URL` behave
exactly as today (routes 403, `capabilities: {}`, zero UI change).

---

## Phase 3 — OSS track: gated frontend (reuse 006's component design)

File list from `plans/006_voice_io/implementation.md` (paths normalized to
the actual `src/` tree root), plus the gating changes:

1. **`src/api/voice.ts`** (NEW, from 006) — `speak(text, voiceId?)`
   (via `requestRaw`, returns a Blob), `transcribe(blob, mime)` (via
   `requestMultipart`), `voices()`; `getVoiceConfig(agentId)` /
   `setVoiceConfig(agentId, cfg)` for the per-agent preference.
2. **`src/hooks/useVoice.ts`** (NEW, from 006) — recorder state machine
   (idle → recording → transcribing → done/error), `speak` playback helper
   (`URL.createObjectURL` instead of 006's data-URL, per Decision D2).
   **New vs 006:** `useCapabilities()` (or extend the existing
   limits-consuming hook found in Phase 3 recon) exposing
   `capabilities.voice` and an `enterpriseReachable` flag.
3. **`src/components/ChatArea.tsx`** (EDIT, from 006) — Play/Stop in
   the assistant action row (~line 574) and mic button in the composer.
   **Gating change:** both render only when `capabilities.voice === true`;
   when the last voice call failed with `enterprise_unreachable`, render
   disabled with the degraded banner ([architecture](architecture.md) §8–9).
4. **Voice settings** (from 006) — per-agent section in the existing agent
   settings surface: TTS/STT enabled toggles and voice picker fed by
   `voices()`. No provider selection (the org's gateway decides providers)
   and no key fields ever. Rendered only with the capability.
5. **Degradation banner** — a small shared banner/toast for
   `enterprise_unreachable` ("Voice is temporarily unavailable...").
6. **i18n** — all new strings added to every locale:
   `src/locales/{en,de,es,fr,ja,vi,zh}.json` (7 files, per 006).
7. **Frontend tests** — `src/api/voice.test.ts` (multipart/raw shaping,
   error mapping) and a component test: with `capabilities: {}` no voice
   control mounts; with `{voice: true}` they do; mock
   `getUserMedia`/`MediaRecorder` per 006's validation §4.
   `npm run build` for type/build verification.

Exit: manual browser pass per [validation](validation.md) §6.

---

## Phase 4 — Hardening + acceptance

- Run the full [validation](validation.md) checklist and record evidence.
- Key tests called out (both already written in Phases 1–2, now run as
  gates): **redaction test** — sentinel text/audio never appears in gateway
  logs; **metering-accuracy test** — N provider-reported seconds → N in
  `voice_usage` ± rounding, once, even under retry.
- Device key-absence sweep: grep `DATA_DIR` and the runtime environment for
  provider key names/values ([validation](validation.md) §3).
- Storage scan on the gateway host: no audio/transcript at rest.
- Docs: update `docs/plans.md` (voice row in the plan matrix),
  `docs/api.md`/`docs/architecture.md` (OSS routes), the E2 honesty note
  about raw-Hermes self-config (Decision E), and mark
  `plans/LOCAL_FEATURES_CHECKLIST.md` 006 row as relocated to E04 (program
  README checklist item).

---

## File change summary

**`xnobrain-enterprise` (Go):** new `migrations/NNNN_voice.sql`,
`internal/voice/{handler,adapter,vault,caps,meter}.go`,
`internal/voice/providers/{elevenlabs,<stt>}.go`, tests with fake providers;
edits to the router wiring, entitlement capability set (E01 policy), E02
rollup job, usage dashboard.

**`xnobrain` (this repo, Python/TS):** new
`xnobrain/integrations/enterprise_voice.py`,
`xnobrain/tests/test_enterprise_voice.py`, `src/api/voice.ts`,
`src/hooks/useVoice.ts`, `src/api/voice.test.ts`; edits to
`xnobrain/models/api.py` + `xnobrain/models/__init__.py`,
`xnobrain/handlers/api.py` (3 voice ops + limits `capabilities`),
`xnobrain/routes/setup.py` (voice routes), agent settings surface,
`src/components/ChatArea.tsx`, all 7 `src/locales/*.json`, docs.
