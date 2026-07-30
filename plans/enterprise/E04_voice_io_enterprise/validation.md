# E04 — Validation

How to prove the capability works, is gated, is metered, and leaks nothing.
Never report success without reading the actual output (AGENTS.md).
Cross-links: [README](README.md) · [findings](findings.md) ·
[architecture](architecture.md) · [approaches](approaches.md) ·
[implementation](implementation.md).

## 1. Entitled happy path (e2e)

- [ ] **TTS:** on a claimed device whose account has the `voice` capability,
      click Play on an assistant reply; audio plays audibly. Evidence:
      manual note + the OSS integration test asserting
      `POST /api/brain/v1/voice/speak` returns `Content-Type:
      audio/mpeg` with non-empty bytes, and the gateway test asserting the
      full middleware chain ran.
- [ ] **STT:** record a voice note via the mic; the transcript appears in
      the composer; sending it produces a normal chat message the agent
      answers. Evidence: manual note + gateway/OSS transcribe tests.
- [ ] **Voices:** the settings voice picker lists voices with
      `{voice_id, name, label}` only. Evidence: response capture showing no
      other fields, no key material.

## 2. Capability gating

- [ ] Account **without** `voice`: no Play button, no mic, no voice
      settings section render at all (component test with
      `capabilities: {}` + manual check). Direct API calls to all three OSS
      voice routes return **403** with stable code `capability_unavailable`
      (entitlements-v1). Evidence: test output + curl transcript.
- [ ] Direct gateway calls without the capability (bypassing the OSS proxy)
      also return 403 `capability_unavailable` — the gateway check is
      authoritative. Evidence: Go handler test.
- [ ] Signed-out / no `ENTERPRISE_API_URL`: `GET /api/brain/v1/limits` returns
      `"capabilities": {}`; OSS behaves exactly as before this plan (route
      snapshot diff shows only gated additions). Evidence: test comparing
      limits payloads in both states.

## 3. Keys never on the device

- [ ] **DATA_DIR sweep:** with a staging tenant whose vault holds a known
      dummy-prefixed provider key, run the full happy path, then
      `grep -r` `DATA_DIR` (profiles, config.yaml files, outbox, caches)
      for the key value and for key names (`ELEVENLABS_API_KEY`,
      `xi-api-key`, the STT provider's header/env names): **zero hits**.
- [ ] **Runtime environment sweep:** dump the OSS runtime container/process
      environment (`/proc/<pid>/environ`, container inspect): no provider
      key names or values present.
- [ ] **Wire check:** OSS-side responses and logs from all voice routes
      contain no key material (automated: run tests with the dummy key set
      gateway-side; assert it never appears in any OSS response body, error,
      or captured log line — reuses 006's key-leak test design).
- [ ] Per-agent voice config PUT given a payload containing `*_api_key` /
      `token` / secret-like fields persists none of them (re-read the
      profile `config.yaml`). Evidence: OSS test.

## 4. Metering accuracy

- [ ] **Fake-provider test (gate):** provider declares N seconds
      synthesized / M seconds transcribed → exactly one `voice_usage` row
      each with N/M ± rounding (NUMERIC(10,3)); a retried request with the
      same `request_id` still yields one row. Evidence: Go test output.
- [ ] **Real-provider spot check:** one staging speak + one transcribe;
      `voice_usage.seconds_*` matches the provider-reported duration (from
      the provider response/dashboard) within rounding. Evidence: SQL
      SELECT output + provider response capture.
- [ ] **Rollup + dashboard:** after the rollup job runs, the day's
      `usage_rollups_daily.voice_seconds_*` equals the SUM over
      `voice_usage` for that tenant/user/device/day, and the central usage
      dashboard displays the voice minutes. Evidence: SQL comparison +
      dashboard screenshot.
- [ ] Failed provider call writes a `status='provider_error'` row with zero
      seconds (observability without billing). Evidence: Go test.
- [ ] A failed metering INSERT fails the request — no audio served
      un-metered. Evidence: Go test with a poisoned DB connection.

## 5. No content at rest server-side (retention policy)

- [ ] **Schema proof:** no column in the voice migration stores audio,
      text, or transcripts (review of `migrations/NNNN_voice.sql` against
      [architecture](architecture.md) §4).
- [ ] **Redaction test (gate):** run speak/transcribe with sentinel strings
      and a sentinel audio pattern through the gateway with logging at max
      verbosity; grep all gateway log output for the sentinels: zero hits —
      metadata only (request_id, ids, sizes, durations, status).
- [ ] **Storage scan:** after a test run, scan the gateway host/container
      writable filesystem and temp dirs for audio files or files containing
      the sentinel text: zero hits (any spill buffer was unlinked).
      Evidence: `find`/`grep` transcript.
- [ ] Provider no-retention flags set where offered (Phase 0 *verify*
      result recorded; adapter code review).

## 6. Degradation (enterprise down)

- [ ] Stop/block the enterprise API. Voice controls show disabled state +
      the clear banner; clicking gives the localized unavailable message,
      never a stack trace. Evidence: manual note + component test for the
      `enterprise_unreachable` (503) mapping.
- [ ] While it is down: chat, agents, kanban, cron, providers, analytics —
      a smoke pass of non-voice features — all work unchanged (AGENTS.md:
      an outage must not restrict local features). Evidence: `make
      smoke-api` output run with `ENTERPRISE_API_URL` pointing at a dead
      endpoint.
- [ ] Restore the API; voice recovers without a runtime restart. Evidence:
      manual note.

## 7. Failure-mode contract

- [ ] Oversize upload (> 25 MiB) → **413** at the OSS edge (never
      forwarded) and 413 from the gateway when called directly. Evidence:
      tests both sides.
- [ ] Over-long speak text → 400 at the gateway; OSS model rejects at
      5,000 chars first. Evidence: tests.
- [ ] Provider outage (fake returns 500) → gateway **502** with a
      provider-agnostic body — no provider name/error text passthrough.
      Evidence: Go test asserting the body.

## 8. Repo checks

- [ ] This repo: `make check` green (lint + backend tests + frontend);
      focused `python -m pytest brain4all/tests/test_enterprise_voice.py`;
      `npm run build`.
- [ ] `brain4all-enterprise`: `go test ./internal/voice/...` green; `go vet`
      / repo lint green; migration applies cleanly on a copy of a
      production-shaped schema.
- [ ] Diff review: OSS changes limited to the
      [implementation](implementation.md) file-change summary; no Hermes
      fork, no new process, single route-assembly point, no database in the
      OSS repo.

## 9. Acceptance (mirrors README definition of done)

- [ ] Entitled user hears a reply and dictates a message; keys never on the
      device (§1 + §3 evidence).
- [ ] Unentitled user sees no voice UI; API returns 403
      `capability_unavailable` (§2).
- [ ] Voice minutes visible in the central usage dashboard and correct
      (§4).
- [ ] Enterprise-down degradation graceful; non-voice features unaffected
      (§6).
- [ ] No audio/text at rest server-side; logs metadata-only (§5).
- [ ] `plans/LOCAL_FEATURES_CHECKLIST.md` 006 row marked relocated to E04;
      enterprise program README checklist E04 box checkable.
