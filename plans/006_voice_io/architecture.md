# 006 — Architecture

How Voice I/O fits the XNOBrain layering, the data flows, where config lives,
the new API contract, adapter methods, and the UI surface. Cross-links:
[README](README.md) · [findings](findings.md) · [approaches](approaches.md) ·
[implementation](implementation.md) · [validation](validation.md).

## 1. Layering fit

Follow the existing boundaries exactly (see `../../AGENTS.md`):

```
Browser (src/)
  mic recorder / play button / Voice settings panel
        |  HTTP (JSON, base64 audio)
        v
routes/setup.py          <- the ONLY place URLs are declared
        |
handlers/api.py          <- HTTP translation + success/failure envelope
        |
services/voice.py        <- rules: validation, size/mime caps, config shaping,
        |                     secret stripping, provider selection
integrations/voice.py    <- adapts Hermes audio tools + registries (in-process)
        |
Hermes runtime (.tools/hermes-agent, pinned, read-only)
  tools.tts_tool.text_to_speech_tool
  tools.voice_mode.transcribe_recording / tools.transcription_tools
  agent.tts_registry / agent.transcription_registry
  hermes_cli.config.load_config  (per-agent config.yaml)
```

Rules honored:
- Integration owns no policy and does no HTTP; it lazy-imports Hermes (like
  `integrations/kanban.py::_module`) and raises a typed `VoiceUnavailable`.
- Service owns all rules and never leaks secrets.
- Handlers only translate. Routes are the single contract surface.
- Config is written atomically with a snapshot; no database.

## 2. Where voice config lives

Per-agent, in the agent profile's native Hermes config file:

```
DATA_DIR/profiles/<agent-id>/config.yaml
  tts:
    enabled: true          # xnobrain product flag (agent may speak)
    provider: edge         # one of the Hermes TTS providers
    elevenlabs:
      voice_id: <id>       # non-secret voice id, provider-specific block
  stt:
    enabled: true
    provider: local
    elevenlabs:
      model_id: scribe_v2
```

- These are **native Hermes keys** (`tts:` / `stt:`), so the config stays
  portable and is honored by Hermes when the agent's profile is the active
  `HERMES_HOME`.
- XNOBrain adds only the product-level `enabled` flag semantics on top.
- Global/root defaults remain in the root `config.yaml` and are reached through
  the existing global-config surface — not redesigned here.
- **Secrets never live here.** `ELEVENLABS_API_KEY` and friends stay in `.env`
  / provider credentials. Get/set voice config strips any key-like field.

## 3. Data flow — TTS (text -> audio -> browser playback)

```
User clicks "Play" on an assistant message
  -> src/api/voice.ts speak(agentId, text)
     POST /api/brain/v1/voice/speak?agent=<id>  { text }
       -> handlers.dispatch -> operation "voice_speak"
         -> services/voice.py speak(agent_id, text)
              validate: non-empty, <= MAX_TTS_CHARS
              resolve per-agent tts config (profiles/<id>/config.yaml : tts)
              -> integrations/voice.py synthesize(text, tts_config)
                   await asyncio.to_thread(text_to_speech_tool, text[, tts_config])
                   read file_path bytes -> base64 -> UNLINK file
                   return { data_url, mime_type, provider }
              strip provider secrets from any error
         <- { data_url: "data:audio/mpeg;base64,...", mime_type, provider }
  -> browser: const a = new Audio(data_url); a.play()
```

Audio is returned **base64 inside the JSON envelope** (mirrors Hermes'
`/api/audio/speak`). No file path is ever exposed. Synthesis runs off the event
loop via `asyncio.to_thread`.

## 4. Data flow — STT (browser audio -> text -> chat message)

```
User presses mic, MediaRecorder records, user stops
  -> blob -> FileReader.readAsDataURL -> "data:audio/webm;base64,..."
  -> src/api/voice.ts transcribe(agentId, dataUrl, mimeType)
     POST /api/brain/v1/voice/transcribe?agent=<id>  { data_url, mime_type }
       -> handlers.dispatch -> operation "voice_transcribe"
         -> services/voice.py transcribe(agent_id, data_url, mime_type)
              validate: data: prefix, ;base64, audio/* (or video/webm),
                        decoded size <= MAX_TRANSCRIBE_BYTES (25 MiB, Hermes parity)
              -> integrations/voice.py transcribe(audio_bytes, mime[, stt_config])
                   write temp file (suffix by mime) -> transcribe_recording(path)
                   UNLINK temp file in finally
                   return { transcript, provider }
         <- { transcript: "...", provider }
  -> browser: place transcript into the composer textarea (ChatArea onSend path)
     user reviews, then sends normally (transcript becomes a chat message)
```

Design choice: transcription fills the composer for review; it does **not**
auto-send. That keeps STT accessible and safe (user confirms) and reuses the
existing `onSend` chat path unchanged. (An "auto-send" toggle can come later.)

## 5. The new XNOBrain API contract (versioned)

All under the existing `agent-gateway/v1` prefix, registered only in
`routes/setup.py`. Responses use the standard `APIEnvelope` (base64 audio inside
`data`). Pydantic request models live in `xnobrain/models/api.py`.

| Method | Path | Operation | Body model | Returns (in `data`) |
| --- | --- | --- | --- | --- |
| GET | `/api/brain/v1/voice/providers` | `voice_providers` | — | `{ tts: [{name,label,builtin}], stt: [...] }` |
| GET | `/api/brain/v1/voice/voices` | `voice_voices` | — | `{ available: bool, voices: [{voice_id,name,label}], error? }` |
| POST | `/api/brain/v1/voice/speak` | `voice_speak` | `VoiceSpeakRequest` | `{ data_url, mime_type, provider }` |
| POST | `/api/brain/v1/voice/transcribe` | `voice_transcribe` | `VoiceTranscribeRequest` | `{ transcript, provider }` |
| GET | `/api/brain/v1/agents/{agent_id}/voice` | `agent_voice_get` | — | `VoiceConfig` (safe) |
| PUT | `/api/brain/v1/agents/{agent_id}/voice` | `agent_voice_put` | `VoiceConfigUpdate` | `VoiceConfig` (safe) |

- `speak`/`transcribe` take the agent via the existing `?agent=<id>` query
  convention (same as conversations). `voices` may take `?provider=elevenlabs`
  (default) — extendable later.
- `voices` is provider-scoped and currently proxies the ElevenLabs voice list
  (the one provider with a queryable catalog); other providers return their
  static voice options via config/schema.

### Pydantic model names (`xnobrain/models/api.py`)

```python
class VoiceSpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)

class VoiceTranscribeRequest(BaseModel):
    data_url: str = Field(min_length=1, max_length=40_000_000)  # base64 of <=25MiB
    mime_type: str | None = Field(default=None, max_length=128)

class VoiceProviderConfig(BaseModel):          # one provider's non-secret block
    voice_id: str | None = Field(default=None, max_length=256)
    model_id: str | None = Field(default=None, max_length=128)

class VoiceChannelConfig(BaseModel):           # tts OR stt
    enabled: bool = True
    provider: str | None = Field(default=None, max_length=64)
    voice_id: str | None = Field(default=None, max_length=256)   # convenience
    model_id: str | None = Field(default=None, max_length=128)

class VoiceConfigUpdate(BaseModel):            # PUT body
    tts: VoiceChannelConfig | None = None
    stt: VoiceChannelConfig | None = None

# VoiceConfig is the response shape the service builds (dict), not a request
# model — it mirrors VoiceConfigUpdate but is always fully populated and
# secret-free.
```

Export every new model through `xnobrain/models/__init__.py` (its `__all__`
picks up all non-underscore names automatically once imported there).

## 6. Integration-adapter methods (`xnobrain/integrations/voice.py`)

Thin, in-process, no policy. Mirrors `integrations/kanban.py` style.

```python
class VoiceUnavailable(RuntimeError): ...

def _tts_tool():   # lazy import, raise VoiceUnavailable on ImportError
    from tools.tts_tool import text_to_speech_tool; return text_to_speech_tool
def _transcribe(): from tools.voice_mode import transcribe_recording; return transcribe_recording
def _tts_providers():  from agent.tts_registry import list_providers; return list_providers
def _stt_providers():  from agent.transcription_registry import list_providers; return list_providers

async def synthesize(text: str, tts_config: dict | None = None) -> dict:
    """Return {data_url, mime_type, provider}. Runs the tool off-loop,
    reads+base64-encodes the produced file, then UNLINKS it."""

async def transcribe(audio: bytes, mime_type: str, stt_config: dict | None = None) -> dict:
    """Write a temp file, call transcribe_recording, UNLINK in finally.
    Return {transcript, provider}."""

def list_providers() -> dict:
    """Builtin names (from CONFIG_SCHEMA-equivalent constants) + registry
    list_providers() names, deduped, {tts:[...], stt:[...]}."""

async def elevenlabs_voices(api_key_present_only: bool = True) -> dict:
    """Return {available, voices:[{voice_id,name,label}]} by calling the same
    logic the Hermes endpoint uses; never returns the key."""
```

Notes:
- `tts_config`/`stt_config` params are used only if approach (b) in
  `approaches.md` is taken (upstream override param). If the pin lacks them, the
  adapter calls the tools without overrides and the service documents
  global-voice-only behavior for the standalone speak/transcribe endpoints
  (per-agent voice still applies during agent runs; see approaches).
- The `elevenlabs_voices` helper may re-implement the tiny HTTP fetch locally
  reading `ELEVENLABS_API_KEY` from env, OR (preferred, less duplication)
  self-call the pinned Hermes route shape. Either way, no key crosses the
  boundary.

## 7. Service responsibilities (`xnobrain/services/voice.py`)

- Validate inputs (empty text, data-url shape, mime allowlist, 25 MiB cap
  matching `_MAX_TRANSCRIPTION_UPLOAD_BYTES`).
- Resolve per-agent config: read `profiles/<agent-id>/config.yaml`, extract
  `tts`/`stt` sections, drop any secret-looking keys, fill product defaults
  (`tts.provider` default `edge`, `stt.provider` default `local`,
  `enabled` default true).
- Get/set: on PUT, snapshot `config.yaml` then write atomically (reuse the
  `PlatformService.update_agent_config` snapshot+atomic pattern, or call it).
  **Reject** any incoming key/secret field; only accept
  `enabled/provider/voice_id/model_id`.
- Map integration errors to `ServiceError` with stable codes
  (`voice_unavailable`, `provider_not_configured`, `audio_too_large`,
  `invalid_audio`) and **no secret content**.
- Wire as `PlatformService.voice = VoiceService(agents, repository, config)` in
  `services/platform.py` (mirrors `self.kanban`).

## 8. React UI surface (`src/`)

- **Play button** — add to the assistant-message action row in
  `src/components/ChatArea.tsx` (next to Copy/ThumbsUp/ThumbsDown, ~line 574).
  Calls `voiceApi.speak(agentId, message.content)`, plays the returned
  `data_url` via `new Audio(...)`. Shows a spinner while synthesizing and a
  stop/replay affordance. Hidden when the agent's `tts.enabled` is false.
- **Mic / record button** — add to the composer (near the send control in
  `ChatArea.tsx`). Uses `navigator.mediaDevices.getUserMedia({audio:true})` +
  `MediaRecorder`. On stop: blob -> data URL -> `voiceApi.transcribe(...)` ->
  set the composer textarea value to the transcript (user reviews and sends).
  Handles permission-denied and no-mic states. Hidden when `stt.enabled` is
  false.
- **Voice settings panel** — a per-agent panel (a section in the existing agent
  settings surface). Fields: TTS enabled, TTS provider (select from
  `voice/providers`), voice id (ElevenLabs list from `voice/voices` when that
  provider is chosen, else free text/select), STT enabled, STT provider,
  STT model id. Reads `GET .../voice`, writes `PUT .../voice`.
- **API module** — `src/api/voice.ts` using `request`/`requestRaw` from
  `src/api/client.ts`. **Hook** — `src/hooks/useVoice.ts` (config load/save,
  recorder state). New i18n strings added to all 7 `src/locales/*.json`.

## 9. Sequence diagrams (ASCII)

### 9.1 Speak a reply

```
 UI(ChatArea)        voice.ts        FastAPI/handlers    services/voice   integrations/voice   Hermes tts_tool
     |  click Play      |                  |                   |                 |                    |
     |----------------->|  POST /voice/speak?agent=id {text}    |                 |                    |
     |                  |----------------->|                   |                 |                    |
     |                  |                  | dispatch->voice_speak                |                    |
     |                  |                  |------------------>| speak(id,text)  |                    |
     |                  |                  |                   | read tts config |                    |
     |                  |                  |                   |---------------->| synthesize(text,cfg)|
     |                  |                  |                   |                 |  to_thread(tts_tool)|
     |                  |                  |                   |                 |------------------->|
     |                  |                  |                   |                 | file_path <--------|
     |                  |                  |                   |                 | read+b64, unlink   |
     |                  |                  |                   |<----------------| {data_url,mime,prov}|
     |                  |                  |<------------------| envelope         |                    |
     |                  |<-----------------|  {data:{data_url}}|                 |                    |
     |  new Audio(url).play()             |                   |                 |                    |
```

### 9.2 Transcribe a voice note

```
 UI(mic)           voice.ts        handlers        services/voice     integrations/voice   Hermes voice_mode
   | record->stop    |                |                  |                    |                   |
   | blob->dataURL   |                |                  |                    |                   |
   |---------------->| POST /voice/transcribe {data_url,mime}                |                   |
   |                 |--------------->| dispatch->voice_transcribe            |                   |
   |                 |                |----------------->| validate size/mime |                   |
   |                 |                |                  |------------------->| write temp file    |
   |                 |                |                  |                    | transcribe_recording|
   |                 |                |                  |                    |------------------->|
   |                 |                |                  |                    | {transcript} <-----|
   |                 |                |                  |                    | unlink temp        |
   |                 |                |<-----------------| {transcript,prov}  |                   |
   |                 |<---------------| {data:{transcript}}                   |                   |
   | put transcript into composer; user reviews & sends (normal chat path)   |                   |
```
