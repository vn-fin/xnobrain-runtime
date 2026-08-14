# E08 Implementation — First AI service: Speech-to-Text on Groq Whisper v3

Concrete build plan for `ai-stt` plus the Go gateway slice that fronts it.
Frame: [README.md](README.md). Supersedes the provider-calling half of E04 §3
(the Go `internal/voice` module keeps auth/entitlement/metering only).

## Phase 0 — Probe and pin the Groq audio API (½ day)

Per repo discipline, live-probe before coding and pin observations in
`findings.md`. Expected shape (verify all of it against the live API + docs
at console.groq.com — do not trust this table until probed):

| Aspect | Expected (to verify) |
|---|---|
| Endpoint | `POST https://api.groq.com/openai/v1/audio/transcriptions` (OpenAI-compatible) |
| Auth | `Authorization: Bearer $GROQ_API_KEY` |
| Models | `whisper-large-v3` (higher accuracy, + translations endpoint), `whisper-large-v3-turbo` (faster/cheaper, transcription-only) |
| Pricing | ~$0.111 / audio-hour (v3), ~$0.04 / audio-hour (turbo) — pin actual |
| Input | multipart `file`: flac, mp3, mp4, mpeg, mpga, m4a, ogg, wav, webm |
| Size cap | ~25 MB (free tier) / higher on paid — pin actual per our tier |
| Params | `language` (ISO 639-1 — verify `vi` quality), `prompt` (spelling hints), `temperature`, `response_format` (`json`\|`verbose_json`\|`text`), `timestamp_granularities` (`word`/`segment`, needs `verbose_json`) |
| Rate limits | per-model RPM + audio-seconds/hour — pin actual headers |
| Errors | 400 (format/size), 401, 413, 429 (retry-after), 5xx |

**Probe checklist:** short WAV EN → text; short **Vietnamese** clip → verify quality
(`language=vi` vs autodetect — XNO's market is Vietnamese-first); `verbose_json` with
segment timestamps (pin shape — the UI may want them); oversized file → pin the
413/400 behavior; rate-limit headers. Write `findings.md` with pinned request/response
excerpts (redact the key).

## Phase 1 — `ai-stt` service (Python/FastAPI) (1–2 days)

**Repo layout** (`xnobrain-enterprise`):

```
services/ai/stt/
├── pyproject.toml            # fastapi, uvicorn, httpx, python-multipart
├── Dockerfile                # slim, non-root, no shell extras
├── app/
│   ├── main.py               # FastAPI app, routes, internal-token dependency
│   ├── groq_client.py        # httpx wrapper: transcribe(), retries, timeout
│   ├── config.py             # env: GROQ_API_KEY, INTERNAL_TOKEN, caps, model allowlist
│   ├── audio.py              # sniff container/duration (mutagen or ffprobe), size checks
│   └── errors.py             # provider error → stable error codes
└── tests/
    ├── test_transcribe.py    # mocked Groq (httpx MockTransport)
    └── test_probe_live.py    # skipped unless GROQ_API_KEY + RUN_LIVE=1
```

**API (internal network only, service token required):**

```
POST /internal/v1/transcribe            multipart: file; fields: language?, model?,
                                        prompt?, timestamps? (bool)
200 → { "text": "...",
        "language": "vi",
        "segments": [...]?,             # only when timestamps requested
        "metering": { "duration_seconds": 92.4, "input_bytes": 1443210,
                       "model": "whisper-large-v3", "provider": "groq" } }
4xx → { "error": "audio_too_large" | "unsupported_format" | "bad_request", ... }
502 → { "error": "provider_error", "retryable": true|false }
503 → { "error": "provider_rate_limited", "retry_after_seconds": n }

GET  /internal/v1/models  → allowlisted models + caps      GET /healthz → 200
```

**Behavior rules:**
- Stateless pass-through: audio streamed to Groq, never written to disk (spooled to
  tmpfs only if the SDK requires a file handle), discarded on response; **no request
  bodies or transcripts in logs** — log only sizes, durations, model, latency, status.
- `duration_seconds` computed locally (header parse / ffprobe) — metering must not
  depend on the provider's mood.
- Caps enforced *before* calling Groq: max bytes (pinned Phase 0), max duration
  (config, e.g. 15 min v1 — longer audio is rejected with a clear error; chunking is a
  later phase).
- Retries: 1 retry on 5xx with jitter; 429 honored via `Retry-After`, surfaced as 503.
- Model allowlist: `whisper-large-v3` (default), `whisper-large-v3-turbo` (opt-in
  cheap mode). Anything else → 400.
- No auth logic beyond the constant-time internal-token check.

## Phase 2 — Gateway slice (Go, `internal/voice`) (1–2 days)

The E04 module, thinned to platform concerns:

```
POST /voice/v1/transcribe    (multipart, member session token)
  1. authn (E05 session) → member, org
  2. entitlement check: capability voice.stt          ← Pro/Enterprise only
     (plans.md: Free=0/403; personal Pro metered to user; org metered to org)
  3. quota reserve (voice minutes) — entitlements-v1 Reserve
  4. proxy multipart to ai-stt /internal/v1/transcribe (svc token, streaming body)
  5. on 200: commit reservation; INSERT usage row (E02 store):
     (tenant, member, capability='voice.stt', model, duration_seconds, cost_usd, ts)
     — server-observed, idempotent by request id; no un-metered audio leaves
  6. return transcript body unchanged; on ai-stt failure: release reservation,
     map to 503 capability_unavailable
```

Also: `GET /voice/v1/models` (proxied), per-org rate limit at the gateway, audit event
`voice.transcribe` (metadata only — duration, model; never content), admin usage
rollup gains a `voice.stt` capability filter (feeds the E04 admin Voice page and the
`mockups/admin/voice.html` design).

## Phase 3 — OSS wiring (thin, dormant) (1 day)

Per E04's OSS slice — unchanged in spirit:
- `xnobrain/integrations/enterprise_voice.py`: forward multipart to
  `ENTERPRISE_API_URL /voice/v1/transcribe` with the member session; map errors.
- Route `/api/brain/v1/voice/transcribe` registered only when `ENTERPRISE_API_URL`
  is set — absent env ⇒ 404, zero behavior change for OSS users.
- UI: the composer mic button (already designed in E04 + mockups) records →
  posts → inserts transcript into the input. Hidden without the `voice` capability
  flag in the limits payload.

## Phase 4 — Ship & operate

- Compose: `ai-stt` container on the internal network, `--profile voice`;
  env `GROQ_API_KEY`, `INTERNAL_TOKEN`, caps. Gateway env: `AI_STT_URL`.
- Dashboards: voice minutes by member/org/day from the usage store (E02 query API).
- Runbook: Groq outage ⇒ capability 503s, alert on error-rate; nothing else degrades.

## Test plan

| Layer | Tests |
|---|---|
| ai-stt unit | mocked Groq: happy path; language passthrough; timestamps shape; size/duration caps → 4xx; 5xx retry then 502; 429 → 503 + retry_after; token check; **no-content-in-logs assertion** |
| ai-stt live probe | `RUN_LIVE=1`: EN clip; **VI clip**; verbose_json; oversized reject (mirrors Phase 0) |
| Gateway | entitlement 403 (Free), reserve/commit row written exactly once (idempotent replay), release on failure, rate limit, audit event emitted |
| OSS | route absent without env; present+proxied with env; UI hidden without capability |
| E2E | mic → transcript in composer on a Pro account; minutes appear in admin usage |

## Definition of done

A Pro (or Enterprise-member) user clicks the mic, speaks Vietnamese or English, and
gets an accurate transcript in the composer; a Free user gets a clean upgrade prompt
(403 path); the admin usage dashboard shows the member's voice minutes and cost for
the period; the Groq key exists **only** in the `ai-stt` container; no audio or
transcript is persisted or logged anywhere server-side; killing `ai-stt` degrades
only the mic button.

## Estimate

~4–6 working days total (Phase 0: 0.5 · ai-stt: 1–2 · gateway: 1–2 · OSS+E2E: 1),
assuming E01's skeleton (authn + entitlements stub) exists; if not, front with the
E01 static-token stopgap and swap when E05 lands.
