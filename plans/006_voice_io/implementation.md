# 006 — Implementation

Ordered, phased, file-by-file steps with real paths. Do phases in order; each
phase ends green (its tests pass) before the next. Cross-links:
[README](README.md) · [findings](findings.md) · [architecture](architecture.md)
· [approaches](approaches.md) · [validation](validation.md).

Chosen approach (from [approaches.md](approaches.md)): in-process adapter (A2);
per-agent voice via additive upstream override with global fallback (B2/B1);
base64 whole-file audio in the standard envelope (C1).

---

## Phase 0 — Pin + compatibility gate (no feature code until green)

1. **Confirm the pin.** Brain4All already pins Hermes for the Kanban program
   (see `plans/001_kanban_foundation/README.md` Phase 0 and
   `docs/development.md`). Reuse the same pinned commit/release; do not move to
   `main`. Record the exact commit used for voice in `docs/development.md`.

2. **Read the pinned source** for these symbols and confirm signatures/shapes
   match [findings](findings.md) sections 1.1–1.4:
   - `.tools/hermes-agent/hermes_cli/web_server.py`: `/api/audio/speak`,
     `/api/audio/transcribe`, `/api/audio/elevenlabs/voices`,
     `_MAX_TRANSCRIPTION_UPLOAD_BYTES`, `CONFIG_SCHEMA` tts/stt options.
   - `.tools/hermes-agent/tools/tts_tool.py::text_to_speech_tool` — **check
     whether it accepts a `tts_config` kwarg** (decides B2 vs B1 fallback).
   - `.tools/hermes-agent/tools/voice_mode.py::transcribe_recording`.
   - `.tools/hermes-agent/tools/transcription_tools.py::transcribe_audio`,
     `is_stt_enabled`.
   - `.tools/hermes-agent/agent/tts_registry.py::list_providers`,
     `.tools/hermes-agent/agent/transcription_registry.py::list_providers`.

3. **If B2 (per-agent override):** add an additive, backward-compatible
   `tts_config: dict | None = None` param to `text_to_speech_tool` (and the STT
   equivalent) in the `hermes_cli`/`tools` extension surface — `None` preserves
   today's behavior (`_load_tts_config()`), a dict is used as the effective
   config. Keep the change minimal and covered by an upstream test. If this
   cannot land against the pin, skip and let the compatibility test select B1.

4. **Compatibility test** — add
   `brain4all/tests/test_voice_compat.py` (mirror
   `brain4all/tests/test_kanban.py`'s `HERMES_AVAILABLE` skip guard):
   - Import each symbol above; assert callable.
   - Detect whether `text_to_speech_tool` accepts `tts_config`
     (`inspect.signature`) and expose a module constant the service reads to
     pick B2 vs B1.
   - Assert `_MAX_TRANSCRIPTION_UPLOAD_BYTES == 25 * 1024 * 1024` (or record the
     value the service must mirror).
   - With a temp `HERMES_HOME`, exercise a tiny round trip where feasible:
     synth a short string (default `edge` if network-free is not possible, else
     assert the tool returns a `success/file_path` shape) and transcribe a
     small generated WAV, asserting the response keys. Mock only external
     network/model calls; do not mock the tools themselves.

5. **Readiness gate.** In the app startup/health path
   (`brain4all/services/platform.py` health/diagnostics, following the Kanban
   pattern), surface a `voice: { available, degraded_reason }` flag derived from
   the adapter's import check. Do not crash unrelated features when voice is
   unavailable; return a clear one-line reason.

Exit: `python -m pytest brain4all/tests/test_voice_compat.py` passes (or skips
cleanly without the runtime), and the B2/B1 selection constant is known.

---

## Phase 1 — Backend contract (models, integration, service, handlers, routes)

### 1.1 Models — `brain4all/models/api.py`
Add (see [architecture](architecture.md) section 5 for full bodies):
`VoiceSpeakRequest`, `VoiceTranscribeRequest`, `VoiceChannelConfig`,
`VoiceConfigUpdate` (and optional `VoiceProviderConfig`). Use `Field(...)`
constraints and `Literal`/allowlists consistent with `KanbanTaskCreate`.
- `VoiceSpeakRequest.text`: 1..20_000 chars.
- `VoiceTranscribeRequest.data_url`: 1..40_000_000 (base64 of <=25 MiB);
  `mime_type` optional <=128.
Then import them in `brain4all/models/__init__.py` alongside the existing
imports so `__all__` (all non-underscore names) exports them.

### 1.2 Integration — `brain4all/integrations/voice.py` (NEW)
Model after `brain4all/integrations/kanban.py`:
- `class VoiceUnavailable(RuntimeError)`.
- Lazy importers `_tts_tool()`, `_transcribe()`, `_tts_providers()`,
  `_stt_providers()` — each `try: from ... import ...; except Exception: raise
  VoiceUnavailable(...)`.
- Constants mirroring Hermes: `TTS_BUILTINS = (...)`, `STT_BUILTINS = (...)`
  (from findings 1.3), `MAX_TRANSCRIBE_BYTES = 25 * 1024 * 1024`.
- `async def synthesize(text, tts_config=None) -> {data_url, mime_type,
  provider}`:
  - `result_json = await asyncio.to_thread(tool, text)` (pass `tts_config` only
    when the B2 kwarg exists); parse JSON; if `not success`, raise a typed error
    with a **sanitized** message.
  - Read `file_path`, base64-encode, derive mime from extension (same map as
    Hermes: `.mp3->audio/mpeg`, `.ogg/.opus->audio/ogg`, `.wav`, `.flac`),
    `os.unlink(file_path)` in a `finally`.
- `async def transcribe(audio: bytes, mime_type, stt_config=None) ->
  {transcript, provider}`:
  - Write a `NamedTemporaryFile` with a suffix from the mime; call
    `await asyncio.to_thread(transcribe_recording, path)`; unlink in `finally`;
    return `{transcript: result["transcript"].strip(), provider}`.
- `def list_providers() -> {tts:[...], stt:[...]}`: builtins first, then
  `list_providers()` registry names deduped; entries `{name, label, builtin}`.
- `async def elevenlabs_voices() -> {available, voices, error?}`: reuse the
  Hermes fetch logic reading `ELEVENLABS_API_KEY` from env; **never** return the
  key; map 401/403 to `{available:false, error:"unauthorized"}`.

### 1.3 Service — `brain4all/services/voice.py` (NEW)
`class VoiceService` constructed with the same collaborators the Kanban service
gets (agents/repository/config). Methods:
- `async speak(agent_id, text) -> dict`: strip/validate text; load per-agent
  `tts` config via `_agent_voice_config(agent_id)`; call
  `integrations.voice.synthesize(text, tts_config)`; return `{data_url,
  mime_type, provider}`. On `VoiceUnavailable` -> `ServiceError(..., code=
  "voice_unavailable", status=503)`.
- `async transcribe(agent_id, data_url, mime_type) -> dict`: parse the data URL
  exactly like Hermes (`data:` prefix, `;base64`, decode, `audio/*` or
  `video/webm`, non-empty, `<= MAX_TRANSCRIBE_BYTES` -> `audio_too_large` 413);
  load per-agent `stt` config; call `integrations.voice.transcribe(...)`.
- `providers() -> dict`: `integrations.voice.list_providers()`.
- `voices(provider="elevenlabs") -> dict`: for `elevenlabs`,
  `await integrations.voice.elevenlabs_voices()`; other providers -> static
  options from constants.
- `get_agent_voice(agent_id) -> dict`: read
  `DATA_DIR/profiles/<agent-id>/config.yaml`, extract `tts`/`stt`, **strip any
  secret/key fields**, fill defaults (`tts.provider="edge"`,
  `stt.provider="local"`, `enabled=true`), return the safe `VoiceConfig`.
- `set_agent_voice(agent_id, body: dict) -> dict`: accept only
  `enabled/provider/voice_id/model_id` under `tts`/`stt`; **reject** any
  key-like field; snapshot then atomically write `config.yaml`. Prefer reusing
  `PlatformService.update_agent_config` (it already snapshots + writes atomic
  YAML), passing a `{tts:..., stt:...}` merge; otherwise replicate its
  snapshot+`repository.atomic_yaml` sequence. Return `get_agent_voice(...)`.
- `_agent_voice_config(agent_id)`: private helper returning the raw `tts`/`stt`
  dicts for the adapter (used only under B2).

Wire it in `brain4all/services/platform.py`: add
`self.voice = VoiceService(...)` next to `self.kanban = KanbanService(...)`.

### 1.4 Handlers — `brain4all/handlers/api.py`
In `APIHandlers._operation`, add to the `operations` dict (all JSON, use the
normal `dispatch` path — no raw response needed for base64):
```python
"voice_providers":  (lambda: s.voice.providers(), "voice providers retrieved successfully", 200),
"voice_voices":     (lambda: s.voice.voices(q.get("provider", "elevenlabs")), "voices retrieved successfully", 200),
"voice_speak":      (lambda: s.voice.speak(agent(), body["text"]), "speech synthesized successfully", 200),
"voice_transcribe": (lambda: s.voice.transcribe(agent(), body["data_url"], body.get("mime_type")), "audio transcribed successfully", 200),
"agent_voice_get":  (lambda: s.voice.get_agent_voice(p["agent_id"]), "voice config retrieved successfully", 200),
"agent_voice_put":  (lambda: s.voice.set_agent_voice(p["agent_id"], body), "voice config updated successfully", 200),
```
`speak`/`transcribe`/`providers`/`voices` return awaitables where async — the
dispatcher already `await`s awaitable results. `agent()` is the existing lambda
that reads `?agent=` and raises if missing.

### 1.5 Routes — `brain4all/routes/setup.py`
Add these to the `ROUTES` tuple (place a `("Voice",)` tag group; import the new
body models at the top with the others). These are **standard JSON envelope
routes** — no `special`, because base64 audio rides inside `APIEnvelope`:
```python
    Route("GET",  "/api/brain/v1/voice/providers", "voice_providers", tags=("Voice",)),
    Route("GET",  "/api/brain/v1/voice/voices", "voice_voices", tags=("Voice",)),
    Route("POST", "/api/brain/v1/voice/speak", "voice_speak", VoiceSpeakRequest, tags=("Voice",)),
    Route("POST", "/api/brain/v1/voice/transcribe", "voice_transcribe", VoiceTranscribeRequest, tags=("Voice",)),
    Route("GET",  "/api/brain/v1/agents/{agent_id}/voice", "agent_voice_get", tags=("Voice",)),
    Route("PUT",  "/api/brain/v1/agents/{agent_id}/voice", "agent_voice_put", VoiceConfigUpdate, tags=("Voice",)),
```
Update the model import block:
```python
from ..models import (
    ...,
    VoiceSpeakRequest, VoiceTranscribeRequest, VoiceConfigUpdate,
)
```
No change to `_endpoint`/`setup_routes` is needed: body routes use the generic
`dispatch` branch; GET routes use the no-body branch; all use the `APIEnvelope`
response model. (If Phase 3 streaming is added later, that one route uses a
`special="voice_speak_stream"` raw handler like `kanban_stream`.)

**On binary audio (explicit):** whole-file audio is transported as a base64
`data:` URL string inside the JSON envelope (`speak` output, `transcribe`
input). This deliberately mirrors the native Hermes endpoints, so no
`response_model=None` raw route is required for Phases 1–2. Enforce the 25 MiB
decoded cap in the service before decoding fully.

Exit Phase 1: backend integration tests (temp `HERMES_HOME`, real package) pass
for speak/transcribe/providers/voices and config get/set; `make check` green.

---

## Phase 2 — Per-agent config honored + chat UI

### 2.1 Per-agent voice applied
- Under B2, `services/voice.py::speak`/`transcribe` pass the agent's `tts`/`stt`
  config into the adapter; add a test proving agent A (provider X) and agent B
  (provider Y) produce different `provider` in the speak response.
- Under B1 fallback, document that standalone speak uses root config and mark
  the panel accordingly; still ship config get/set.

### 2.2 Frontend API + hook
- `src/api/voice.ts` (NEW): `providers()`, `voices(provider?)`, `speak(agentId,
  text)`, `transcribe(agentId, dataUrl, mimeType)`, `getVoiceConfig(agentId)`,
  `setVoiceConfig(agentId, cfg)` — using `request`/`buildApiUrl` from
  `src/api/client.ts` and the `?agent=` query helper pattern from
  `src/api/conversations.ts`.
- `src/hooks/useVoice.ts` (NEW): load/save voice config; recorder state machine
  (idle -> recording -> transcribing -> done/error); a `speak(text)` helper that
  plays the returned data URL.

### 2.3 Play button — `src/components/ChatArea.tsx`
- Add a Play/Stop button to the assistant message action row (~line 574, beside
  Copy/ThumbsUp/ThumbsDown). On click: `voiceApi.speak(agentId,
  message.content)` -> `const a = new Audio(data_url); a.play()`. Show a spinner
  while pending; toggle to Stop while playing. Hide when the agent's
  `tts.enabled` is false. Guard empty/very-long content.

### 2.4 Mic recorder — `src/components/ChatArea.tsx` composer
- Add a mic button near the send control. Start: `getUserMedia({audio:true})` +
  `new MediaRecorder(stream)`. Stop: assemble the blob, `FileReader.
  readAsDataURL`, call `voiceApi.transcribe(agentId, dataUrl, blob.type)`, then
  set the composer textarea value to the transcript (reuse the existing
  controlled-input path so the user reviews and sends normally). Handle
  permission-denied, no-mic, and empty-transcript (silence) states. Hide when
  `stt.enabled` is false.

### 2.5 Voice settings panel
- A per-agent panel (a section in the existing agent settings surface). Fields:
  TTS enabled, TTS provider (from `voice/providers.tts`), voice id (ElevenLabs
  list from `voice/voices` when provider is `elevenlabs`, else free
  text/select), STT enabled, STT provider (from `voice/providers.stt`), STT
  model id. Load via `getVoiceConfig`, save via `setVoiceConfig`. Never render a
  secret field.
- Add all new UI strings to every `src/locales/*.json` (en, de, es, fr, ja, vi,
  zh).

Exit Phase 2: browser manual steps in [validation.md](validation.md) pass;
frontend component/hook tests and `npm run build` pass.

---

## Phase 3 — Optional streaming TTS (not required for done)
- Add a raw route `special="voice_speak_stream"` bridging Hermes
  `/api/audio/speak-stream`, plus a Web Audio PCM player. Only pursue with a
  stable upstream WS contract and a clear latency win. Out of scope for done.

---

## File change summary

New:
- `brain4all/integrations/voice.py`
- `brain4all/services/voice.py`
- `brain4all/tests/test_voice_compat.py`
- `brain4all/tests/test_voice_api.py` (integration, temp `HERMES_HOME`)
- `src/api/voice.ts`
- `src/hooks/useVoice.ts`
- `src/api/voice.test.ts`, plus a component test for the mic/play controls

Edited:
- `brain4all/models/api.py` (+ voice models), `brain4all/models/__init__.py`
  (import them)
- `brain4all/handlers/api.py` (+6 operations)
- `brain4all/routes/setup.py` (+6 routes, + model imports)
- `brain4all/services/platform.py` (`self.voice = VoiceService(...)`; voice
  health flag)
- `src/components/ChatArea.tsx` (play button + mic recorder)
- agent settings surface (Voice panel), `src/locales/*.json` (all 7)
- Upstream (only under B2): `tts_config`/`stt_config` kwarg in the Hermes tool
  entry points, in the `hermes_cli`/`tools` extension surface
- Docs: `docs/architecture.md`, `docs/api.md`, `docs/development.md` (pinned
  commit, routes, provider/credential notes)
