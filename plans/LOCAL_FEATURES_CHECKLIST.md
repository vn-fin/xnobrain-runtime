# Local-user feature program — implementation checklist

This is the execution index for the Brain4All **local-user feature program**: surfacing
Hermes-native capabilities the app already *could* offer a self-hosted user but has not
wired yet. It is a sibling to [the Kanban program](CHECKLIST.md); the two are independent.

Implement each plan in the order below. An item is complete only when its acceptance
checks in that plan's `validation.md` pass with evidence — flipping a checkbox without the
evidence is not complete.

Legend: `[ ]` not started · `[~]` in progress · `[x]` verified · `[!]` blocked (record the
blocker beside the item).

## What already exists (do not re-plan)

Agents, conversations/runs (SSE + approval + stop), skills, memory, MCP config, **cron
(basic CRUD)**, **Kanban** (plans 000–004), teams, providers (9router), snapshots,
workspace files, portable bundles. See `brain4all/routes/setup.py` for the current surface.

## Program principles (every plan obeys these)

- One FastAPI/Hermes process (`:8642`) + one 9router (`:20128`). **No Go, PostgreSQL, ORM,
  or second API process.**
- Preserve the original Hermes core. **Extend from the `brain4all` package; ride Hermes'
  native APIs/config; never copy, fork, or re-implement Hermes internals.**
- `brain4all/routes/setup.py` is the only route-assembly point. handlers = HTTP/SSE,
  services = rules, repositories = atomic files, integrations = adapt Hermes/9router,
  models = Pydantic. Local layers call each other directly.
- Provider forced to 9router. No application DB — Brain4All state is atomic files under
  `DATA_DIR` (temp → fsync → rename); snapshot **before** any persistence-promising
  mutation. Hermes state (`state.db`, `config.yaml`) is authoritative and Brain4All only
  reads it read-only unless mutating through a Hermes public API.
- Never return, log, or trace credentials, tokens, prompts, or provider keys. No mock or
  demo data in production paths.
- **Phase 0 gate for every plan:** pin the Hermes dependency to an immutable release/commit
  and add a compatibility test that imports the exact public Hermes symbols/endpoints the
  plan uses. If a needed capability is Hermes-private, expose a small public hook in the
  `hermes_cli` extension surface — do not copy internals.

## Plan documents

| # | Plan | Priority | Depends on | Docs |
|---|------|----------|-----------|------|
| 005 | **Messaging channels** (Telegram/Discord/Slack/WhatsApp/Signal via the gateway) | P1 | — | [README](005_messaging_channels/README.md) · [findings](005_messaging_channels/findings.md) · [architecture](005_messaging_channels/architecture.md) · [approaches](005_messaging_channels/approaches.md) · [implementation](005_messaging_channels/implementation.md) · [validation](005_messaging_channels/validation.md) |
| 006 | **Voice I/O** (TTS speak + STT transcribe) | P2 | — | [README](006_voice_io/README.md) · [findings](006_voice_io/findings.md) · [architecture](006_voice_io/architecture.md) · [approaches](006_voice_io/approaches.md) · [implementation](006_voice_io/implementation.md) · [validation](006_voice_io/validation.md) |
| 007 | **Plugins & hooks + native tool toggles** | P2 | — | [README](007_plugins_and_hooks/README.md) · [findings](007_plugins_and_hooks/findings.md) · [architecture](007_plugins_and_hooks/architecture.md) · [approaches](007_plugins_and_hooks/approaches.md) · [implementation](007_plugins_and_hooks/implementation.md) · [validation](007_plugins_and_hooks/validation.md) |
| 008 | **Cron delivery targets & blueprints** | P2 | 005 (for channel targets); Kanban 001–004 (for Kanban target) | [README](008_cron_delivery_and_blueprints/README.md) · [findings](008_cron_delivery_and_blueprints/findings.md) · [architecture](008_cron_delivery_and_blueprints/architecture.md) · [approaches](008_cron_delivery_and_blueprints/approaches.md) · [implementation](008_cron_delivery_and_blueprints/implementation.md) · [validation](008_cron_delivery_and_blueprints/validation.md) |
| 009 | **Usage analytics & budgets** | P3 | — | [README](009_usage_analytics/README.md) · [findings](009_usage_analytics/findings.md) · [architecture](009_usage_analytics/architecture.md) · [approaches](009_usage_analytics/approaches.md) · [implementation](009_usage_analytics/implementation.md) · [validation](009_usage_analytics/validation.md) |

**Recommended order:** 005 → 006 → 007 → 008 → 009. 005/006/007/009 are independent and may
be parallelized; 008's channel-delivery target requires 005 (its email/Kanban/file targets
do not).

---

## 005 — Messaging channels

- [ ] Phase 0: pin Hermes + compatibility test for `POST /api/gateway/{start,stop,restart,drain}`, `/api/messaging/platforms*`, pairing/onboarding routes, and the `gateway.config` public primitives the plan names.
- [ ] `integrations/gateway.py` adapter over the Hermes gateway API + profile `config.yaml` gateway section (no re-implemented dispatch loop).
- [ ] `services/channels.py` + handler operations + Pydantic models (list/enable/disable channels, set/rotate credentials, gateway lifecycle, pairing).
- [ ] Versioned routes added to `routes/setup.py` (per-agent channel + gateway routes).
- [ ] Per-agent ↔ profile ↔ multiplex decision implemented per the plan (port-binding channels scoped correctly).
- [ ] React "Channels" surface (enable a channel, show status, pair).
- [ ] Security: bot tokens never appear in responses, logs, traces, or portable bundles (dedicated test).
- [ ] `validation.md` acceptance passed (real Telegram round-trip) with evidence.

## 006 — Voice I/O

- [ ] Phase 0: pin Hermes + compatibility test for `/api/audio/{speak,transcribe}`, `/api/audio/elevenlabs/voices`, and the `tts_registry`/`transcription_registry` `list_providers` symbols.
- [ ] `integrations/voice.py` adapter over the Hermes audio API/tools; per-agent voice config in `config.yaml` (enabled/provider/voice-id) via the additive upstream hook the plan specifies.
- [ ] Models + handler operations + routes (list voices/providers, synthesize, transcribe, get/set per-agent voice config); binary audio handled explicitly (base64 in the envelope or raw-response).
- [ ] React: play button in chat, mic capture in the composer, a Voice settings panel; i18n covered.
- [ ] Security: provider keys (e.g. ElevenLabs) stay server-side and never leak.
- [ ] `validation.md` acceptance passed (hear a reply; a voice note becomes a transcript message) with evidence.

## 007 — Plugins & hooks + native tool toggles

- [ ] Phase 0: pin Hermes + compatibility test for `/api/dashboard/plugins*`, `/agent-plugins/*`, `plugins_cmd.dashboard_*`, `plugins.VALID_HOOKS`, `toolsets.TOOLSETS`, and `osv_check`/`skills_guard`.
- [ ] `integrations/plugins.py` + `services/plugins.py` with the **mandatory safety gate**: install-without-enable → OSV/malware scan (`osv_check` + `skills_guard`) → explicit content-hash-bound approval → only then enable.
- [ ] Models + handler operations + routes (list installed, browse hub, install, enable/disable/update/remove per agent, list & toggle native tools per agent).
- [ ] React: Plugins page + per-agent Tools/Capabilities panel.
- [ ] Security test: a plugin **cannot** be enabled without a passing scan + fresh approval (asserts the Hermes enable call is never reached otherwise).
- [ ] `validation.md` acceptance passed (incl. the security acceptance item) with evidence.

## 008 — Cron delivery targets & blueprints

- [ ] Phase 0: pin Hermes + compatibility test for `/api/cron/{delivery-targets,blueprints,blueprints/instantiate,fire}`, `/api/cron/jobs/{id}/{trigger,runs}`, and the cron/blueprint/delivery modules.
- [ ] Extend the existing cron integration/service with delivery targets (`channel|email|kanban|file`) and blueprints; no copied scheduler.
- [ ] Models + handler operations + routes (list/create/instantiate blueprints; list/add/remove targets; attach a target to a job; trigger; list runs).
- [ ] Channel target type wired to Plan 005; email/Kanban/file targets work independently of 005.
- [ ] React: blueprint gallery + a "deliver to" selector on a cron job.
- [ ] `validation.md` acceptance passed (instantiate a blueprint, attach a target, trigger, confirm delivery + run record) with evidence.

## 009 — Usage analytics & budgets

- [ ] Phase 0: pin Hermes + compatibility test asserting the `sessions` schema columns the aggregation reads, the read-only helpers, and the 9router `usage()` shape.
- [ ] `services/analytics.py` computes aggregates **on read** across each agent's `state.db` opened read-only (`?mode=ro`); **never writes `state.db`**; no new persistent store.
- [ ] Per-model, per-agent, and time-series aggregation; 9router quota overlay; estimated-vs-actual-cost caveat surfaced.
- [ ] Per-agent budget config in `config.yaml` (advisory soft-warning; hard enforcement stays Enterprise-only) with snapshot-before-write.
- [ ] Models + handler operations + routes (usage summary, per-agent, per-model, time-series, get/set budget).
- [ ] React: Analytics dashboard (totals, per-model bars, time chart, budget bar) using an inline SVG chart (no new dependency).
- [ ] Security: no prompt/credential content exposed; read-only proven.
- [ ] `validation.md` acceptance passed (real session records produce correct totals + a budget warning) with evidence.

---

## Deferred (candidates for a future batch — not planned here)

Session full-text search across conversations (Hermes `session_search_tool` / FTS5);
self-improvement visualization (Hermes `curator` + `/api/learning/graph`); Git / code-review
workspace (`/api/git/*`); credentials pool (`/api/credentials/pool`); dashboard theming;
in-app Hermes runtime update (`/api/hermes/update`). Each would follow the same six-file
plan format and the program principles above.
