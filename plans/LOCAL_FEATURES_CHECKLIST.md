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
| 010 | **Team runs v2** (persistent, observable, cancellable multi-agent runs) | P2 | — | [README](010_team_runs/README.md) · [findings](010_team_runs/findings.md) · [architecture](010_team_runs/architecture.md) · [approaches](010_team_runs/approaches.md) · [implementation](010_team_runs/implementation.md) · [validation](010_team_runs/validation.md) |
| 011 | **Multi-account provider connections** (9router native multi-account) | P1 | — | [README](011_provider_connections/README.md) · [findings](011_provider_connections/findings.md) · [architecture](011_provider_connections/architecture.md) · [approaches](011_provider_connections/approaches.md) · [implementation](011_provider_connections/implementation.md) · [validation](011_provider_connections/validation.md) |
| 012 | **Model Blends** (user-named 9router combos: fallback / round-robin / fusion) | P2 | soft: 011 | [README](012_model_blends/README.md) · [findings](012_model_blends/findings.md) · [architecture](012_model_blends/architecture.md) · [approaches](012_model_blends/approaches.md) · [implementation](012_model_blends/implementation.md) · [validation](012_model_blends/validation.md) |

**Recommended order:** 005 → 006 → 007 → 008 → 009. 005/006/007/009 are independent and may
be parallelized; 008's channel-delivery target requires 005 (its email/Kanban/file targets
do not). The second batch (010–012) is independent of the first: recommended 011 → 012 →
010 (011 is the sharpest user pain; 012 builds on the same 9router adapter surface; 010 is
self-contained). 009 is implemented and verified (2026-07-25).

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
- [ ] Aggregate the pre-summed **`sessions`** table (one row per conversation), never the per-message table; window-filter on `idx_sessions_started`.
- [ ] **Many-agent scaling:** each agent has its own `state.db`, so cross-agent totals must read every profile. Cost must scale with *active* agents, not *total* — implement per-agent partial cache with **`os.stat` mtime-skip** (re-read only changed profiles) + **bounded-concurrency** reads (`asyncio.gather` + semaphore), plus the merged-result TTL cache. UI list paging does **not** substitute for this. **Do not add an embedded OLAP engine (DuckDB) unless a *measured* bottleneck triggers the escalation ladder** — see `009_usage_analytics/approaches.md` Decision E and `architecture.md` → Scaling to many agents.
- [ ] Per-model, per-agent, and time-series aggregation; 9router quota overlay; estimated-vs-actual-cost caveat surfaced.
- [ ] Per-agent budget config in `config.yaml` (advisory soft-warning; hard enforcement stays Enterprise-only) with snapshot-before-write.
- [ ] Models + handler operations + routes (usage summary, per-agent, per-model, time-series, **`GET /analytics/agents`** for the picker, get/set budget). Read endpoints accept the Grafana-style controls: **`agents` (CSV; absent = all)**, **relative `days` or absolute `from`/`to`**, and **`bucket` = hour|day|week|month**.
- [ ] React: **Grafana-style control bar** — agent **multi-select (default All, or a subset)**, time-range picker (presets + custom from/to), and bucket selector; selection persisted (URL + localStorage) and drives all panels. Plus the Analytics dashboard (totals, per-model bars, time chart, budget bar) using an inline SVG chart (no new dependency).
- [ ] Security: no prompt/credential content exposed; read-only proven.
- [ ] `validation.md` acceptance passed (real session records produce correct totals + a budget warning) with evidence.

---

## Deferred (candidates for a future batch — not planned here)

Session full-text search across conversations (Hermes `session_search_tool` / FTS5);
self-improvement visualization (Hermes `curator` + `/api/learning/graph`); Git / code-review
workspace (`/api/git/*`); credentials pool (`/api/credentials/pool`); dashboard theming;
in-app Hermes runtime update (`/api/hermes/update`). Each would follow the same six-file
plan format and the program principles above.

## 010 — Team runs v2

- [ ] Phase 0: compatibility test pins the Hermes/team symbols the engine uses (`AgentManager.chat`, subprocess helpers) and locks the `_run_hermes_command` CancelledError behavior.
- [ ] **Subprocess-kill fix:** `_run_hermes_command` gains an `except asyncio.CancelledError` branch that kills the child `hermes` process (mirroring `_chat_stream_events`) — without it, cancelling a team run orphans subprocesses.
- [ ] Run records persisted as atomic files under `DATA_DIR/teams/runs/<team_id>/<run_id>.json` (status machine: pending → running → completed/failed/cancelled); stale `running` records marked `interrupted_by_restart` on first read.
- [ ] Async run API: `POST /api/v1/teams/{id}/runs` (202), list/detail/cancel routes, and an SSE events route following the `kanban_event_stream` handler pattern; existing synchronous `POST /run` stays backward-compatible.
- [ ] Shared `_execute_workflow` engine used by both sync and async paths; in-process run registry; cancellation terminates child subprocesses (pgrep-verified in validation).
- [ ] Frontend: TeamsView Runs panel (history, live per-step status via SSE, cancel).
- [ ] Cross-profile teams keep chat-per-step (per the approaches analysis — Hermes `delegate_tool` cannot span profiles); no delegate_tool migration in this plan.
- [ ] `validation.md` acceptance passed with evidence.

## 011 — Multi-account provider connections

- [ ] Phase 0: live-probe test against the pinned 9router (0.5.40) — verify `PUT /api/providers/{id}` body shape for `priority`/`isActive`, response field names, and rotation-vs-priority semantics; skip cleanly when 9router is down.
- [ ] Adapter: `list_connections` returns per-connection `priority`; new methods for patch (active/priority), per-connection test, and `usage_for_connection(connection_id)`.
- [ ] Connection-level API: list/add/patch/test/delete a **single** connection under `/agent-gateway/v1/providers/{provider_id}/connections*`; provider "connected" = ≥1 active connection; provider-level disconnect stays as explicit remove-all with confirmation.
- [ ] Keep the curated 6-provider allowlist for now (Decision A); configurable widening is a follow-up flag.
- [ ] Frontend: provider cards expand to an accounts list (name/email, active toggle, priority reorder, test dot, per-row quota bar, remove) + per-provider "Add account" (api-key form or OAuth popup reusing existing flows).
- [ ] Security: no api-key/token material ever appears in Brain4All responses or logs (response-scan test); all credential state lives in 9router only.
- [ ] `validation.md` acceptance passed with evidence.

## 012 — Model Blends

- [ ] Phase 0: probe the pinned 9router — combos CRUD shapes, `/api/settings` GET/PATCH shape for `comboStrategy`/`comboStrategies`/`comboStickyRoundRobinLimit`, combo name charset, and how combos appear in `/v1/models`.
- [ ] Adapter refactor: public combo CRUD methods (extracted from `_ensure_auto_combo` internals) + strategy accessors; **fix `list_models` so user blends are not silently filtered out** (today's owner filter hides every custom combo).
- [ ] Blend API: `GET/POST /agent-gateway/v1/blends`, `PATCH/DELETE /blends/{id}`, `GET /blends/available-models`; strategy embedded in the blend DTO (hydrated from 9router settings); Brain4All stores nothing.
- [ ] Guards: "auto" is a read-only system blend (409 on modify/delete); blend names cannot collide with real model ids.
- [ ] Strategies exposed: fallback (ordered), round-robin (sticky limit), fusion (judge model + tuning) with the verified cost/tools caveat surfaced in UI copy ("fusion runs every model per request; tools are disabled").
- [ ] Frontend: Model Blends management panel (list, create/edit dialog with ordered model multi-select + strategy controls, delete) and blends grouped at the top of agent model pickers; an agent's model accepts a blend name through the existing config path unchanged.
- [ ] `validation.md` acceptance passed with evidence.
