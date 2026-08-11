# E04 — Approaches

Options considered, trade-offs, chosen answers. Style follows
`plans/011_provider_connections/approaches.md`. Cross-links:
[README](README.md) · [findings](findings.md) ·
[architecture](architecture.md) · [implementation](implementation.md) ·
[validation](validation.md).

Five independent decisions; the chosen option is marked.

---

## Decision A — Central proxy vs short-lived key issuance

Plan 006 verified Hermes has native `/api/audio/*` driven by locally
configured provider keys ([findings](findings.md) §1). Enterprise mode could
either proxy everything centrally or hand devices temporary credentials and
let Hermes' native path do the work.

### A1. Full central proxy — device never sees a key  ✅ CHOSEN

All speak/transcribe/voices traffic flows device → gateway → provider
([architecture](architecture.md) §1–2). Keys live only in the gateway vault.

### A2. Short-lived provider key issuance to devices

The gateway mints/leases scoped provider keys (or provider-native ephemeral
tokens where supported) and hands them to entitled devices; the device then
calls Hermes' native `/api/audio/*` locally (which 006 verified works), and
the device self-reports usage.

Honest trade-off table:

| Dimension | A1 central proxy | A2 key issuance |
| --- | --- | --- |
| Latency | +1 WAN hop per request (device→gateway→provider). Acceptable for click-to-play TTS / stop-then-transcribe STT, whose wall time is dominated by provider processing; would matter for realtime streaming, which is a non-goal | Lower: device→provider direct |
| Key exposure | Keys never leave the org boundary; one vault row; instant rotation | Keys (even short-lived) land in device memory/env; any compromised device holds a working credential for its TTL; not all providers support scoped/ephemeral keys (*verify*), so some would receive the real org key |
| Metering | Gateway measures server-side: billing-grade, tamper-proof, one code path | Client-reported (or provider-dashboard reconciliation): trusts the fleet, needs an outbox/dedup pipeline like E02's, and per-provider usage APIs (*verify*) |
| Implementation | One Go service; OSS side is a thin forwarder | Key-lease protocol + TTL/refresh + revocation + Hermes-side key injection + client metering — strictly more moving parts across both repos |
| Offline behavior | Voice needs connectivity anyway (cloud providers); no difference | Same — provider calls need the network regardless |
| Reuse of 006 backend design | Drops Hermes tools entirely | Reuses Hermes tools, but re-opens 006's per-agent-config upstream-kwarg problem (006 findings §4) |

**Decision: A1.** The latency cost is real but acceptable for the shipped
interaction patterns; A2's key-on-device exposure defeats the primary
rationale for making voice an enterprise capability, and client-trusted
metering is the wrong foundation for a billable meter. A2 stays documented
as a revisit-trigger: if v2 realtime streaming ever makes gateway latency
untenable, re-run Phase 0 against the then-pinned Hermes before reviving it
([implementation](implementation.md) Phase 0).

---

## Decision B — Metering shape

### B1. Separate `voice_usage` table + shared rollups  ✅ CHOSEN

One row per gateway request in a dedicated table; E02's daily rollup job
extended with three voice columns on `usage_rollups_daily`
([architecture](architecture.md) §4).

- Pros: voice rows are server-observed single events — a different lifecycle
  from E02's client-pushed, monotonically-updated session snapshots (upsert
  by session); mixing them would force fake session semantics onto
  point-in-time events. Clean unique key `(tenant_id, request_id)` gives
  retry idempotency. Dashboards keep one read path (the rollup table).
- Cons: one more table + one more aggregation query in the rollup job.

### B2. Overload E02's `usage_session_snapshots` (new snapshot kind)

Add `kind='voice'` rows with seconds in repurposed token columns.

- Pros: zero schema addition.
- Cons: column semantics lie (`input_tokens` meaning seconds?); the
  snapshot upsert-by-session model doesn't fit per-request events (would need
  synthetic session ids); pollutes E02's ingest invariants and every query
  that assumes snapshot rows are LLM sessions. Rejected.

**Decision: B1.** Note on quotas: v1 **meters only** — no voice-minute quota
enforcement. If plans later cap voice minutes, the entitlements-v1
Reserve/Commit machinery applies (429 `quota_exhausted`) with `voice_usage`
as the consumption record; the schema already supports it.

---

## Decision C — How the frontend learns capabilities

### C1. Extend the existing `limits` payload  ✅ CHOSEN

`GET /api/brain/v1/limits` (`brain4all/routes/setup.py` line 40; op at
`brain4all/handlers/api.py` line 97 — verified) gains a `capabilities` object
from the cached entitlement document when signed in, `{}` when signed out.

- Pros: the route and its frontend consumption already exist; one fetch on
  load covers plan id, limits, and capabilities; signed-out behavior is
  automatic (`{}` → no voice UI); no new contract surface.
- Cons: mildly widens the "limits" concept to "limits + capabilities" — the
  payload is additive, so existing consumers are unaffected.

### C2. New `/api/brain/v1/entitlements` passthrough route

A dedicated route returning the (safe subset of the) entitlement document.

- Pros: cleaner separation; room for the full document later (numeric
  limits, expiry, revision/ETag) which E-program features after E04 may want.
- Cons: a second fetch and a second cache to keep coherent with limits; new
  route + handler + client module for what is today a single boolean;
  entitlement documents contain fields (plan internals, expiry, revision)
  the UI has no use for yet and which would need filtering.

**Decision: C1 now; C2 is the natural evolution** when a later plan needs
the full document client-side — at that point `capabilities` in the limits
payload stays as a compatibility mirror. The frontend reads
`capabilities.voice` only through one hook so a future migration touches one
file ([architecture](architecture.md) §8).

---

## Decision D — Audio transport

Plan 006 chose base64-JSON both ways (its Decision C1) to mirror Hermes'
native endpoints. That rationale — minimal compatibility surface with
in-process Hermes tools — is gone: the counterparty is now our own gateway.

### D1. Base64 data URLs in JSON both directions (006's choice)

- Pros: identical to 006's design; simple envelope handling.
- Cons: ~33% size overhead in both directions across a WAN hop (uploads up
  to 25 MiB become ~33 MiB of JSON); full-body buffering and JSON parsing of
  megabyte strings at gateway and proxy.

### D2. Multipart upload for transcribe; raw `audio/mpeg` response for speak  ✅ CHOSEN

- Transcribe: browser sends the recorded blob as `multipart/form-data`
  (field `audio`, plus `mime_type`); the OSS proxy streams it through;
  `src/api/client.ts` already has `requestMultipart` (verified,
  findings §7). Response is small JSON (transcript) in the normal envelope.
- Speak: request is small JSON (`{text, voice_id?}`); response is raw
  `audio/mpeg` bytes via a `special` raw-response route
  (`response_model=None`) — the established pattern used by
  `workspace_upload`/`sandbox_*` per 006's findings §2. Browser:
  `blob → URL.createObjectURL → new Audio(url).play()`.
- Pros: no base64 tax on the two large payloads; streamable through the
  proxy without full buffering; matches provider APIs (which speak bytes,
  not data URLs).
- Cons: the speak route bypasses the JSON envelope (one raw route — a
  pattern the codebase already has); slightly more client code than
  `new Audio(dataUrl)`.

**Decision: D2.** MP3 (`audio/mpeg`) as the default TTS output for universal
browser decode (findings Q2, *verify*); upload mime passthrough
(`audio/webm`/`audio/mp4` per browser, 006 risk R7) with the STT provider's
accepted-format list checked in Phase 0 (findings Q1).

Related sub-decision (findings Q4): the per-agent voice **preference**
(voice_id per agent, TTS/STT enabled toggles) is stored OSS-side in the
agent profile `config.yaml` reusing 006's `tts:`/`stt:` shape minus
provider/key fields — agents are device-local objects, and the gateway is
stateless about voice choice (the voice_id rides on each speak request).
No secrets are ever stored there, so key-custody is unaffected.

---

## Decision E — Self-hosted users who configure Hermes voice manually

Hermes natively supports TTS/STT with locally configured providers/keys
(006 findings §1) — including fully local engines (`neutts`, `piper`, local
Whisper). A user can put `tts:`/`stt:` in their raw Hermes `config.yaml`,
set e.g. `ELEVENLABS_API_KEY` in their own `.env`, and use Hermes' native
`/api/audio/*` endpoints today.

### E1. Block/hide the native Hermes audio routes in OSS builds

Rejected outright. It violates AGENTS.md ("Preserve the original Hermes core
and native FastAPI routes") and `docs/plans.md` ("self-hosting never limits
local Hermes features"), and it would be DRM theater — the code is open.

### E2. Documented as unsupported-but-possible; no blocking  ✅ CHOSEN

Brain4All neither removes nor surfaces the capability in OSS: no Brain4All
voice routes/UI without the enterprise capability, but Hermes' native
behavior is untouched. Documentation (the E04 section added to `docs/`)
states plainly: *self-hosted users may configure Hermes voice providers
directly with their own keys; this path is not integrated with the Brain4All
UI, not metered, and not supported — and that's fine.* The enterprise value
proposition is key custody, integration, and metering — not artificial
scarcity.

**Decision: E2.** One guard follows from it: the OSS voice UI and the
enterprise proxy must not collide with a manually configured Hermes voice
setup (they don't — different routes, different config keys, no shared
state).

---

## Chosen approach (summary)

1. **A1** — full central proxy; keys never leave the gateway; server-side
   metering. A2 revisit only if v2 streaming demands it.
2. **B1** — dedicated `voice_usage` table with per-request idempotency,
   folded into E02's `usage_rollups_daily`; metering-only in v1.
3. **C1** — capability flags ride the existing `/api/brain/v1/limits` payload;
   one frontend hook isolates the source.
4. **D2** — multipart uploads, raw `audio/mpeg` speak responses; per-agent
   voice preference stored OSS-side, secret-free.
5. **E2** — raw-Hermes self-configuration stays possible and is documented
   honestly as unsupported; nothing is blocked.
