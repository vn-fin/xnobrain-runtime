# 006 — Approaches

Options considered, trade-offs, and the chosen approach. Cross-links:
[README](README.md) · [findings](findings.md) · [architecture](architecture.md)
· [implementation](implementation.md) · [validation](validation.md).

Three independent decisions. Each has options; the chosen one is marked.

---

## Decision A — How XNOBrain reaches Hermes audio

### A1. HTTP self-call the native `/api/audio/*` routes
XNOBrain handlers call `http://127.0.0.1:8642/api/audio/speak` etc.

- Pros: zero coupling to Hermes internals; uses the exact tested endpoint.
- Cons: a process calling its own HTTP port is wasteful and fragile (auth
  headers, event-loop reentrancy); gives **no hook** for per-agent config; adds
  a network round-trip for large base64 blobs; contradicts the repo rule
  "local layers call each other directly rather than through HTTP".

### A2. In-process adapter over the Hermes tools + registries  ✅ CHOSEN
`integrations/voice.py` imports `tools.tts_tool.text_to_speech_tool`,
`tools.voice_mode.transcribe_recording`, and the two registries, exactly like
`integrations/kanban.py` imports `hermes_cli.kanban_db`.

- Pros: matches the established XNOBrain pattern; no self-HTTP; direct control
  of config, temp-file cleanup, and response shaping; testable with a temp
  `HERMES_HOME` and the real package.
- Cons: depends on tool symbols (mitigated by the Phase 0 compatibility test
  and the pin).

**Rationale:** A2 is the only option consistent with AGENTS.md ("extend from
xnobrain", "local layers call each other directly") and it is the only one
that can carry per-agent config down to synthesis.

---

## Decision B — Per-agent voice vs global voice

Hermes' public `text_to_speech_tool(text, output_path=None)` reads the **root**
`config.yaml` and takes no config override (see [findings](findings.md) section 4).

### B1. Global voice only
Standalone speak/transcribe always use the root config; per-agent config is
stored but only honored during full agent runs (where the profile is active).

- Pros: zero upstream change; ships immediately.
- Cons: the "Play this agent's reply" button ignores the agent's chosen voice —
  a visible product gap; per-agent panel writes config that the speak endpoint
  won't honor.

### B2. Additive upstream override parameter  ✅ CHOSEN (with B1 as fallback)
Add a small, backward-compatible `tts_config: dict | None = None` (and the STT
equivalent) to the Hermes tool entry points, made in the **`hermes_cli`
extension surface** (AGENTS.md 001 and the constraints explicitly allow
"necessary upstream changes in the current hermes_cli package"). When `None`,
behavior is identical to today (`_load_tts_config()`); when provided, it is used
as the effective config. `integrations/voice.py` passes the per-agent
`tts:`/`stt:` sections.

- Pros: clean, honest per-agent voice for the standalone endpoints; additive and
  fully backward-compatible; no fork (extension, not a copy).
- Cons: a real upstream change gated behind the pin and a compatibility test.

**Rationale:** The product story ("the agent speaks with its voice") requires
B2. It is small, additive, and explicitly permitted. **Fallback:** if the pin
cannot take the change in time, Phase 1 ships B1 (global voice) and the panel
notes that per-agent voice applies during runs; the compatibility test detects
whether the override kwarg exists and the service branches accordingly. Done
(README) is written to accept either, but B2 is the target.

---

## Decision C — Audio transport (base64 whole-file vs streaming)

### C1. Base64 whole-file in the JSON envelope  ✅ CHOSEN
`speak` returns `{data_url:"data:<mime>;base64,..."}`; `transcribe` accepts a
base64 data URL. Identical to the native Hermes endpoints.

- Pros: dead simple in the browser (`new Audio(data_url)`, `readAsDataURL`);
  fits the existing `APIEnvelope`; no new raw-response plumbing for the common
  path; matches Hermes so the compatibility surface is minimal.
- Cons: ~33% base64 overhead and full buffering; higher latency for long
  replies (bounded by `MAX_TTS_CHARS`).

### C2. Raw audio bytes response
`speak` returns `Response(content=bytes, media_type=...)` via a `special`
raw-response route (like `workspace_upload`/`sandbox_setup`).

- Pros: smaller payload, streamable.
- Cons: bypasses the envelope; needs a dedicated handler method and
  `response_model=None` route; more UI plumbing. Not worth it for whole-file.

### C3. WebSocket streaming TTS (`/api/audio/speak-stream`)
Bridge the Hermes PCM streaming socket for low-latency, speak-as-it-generates.

- Pros: best latency; overlaps generation and playback.
- Cons: significant client (Web Audio PCM playback) and server (WS bridge)
  work; auth and barge-in handling. **Deferred to optional Phase 3.**

**Rationale:** C1 for Phases 1–2 (simple, matches Hermes, covers the user
stories). C3 is an optional enhancement, not part of done.

---

## Chosen approach (summary)

1. **A2** — in-process `integrations/voice.py` over the Hermes tools and
   registries (never self-HTTP, never fork).
2. **B2** — per-agent voice via an additive upstream `tts_config`/`stt_config`
   override in the `hermes_cli` extension surface; **B1** (global voice) is the
   automatic fallback the compatibility test selects if the pin lacks the kwarg.
3. **C1** — base64 whole-file audio inside the standard `APIEnvelope`; **C3**
   streaming is optional Phase 3.

This keeps one process, one route-assembly point, no database, no Hermes fork,
credentials server-side, and delivers both user stories (hear a reply, speak a
note) with a per-agent voice identity. Concrete steps in
[implementation.md](implementation.md); gates in [validation.md](validation.md).
