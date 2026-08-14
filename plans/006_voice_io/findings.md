# 006 — Findings

Ground truth from reading the pinned Hermes source under
`.tools/hermes-agent/` (read-only, we EXTEND it) and the XNOBrain backend.
Every path and shape below was read directly, not inferred. Cross-links:
[README](README.md) · [architecture](architecture.md) · [approaches](approaches.md)
· [implementation](implementation.md) · [validation](validation.md).

## 1. What Hermes provides (verified)

### 1.1 HTTP endpoints — `.tools/hermes-agent/hermes_cli/web_server.py`

These live in the same FastAPI app XNOBrain runs inside (`:8642`). They are
**Hermes-owned native routes**; XNOBrain must not treat them as its product
contract, but we can call the functions they call, in-process.

- **`POST /api/audio/transcribe`** (def `transcribe_audio_upload`, ~line 4269)
  - Request model `AudioTranscriptionRequest` (~line 1282):
    `{ "data_url": str, "mime_type": Optional[str] }`.
  - `data_url` must be `data:<audio-mime>;base64,<...>`. Rejects non-`data:`,
    non-`;base64`, non-`audio/*` (allows `video/webm`), empty, or `> 25 MiB`
    (`_MAX_TRANSCRIPTION_UPLOAD_BYTES = 25 * 1024 * 1024`, ~line 1322).
  - Writes bytes to a temp file, then calls
    **`tools.voice_mode.transcribe_recording(temp_path)`** in an executor.
    `transcribe_recording` (not raw `transcribe_audio`) filters Whisper
    hallucinations and treats an empty transcript as success (silence).
  - Response: `{ "ok": true, "transcript": str, "provider": <str|null> }`.
    On provider failure: HTTP 400/500 with `detail`. Temp file is always
    unlinked in `finally`.

- **`POST /api/audio/speak`** (def `speak_text`, ~line 4448)
  - Request model `TTSSpeakRequest` (~line 4347): `{ "text": str }`.
  - Calls **`tools.tts_tool.text_to_speech_tool(text)`** in an executor. That
    tool reads `tts:` from the **root** `config.yaml` via
    `hermes_cli.config.load_config()` (see section 4 — this is the per-agent gap).
  - Reads the produced file, base64-encodes it, **unlinks the file**, returns:
    `{ "ok": true, "data_url": "data:<mime>;base64,<...>", "mime_type": str,
       "provider": <str|null> }`. mime derived from extension
    (`.mp3->audio/mpeg`, `.ogg/.opus->audio/ogg`, `.wav`, `.flac`).
  - The on-disk path is never returned (deliberate — desktop privacy).

- **`GET /api/audio/elevenlabs/voices`** (def `get_elevenlabs_voices`, ~4380)
  - Reads `ELEVENLABS_API_KEY` from `load_env()` / `os.environ`. If absent:
    `{ "available": false, "voices": [] }`.
  - Fetches `https://api.elevenlabs.io/v1/voices` with the key in an
    `xi-api-key` header. **The key stays server-side; only non-secret voice
    metadata is returned.**
  - Response: `{ "available": true, "voices": [ { "voice_id", "name",
    "label" } ] }`. 401/403 -> `{ "available": false, "voices": [],
    "error": "unauthorized" }` (200, logged once). Other errors -> 502.

- **`WebSocket /api/audio/speak-stream`** (def `speak_stream_ws`, ~4533)
  - Streaming TTS: client sends `{"text": ...}` deltas and `{"done": true}`;
    server sends `{"type":"start","sample_rate":N,"channels":1}`, then binary
    int16 PCM frames, then `{"type":"end"}`. Sends `{"type":"fallback"}` when
    the configured provider has no chunked API (client should use the POST
    endpoint instead). Auth-gated via `_ws_auth_ok` / `_ws_request_is_allowed`.
  - Relevant only to the optional Phase 3.

### 1.2 Tools (the functions the endpoints call)

- **`tools/tts_tool.py`**
  - `text_to_speech_tool(text: str, output_path: Optional[str] = None) -> str`
    (~line 2287). Returns a JSON string `{success, file_path, provider, ...}`.
    Reads provider/voice from `_load_tts_config()` -> `load_config()["tts"]`.
    **No config-override parameter** on the public entry point (important —
    see section 4).
  - `_load_tts_config()`, `_get_provider(cfg)` (default `edge` unless user opts
    into a cloud provider), `BUILTIN_TTS_PROVIDERS`, `_resolve_max_text_length`.
- **`tools/transcription_tools.py`**
  - `transcribe_audio(file_path: str, model: Optional[str] = None) -> Dict`
    (~line 1717). `_load_stt_config()`, `is_stt_enabled(cfg)`, `_get_provider`,
    `BUILTIN_STT_PROVIDERS`.
- **`tools/voice_mode.py`**
  - `transcribe_recording(wav_path: str, model=None) -> Dict` (~line 878) —
    hallucination-filtered wrapper used by the HTTP transcribe endpoint.
- **`tools/neutts_synth.py`** — standalone local NeuTTS synth run as a
  subprocess by `tts_tool.py` so the ~500 MB model process exits after use.
  Requires `neutts[all]` and `espeak-ng`. This is the local/offline TTS path.

### 1.3 Registries (provider discovery) — `.tools/hermes-agent/agent/`

- **`agent/tts_registry.py`** -> `list_providers() -> List[TTSProvider]`,
  `get_provider(name)`, `register_provider(...)`. Built-in names (reserved,
  always win): `edge, elevenlabs, openai, minimax, xai, mistral, gemini,
  neutts, kittentts, piper, deepinfra`.
- **`agent/transcription_registry.py`** -> same API for STT. Built-in names:
  `local, local_command, groq, openai, mistral, xai, elevenlabs, deepinfra`.
- These registries only hold **plugin-registered** providers (populated at
  import via `PluginContext.register_*_provider`); they may legitimately be
  empty in a process that did not run `discover_plugins()`.

### 1.4 Provider option lists (for a settings dropdown)

There is **no dedicated `/api/audio/providers` endpoint.** The canonical option
lists live in `CONFIG_SCHEMA` in `web_server.py`:

- `tts.provider.options`: `["edge","elevenlabs","openai","xai","minimax",
  "mistral","gemini","neutts","kittentts","piper"]` (~line 821).
- `stt.provider.options`: `["local","groq","openai","xai","elevenlabs"]`
  (~line 831). (`mistral` temporarily removed upstream — quarantined package.)
- `stt.elevenlabs.model_id.options`: `["scribe_v2","scribe_v1"]`.
- Dynamic merge helper `_custom_provider_options(kind, builtins, cfg)` (~1045)
  and `_schema_with_dynamic_provider_options()` (~1160) add command-type and
  plugin providers at request time. XNOBrain's provider list should mirror
  this: builtins first, then registry `list_providers()` names, deduped.

### 1.5 Local vs cloud

- **Local / offline / free default:** `edge` (Edge TTS, network but free, the
  historical default), `neutts`, `kittentts`, `piper` (fully local models);
  STT `local` (local Whisper). `_get_provider` defaults TTS to `edge` unless
  the user explicitly opts into a paid provider — "inference credentials do not
  imply consent to paid speech generation."
- **Cloud / paid:** `elevenlabs, openai, xai, minimax, mistral, gemini` (TTS);
  `groq, openai, xai, elevenlabs` (STT). These need provider keys in the
  environment / provider credentials.

## 2. What XNOBrain has today

- One FastAPI/Hermes process; `xnobrain/routes/setup.py` assembles all
  XNOBrain routes (`Route` dataclass; `special`/raw-response for streaming and
  uploads). No voice routes exist.
- `xnobrain/handlers/api.py` — `APIHandlers.dispatch` maps `route.name` ->
  an operation in a big `operations` dict, wraps results in the success
  envelope. Raw endpoints (`stream`, `workspace_upload`, `sandbox_*`,
  `kanban_stream`, bundles) are handled by dedicated methods and registered
  with `response_model=None`.
- `xnobrain/integrations/` — `hermes.py`, `config.py`, `kanban.py`,
  `nine_router.py`, `runtime.py`. `kanban.py` shows the lazy-import pattern:
  `def _module(): from hermes_cli import kanban_db ...` raising a typed
  `KanbanUnavailable` if the runtime is missing. **No `voice.py`.**
- `xnobrain/services/platform.py` — `PlatformService` holds sub-services
  (`self.kanban = KanbanService(...)`). Per-agent config is written by
  `update_agent_config(agent_id, body)`: snapshot the current `config.yaml`
  (`repository.snapshot`), then `agents.update_config(...)`. Agent profiles
  live under `DATA_DIR/profiles/<agent-id>/config.yaml`. **No voice service.**
- `xnobrain/models/api.py` — Pydantic request models (e.g. `KanbanTaskCreate`
  with `Field(...)` constraints and `Literal[...]`). Exported via
  `xnobrain/models/__init__.py` (`__all__` = all non-underscore names).
  **No voice models.**
- Frontend `src/`:
  - `src/api/client.ts` — `request<T>`, `requestRaw`, `requestMultipart`,
    `requestWithMeta`, `buildApiUrl`, `ApiError`. Envelope-aware.
  - `src/api/conversations.ts`, `src/chat/*`, `src/components/ChatArea.tsx`
    (composer `textareaRef`, `onSend(input)`, assistant message action buttons
    Copy/ThumbsUp/ThumbsDown at ~line 574 — the natural home for a Play button).
  - No `MediaRecorder` / `getUserMedia` / `new Audio(...)` usage anywhere yet.
  - i18n: `src/locales/{en,de,es,fr,ja,vi,zh}.json` (7 locales; new strings
    must be added to all).
  - **No voice API module, hook, or component.**

## 3. The exact gap

Hermes can already synthesize and transcribe in the same process, but:

1. No XNOBrain **contract**: nothing under `/api/brain/v1/voice/*` or
   `/api/brain/v1/agents/{id}/voice`.
2. No XNOBrain **adapter**: no `integrations/voice.py` calling the Hermes
   tools/registries, no service, no models.
3. No **per-agent voice**: Hermes TTS/STT read the root profile `config.yaml`
   only (see section 4). XNOBrain manages many agent profiles; each needs its
   own `tts:`/`stt:` config, exposed and edited safely.
4. No **UI**: no play button, no recorder, no Voice settings panel.

## 4. The per-agent-config subtlety (design-driving finding)

`text_to_speech_tool` / `transcribe_*` read config via
`hermes_cli.config.load_config()`, which is **keyed on `HERMES_HOME` /
`get_config_path()`** and cached. The public `text_to_speech_tool(text,
output_path=None)` has **no `tts_config` override parameter**. So:

- The native `/api/audio/speak` uses the **root** `tts:` config only.
- To make an agent speak with *its own* voice, XNOBrain cannot simply call the
  public tool and get per-agent behavior.

Options (decided in [approaches.md](approaches.md)):
- (a) Ship global voice first (root config), store per-agent config natively,
  apply it only during agent runs where the profile is already active.
- (b) Add a small, additive, backward-compatible `tts_config` / `stt_config`
  override parameter to the Hermes tool entry points **in the `hermes_cli`
  extension surface** (constraints explicitly allow "necessary upstream changes
  in the current hermes_cli package"). This is an extension, not a fork.

`approaches.md` chooses (b) for a clean per-agent story, with (a) as the Phase 1
fallback if the pin lacks the parameter.

## 5. Provider-credential handling

- `ELEVENLABS_API_KEY` and other provider keys are read by Hermes from
  `load_env()` / `os.environ` and **never returned**. XNOBrain must preserve
  this: the voices endpoint returns only `voice_id/name/label`; the providers
  endpoint returns only names/labels/availability; the per-agent voice config
  get/set operates on `tts:`/`stt:` YAML (provider name, `voice_id`,
  `model_id`) and **must strip/never accept secret keys**. Keys stay in `.env`
  / provider credentials managed elsewhere.
- Never log the config body, transcript-provider errors verbatim if they could
  echo a key, or the raw audio bytes.

## 6. Compatibility APIs to pin and test (Phase 0)

Pin the Hermes commit/release, then a compatibility test asserts these import
and behave:

- `from tools.tts_tool import text_to_speech_tool` (callable; and, if approach
  (b) is taken, that it accepts a `tts_config` kwarg).
- `from tools.transcription_tools import transcribe_audio, is_stt_enabled`.
- `from tools.voice_mode import transcribe_recording`.
- `from agent.tts_registry import list_providers as tts_list_providers`.
- `from agent.transcription_registry import list_providers as stt_list_providers`.
- The HTTP shapes of `/api/audio/speak`, `/api/audio/transcribe`,
  `/api/audio/elevenlabs/voices` (request/response keys) if we choose to
  self-call them instead of the tools (we don't — see approaches — but pin the
  shapes we depend on).
- `_MAX_TRANSCRIPTION_UPLOAD_BYTES` value (or replicate the 25 MiB cap and
  assert parity so our validation matches Hermes').

## 7. Risks

- **R1 — Per-agent config not honored** by the public tool (section 4).
  Mitigation: approach (b) upstream param, or global-only Phase 1.
- **R2 — Provider not configured / paid provider not consented.** `edge` is the
  safe default; surface a clear "voice provider not configured" error, never a
  stack trace or key.
- **R3 — Large / wrong-format audio.** Enforce the 25 MiB cap and `audio/*`
  mime at the XNOBrain boundary before touching Hermes.
- **R4 — Blocking synthesis on the event loop.** Hermes runs the tool in an
  executor; XNOBrain's adapter must do the same (`run_in_executor` /
  `asyncio.to_thread`) so TTS does not stall the single process.
- **R5 — Temp-file leakage.** The Hermes endpoints unlink their temp files;
  if we call the tools directly we must unlink the file the tool returns.
- **R6 — Key leakage** via config echo or error text (section 5). Test for it.
- **R7 — Browser `MediaRecorder` mime variance** (`audio/webm`, `audio/mp4` on
  Safari). Send the real mime with the data URL; Hermes maps common ones.

## 8. Open questions

- Q1: Does the pinned Hermes tool accept a config override, or must we add one
  (approach b)? Resolve in Phase 0 by reading the pinned source.
- Q2: Should the play button auto-play streamed replies, or be click-to-play
  only? Plan defaults to click-to-play (Phase 2); auto/stream is Phase 3.
- Q3: Where do global (root) voice defaults surface in the UI — global Settings
  vs per-agent only? Plan: per-agent panel writes the agent `config.yaml`;
  global defaults reuse the existing global config surface (out of scope to
  redesign here).
