# 006 — Validation

How to prove the feature works and is safe. Cross-links:
[README](README.md) · [findings](findings.md) · [architecture](architecture.md)
· [approaches](approaches.md) · [implementation](implementation.md).

Run focused tests while iterating; run `make check` before handoff. Never
report success without reading the actual output (AGENTS.md).

## 1. Compatibility test (Phase 0 gate)

File: `xnobrain/tests/test_voice_compat.py` (skip-guarded on
`HERMES_AVAILABLE`, like `xnobrain/tests/test_kanban.py`).

- [ ] Imports succeed: `tools.tts_tool.text_to_speech_tool`,
      `tools.voice_mode.transcribe_recording`,
      `tools.transcription_tools.transcribe_audio` + `is_stt_enabled`,
      `agent.tts_registry.list_providers`,
      `agent.transcription_registry.list_providers`.
- [ ] `inspect.signature(text_to_speech_tool)` is checked; the B2/B1 selection
      constant reflects whether `tts_config` is accepted.
- [ ] The 25 MiB cap constant matches (`_MAX_TRANSCRIPTION_UPLOAD_BYTES`) or the
      service's mirrored value equals it.
- [ ] Round trip in a temp `HERMES_HOME`: synth a short string returns a
      `success/file_path`-shaped result; transcribe of a small generated WAV
      returns a `transcript` key. External model/network calls mocked; the
      tools themselves are NOT mocked.

Command: `python -m pytest xnobrain/tests/test_voice_compat.py -q`

## 2. Unit tests (service rules, no Hermes required)

File: `xnobrain/tests/test_voice_api.py` (handler/service level) — mock the
integration adapter:

- [ ] `speak` rejects empty and over-long text (400).
- [ ] `transcribe` rejects: non-`data:` URL, missing `;base64`, non-`audio/*`
      mime, empty audio, and `> 25 MiB` decoded (`audio_too_large` 413).
- [ ] `get_agent_voice` returns defaults for an agent with no `tts:`/`stt:`
      config (`tts.provider=edge`, `stt.provider=local`, `enabled=true`).
- [ ] `set_agent_voice` **rejects** any secret/key field and only persists
      `enabled/provider/voice_id/model_id`.
- [ ] `VoiceUnavailable` maps to `voice_unavailable` 503; provider-not-configured
      maps to a clean 4xx with no stack trace.
- [ ] Envelope shape: success responses are
      `{success, data, message, status_code}` (matches `APIHandlers.success`).

## 3. Integration tests (real pinned Hermes, temp `HERMES_HOME`)

Same harness as `test_kanban.py` (`XNOBrainApplication`, `httpx.AsyncClient`,
`ASGITransport`), skip-guarded on the runtime:

- [ ] `POST /api/brain/v1/voice/speak?agent=<id>` with `{text}` returns
      `data.data_url` starting `data:audio/`, plus `mime_type`, `provider`; the
      temp synth file is gone afterward (no leak).
- [ ] `POST /api/brain/v1/voice/transcribe?agent=<id>` with a small base64
      WAV returns a `data.transcript` string; temp file cleaned up.
- [ ] `GET /api/brain/v1/voice/providers` returns `{tts:[...], stt:[...]}`
      with builtins present.
- [ ] `GET /api/brain/v1/voice/voices` returns `{available:false,voices:[]}`
      with no `ELEVENLABS_API_KEY` set (and never a key when one is set).
- [ ] `GET` then `PUT` `/api/brain/v1/agents/{id}/voice` round-trips config;
      the written `profiles/<id>/config.yaml` gains `tts:`/`stt:` keys and a
      snapshot exists (persistence-before-success).
- [ ] Under B2: two agents with different providers yield different `provider`
      in their speak responses.
- [ ] Config change made via the API is visible to the native Hermes tools
      (read `load_config()` under that profile), i.e. no divergent store.

## 4. Frontend tests + build

- [ ] `src/api/voice.test.ts`: request shaping (query `?agent=`, JSON bodies),
      envelope unwrap, error mapping.
- [ ] Component test for the mic/play controls: play button calls `speak` and
      constructs an `Audio`; recorder wires `MediaRecorder` and puts the
      transcript into the composer (mock `getUserMedia`/`MediaRecorder`).
- [ ] `npm test` passes for the new tests.
- [ ] `npm run build` passes (type + build).

## 5. Repo checks

- [ ] `make check` passes (lint + backend tests + frontend).
- [ ] `make test` passes for the backend voice tests.
- [ ] `make smoke-api` passes and includes voice endpoints reachable (extend the
      smoke set if it enumerates routes; no synthetic/mock records persisted).

## 6. Manual verification (real stack, `make run`)

TTS (hear a reply):
1. Open an agent chat, send a message, wait for the assistant reply.
2. Click the Play button on the reply.
3. Expect: audio plays and is audible; button toggles to Stop; no page error.
4. Set the agent's TTS provider to a second provider in the Voice panel; play
   again; expect the `provider` field / audible voice differs (B2).

STT (speak a note):
1. Click the mic button in the composer; grant mic permission; speak; stop.
2. Expect: a spinner, then the transcript appears in the composer textarea.
3. Review and send; the transcript becomes a normal chat message and the agent
   replies to it.
4. Deny mic permission once; expect a clean, localized error, not a crash.

Config:
1. Voice settings panel loads current per-agent config; change provider/voice
   id; save; reload; the change persists (config.yaml + snapshot).

## 7. Security / credential-leak check (must pass)

- [ ] Automated: a test asserts no response body from any voice endpoint
      contains `ELEVENLABS_API_KEY`, an `xi-api-key` value, or any `.env` secret
      — set a dummy key in the environment and assert it never appears in
      `voice/voices`, `voice/providers`, `agent/{id}/voice`, or error bodies.
- [ ] `set_agent_voice` given a payload containing a `*_api_key`/`token`/secret
      field persists none of it (re-read `config.yaml` and assert absence).
- [ ] Logs: grep the test run output for the dummy key value; assert zero hits
      (voice code must never log config bodies, audio bytes, or provider keys).
- [ ] Error text from a provider failure is sanitized (no key, no absolute
      temp path, no stack detail beyond a stable code/message).

## 8. Acceptance checklist (with evidence)

- [ ] Hermes pinned; `test_voice_compat.py` passes. Evidence: pytest output +
      the recorded commit in `docs/development.md`.
- [ ] `voice_speak` returns audible audio. Evidence: integration test asserting
      `data:audio/` + manual playback note.
- [ ] `voice_transcribe` returns a transcript. Evidence: integration test +
      manual "spoke a note, saw the transcript" note.
- [ ] `voice_providers` / `voice_voices` return safe, non-secret data. Evidence:
      integration test assertions.
- [ ] `agent/{id}/voice` GET/PUT persists atomically with a snapshot and no
      secrets. Evidence: test reading `config.yaml` + snapshot dir.
- [ ] Per-agent voice honored (B2) or documented global fallback (B1). Evidence:
      the two-agent provider test, or the B1 note in the panel + test.
- [ ] No key leaks in responses, logs, or errors. Evidence: section 7 tests.
- [ ] `make check` + focused tests + `npm run build` pass. Evidence:
      command output.
- [ ] Temp audio files are always cleaned up. Evidence: post-call file-absence
      assertions.
- [ ] No new process, no database, single route-assembly point, no Hermes fork.
      Evidence: diff review (only the files in
      [implementation.md](implementation.md) file-change summary, plus the
      additive upstream kwarg under B2).
