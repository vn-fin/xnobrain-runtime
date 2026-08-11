# E08 — Enterprise service platform: App services (Go) + AI services (Python)

**Priority:** P1 — this is the packaging frame every other enterprise plan deploys into ·
**Depends on:** E01 (control-plane skeleton) · **Refines:** E04 (voice moves its
provider-calling half into a Python AI service) · **First build:** speech-to-text via
Groq Whisper v3 (see [implementation.md](implementation.md)) · **Status:** design

## Goal

Define the service architecture of `brain4all-enterprise` as two families:

- **App services — Go.** Everything billing-grade, stateful, and control-plane:
  identity, entitlements, usage, fleet, boards, billing. Raw SQL on PostgreSQL
  (no ORM, per repo rules), one place where auth and metering happen.
- **AI services — Python (FastAPI).** Everything model-shaped: speech-to-text,
  text-to-speech, skill evaluation, search. Python because every AI SDK is
  Python-first and these services are simple, stateless workers.

## Why this split

- **Go for correctness-critical accounting** — the E01/E02 contracts (entitlements,
  snapshot upserts, device commands) are already specified as Go + Postgres.
- **Python for AI velocity** — provider SDKs, audio handling, and the skill-eval
  harness (which must drive the OSS Python sandbox) are days-of-work in Python.
- **Failure isolation** — an AI provider outage degrades one optional capability;
  the control plane (auth, usage, fleet) is unaffected. Consistent with the
  program invariant: enterprise trouble never restricts local operation.

## Topology

```
                                   brain4all-enterprise deployment
member runtime / browser          ┌──────────────────────────────────────────────────────┐
  (OSS thin proxy,                │  control-plane  (ONE Go binary, modular)             │
   ENTERPRISE_API_URL)  ──TLS──►  │   gateway: authn · RBAC · entitlements · rate limit  │
                                  │   identity │ usage │ fleet │ boards │ billing        │
                                  │        │ meter (duration, tokens, cost)              │
                                  │        ▼ internal network only (svc token)           │
                                  │  ┌───────────┐ ┌───────────┐ ┌──────────────┐        │
                                  │  │ ai-stt    │ │ ai-tts    │ │ ai-skill-eval│  ...   │
                                  │  │ (Python)  │ │ (Python)  │ │ (Python)     │        │
                                  │  └─────┬─────┘ └─────┬─────┘ └──────┬───────┘        │
                                  │        │ provider keys live here only                │
                                  └────────┼───────────────┼────────────┼────────────────┘
                                           ▼               ▼            ▼
                                       Groq API       TTS provider   OSS sandbox / ledger
                                  ┌──────────────┐
                                  │ PostgreSQL   │  ← written ONLY by the Go binary
                                  └──────────────┘
```

## Service map

### App services (Go — v1 ships as ONE modular binary, `cmd/control-plane`)

| Module (internal/…) | Plan | Function |
|---|---|---|
| `gateway` | E01 | front door: authn, RBAC middleware, entitlement checks, rate limits, routing to AI services |
| `identity` | E05 | orgs, accounts, auth backends (OIDC/SAML/LDAP/local), roles from `auth.yaml`, SCIM |
| `entitlements` | E01 | plans (Free/Pro/Enterprise), quotas, Check/Reserve/Commit/Release |
| `usage` | E02 | snapshot ingest, rollups, admin query API, CSV export — **also stores AI-service metering** |
| `fleet` | E03 | device registry, device-command-v1, heartbeats, staged rollout |
| `boards` | E07 | enterprise cross-account Kanban: dispatch + status snapshots |
| `voice` | E04 | *thin now*: entitlement + metering + proxy to `ai-stt`/`ai-tts` (provider calls moved out — this refines E04 §3) |
| `billing` | future | subscriptions (Pro self-serve), seats (Enterprise), Stripe integration |
| `market` | future | marketplace app side: listings, versions, purchases, royalty/lineage ledger |
| `audit` | E05 | append-only audit events + export |

> Deliberately **not** microservices in v1: one Go binary with internal packages keeps
> deploys and transactions simple. Split a module out only when scale forces it.

### AI services (Python — one FastAPI container each, stateless)

| Service | Status | Function | Provider / engine |
|---|---|---|---|
| **`ai-stt`** | **build first** | speech-to-text | **Groq API, `whisper-large-v3` / `-turbo`** |
| `ai-tts` | next (completes E04) | text-to-speech | provider adapter (Phase 0 probe picks) |
| `ai-skill-eval` | per [`skill-evaluation.md`](../../../product/specs/skill-evaluation.md) | runs metric packs: G3 sandbox suites, golden sets, uplift runs; ledger settlement worker | OSS sandbox + market data |
| `ai-search` | future | embeddings + semantic search over marketplace listings/docs | embedding model TBD |
| `ai-judge` | future | LLM triage for listing review — **triage only, never the certified result** | routed via 9router |

## Rules (bind all services)

1. **Python never touches PostgreSQL.** AI services are stateless; all state, metering,
   and accounting flow through the Go binary. One writer, no drift.
2. **Go owns auth; Python trusts the gateway.** AI services listen on the internal
   network only and require a per-deployment service token (`X-Internal-Token`,
   rotated). They do no user auth, no entitlement logic.
3. **Provider keys live only in AI services** (env/vault-injected). They never appear
   in the Go config, logs, responses, or the OSS runtime — consistent with E04's
   key-custody value proposition.
4. **Every AI response returns metering fields** (e.g. `duration_seconds`,
   `input_bytes`, `model`); the gateway records them into the E02 usage store before
   returning. No un-metered work leaves the platform.
5. **Content is pass-through, never at rest.** Audio/text transits an AI service and
   is discarded; no request bodies in logs; transcripts return to the member's runtime
   and live only there. Only counts and durations are stored centrally.
6. **Capability degradation is graceful.** AI service down → that capability returns
   `503 capability_unavailable`; nothing else is affected; OSS local features never
   depend on any of it.
7. **Phase 0 probe-and-pin per provider** (repo discipline): live-probe the provider
   API, pin observed request/response shapes and limits in findings before coding.

## Plan of record

1. **Phase A — `ai-stt` + gateway voice module** → [implementation.md](implementation.md)
   (Groq Whisper v3; entitlement `voice.stt` = Pro/Enterprise per
   [`product/specs/plans.md`](../../../product/specs/plans.md)).
2. **Phase B — `ai-tts`** (same pattern; completes E04's user-facing loop).
3. **Phase C — `ai-skill-eval` v0** (G3 execution packs + uplift harness — the
   marketplace's certification engine; ledger worker per the addendum's sequencing).
4. **Phase D — `ai-search`**, `ai-judge` as marketplace volume justifies.

## Open questions

1. Deployment unit: docker-compose profile per AI service vs one `ai` profile —
   compose profiles recommended (`--profile voice` enables stt+tts).
2. Internal auth: static rotated token (v1, simple) vs mTLS (later, fleet-scale).
3. Groq key scope: one platform key v1 vs per-org keys in vault (E04 lists org-key
   custody as the sell — v1 platform key, org keys when a customer requires it).
4. Long-audio strategy for STT: reject > cap in v1, chunked transcription later.
