# 008 — Architecture

How delivery targets and blueprints fit XNOBrain's layering without a second
scheduler, store, or API process.

Cross-links: [README.md](README.md) · [findings.md](findings.md) ·
[approaches.md](approaches.md) · [implementation.md](implementation.md) ·
[validation.md](validation.md).

## Layering fit

XNOBrain's fixed boundaries (from `AGENTS.md`): handlers own HTTP translation,
services own rules, repositories own atomic files, integrations adapt Hermes,
models are Pydantic, and `routes/setup.py` is the only route-assembly point.

```
React (Settings > Automations: blueprint gallery + "Deliver to" selector)
   │  HTTP  /api/brain/v1/cron/...
   ▼
handlers/api.py        operation map: cron_blueprints, cron_blueprint_instantiate,
                       cron_delivery_targets, cron_job_targets_list/add/remove,
                       cron_trigger, cron_runs
   ▼
services/platform.py   cron rules: instantiate → Kanban schedule; validate target
   (PlatformService)   type; attach/detach target on the schedule template;
                       trigger (run now); read runs; post-run delivery orchestration
   ▼
integrations/cron_delivery.py   NEW adapter over Hermes cron.blueprint_catalog +
                                cron.scheduler.cron_delivery_targets +
                                gateway.delivery (channel/email routing)
integrations/kanban.py          existing — create/schedule/run tasks, run history
repositories/…                  workspace/file writes for the `file` target
   ▼
Hermes (in-process import)  cron.blueprint_catalog, cron.scheduler,
                            gateway.delivery, hermes_cli.kanban_db
```

No new HTTP hop: local layers call each other directly. The adapter imports
Hermes Python modules in-process, exactly as `integrations/kanban.py` imports
`hermes_cli.kanban_db`. We do **not** HTTP-call Hermes's `/api/cron/*` routes.

## Data flow: job fires → agent runs → output routed to target

Because XNOBrain's cron is Kanban-backed (Plan 003), a "job" is a scheduled
template task on the `default` board and a "fire" is the Kanban dispatcher
promoting/creating an **occurrence** task that a worker runs. Delivery is a
**post-run step XNOBrain owns** — Hermes's native `deliver` only fires inside
its own `run_job` loop, which we do not run.

```
Kanban dispatcher tick (Plan 001/003, the only scheduler)
  → occurrence task created/promoted from the schedule template
  → worker runs, task reaches Done with a run/output summary
  → PlatformService.on_occurrence_complete(occurrence)      [new hook]
      → read attached delivery targets from the template metadata
      → for each target, compute deterministic delivery key
           (skip if already delivered/failed for this occurrence+target)
      → route by target_type:
           channel → cron_delivery.deliver_channel(...)  (Plan 005 dependent)
           email   → cron_delivery.deliver_email(...)     (email gateway config)
           kanban  → kanban.create_task(target board, summary card)
           file    → workspace write beneath profile root (path guard)
      → record delivery result (delivered | failed | degraded) on the occurrence
```

The completion hook attaches to the same place the dispatcher already reports
occurrence completion (Plan 003 execution path). If no such hook exists yet,
add one thin call site in the service that the dispatcher/occurrence-finish path
invokes; it must not be a second loop.

## Where config lives

- **Blueprint catalog:** Hermes `cron/blueprint_catalog.py` (read-only,
  in-image). XNOBrain never stores or forks blueprints.
- **Delivery-target availability:** derived live from
  `cron.scheduler.cron_delivery_targets()` (channel/email platforms the gateway
  has configured) + always-available `kanban` and `file`. Not persisted.
- **Attached targets (per job):** stored on the **Kanban schedule template's
  metadata** in the same board SQLite that already holds Plan 003 schedule
  metadata — no new database, no new file store. Each attached target is a small
  opaque record: `{id, target_type, destination, created_at}` where
  `destination` is a platform id (channel/email), a board slug (kanban), or a
  relative workspace path (file). Credentials are never stored here; channel and
  email resolve their secrets from gateway config at delivery time.
- **Gateway/email/channel secrets:** stay in Hermes gateway config / profile
  `config.yaml` and env (`EMAIL_HOME_ADDRESS`, `*_HOME_CHANNEL`), owned by
  Hermes and (for channels) Plan 005. XNOBrain references them by id only.

## New XNOBrain API contract (versioned)

All under the existing `Cron` tag / `/api/brain/v1/cron` prefix. Request and
response bodies are strict Pydantic models in `xnobrain/models/api.py`. Envelope
matches the existing handler response envelope.

| Method & path | Operation | Request model | Response |
| --- | --- | --- | --- |
| `GET /api/brain/v1/cron/blueprints` | `cron_blueprints` | — | `CronBlueprintList` |
| `POST /api/brain/v1/cron/blueprints/instantiate` | `cron_blueprint_instantiate` | `CronBlueprintInstantiate` | `CronJobSummary` |
| `GET /api/brain/v1/cron/delivery-targets` | `cron_delivery_targets` | — | `CronDeliveryTargetOptions` |
| `GET /api/brain/v1/cron/jobs/{job_id}/delivery-targets` | `cron_job_targets_list` | — | `CronDeliveryTargetList` |
| `POST /api/brain/v1/cron/jobs/{job_id}/delivery-targets` | `cron_job_target_add` | `CronDeliveryTargetCreate` | `CronDeliveryTarget` |
| `DELETE /api/brain/v1/cron/jobs/{job_id}/delivery-targets/{target_id}` | `cron_job_target_remove` | — | `{deleted: true}` |
| `POST /api/brain/v1/cron/jobs/{job_id}/trigger` | `cron_trigger` | — | `CronJobRunSummary` |
| `GET /api/brain/v1/cron/jobs/{job_id}/runs` | `cron_runs` | — | `CronJobRunList` |

Pydantic model names (proposed):

- `CronBlueprintField` — `{name, type, label, default, options, optional,
  strict, help}` (mirrors Hermes form field; no upstream leakage beyond this).
- `CronBlueprintSummary` — `{key, title, description, category, tags, fields,
  schedule, schedule_human, command, app_url}`.
- `CronBlueprintList` — `{blueprints: list[CronBlueprintSummary]}`.
- `CronBlueprintInstantiate` — `{blueprint: str, agent_id: str,
  values: dict[str, Any] = {}, deliver_targets: list[CronDeliveryTargetCreate]
  = []}`. `agent_id` is required because a Kanban schedule needs an assignee
  (unlike Hermes's profile-scoped native job).
- `CronDeliveryTargetType = Literal["channel", "email", "kanban", "file"]`.
- `CronDeliveryTargetCreate` — `{target_type: CronDeliveryTargetType,
  destination: str}`. `destination` meaning by type: channel → platform id
  (e.g. `telegram`) or `platform:chat_id`; email → recipient address or empty
  for the configured home address; kanban → board slug; file → relative
  workspace path.
- `CronDeliveryTarget` — `{id, target_type, destination, available: bool,
  degraded_reason: str | None}`.
- `CronDeliveryTargetList` — `{targets: list[CronDeliveryTarget]}`.
- `CronDeliveryTargetOptions` — `{options: list[{target_type, id, name,
  available, degraded_reason}]}` (what the selector can offer; channel/email
  from `cron_delivery_targets()`, plus static kanban/file).
- `CronJobRun` — `{id, occurrence_id, status, started_at, ended_at,
  deliveries: list[{target_id, target_type, status, at}]}` — no prompt/output/
  tool content.
- `CronJobRunList` — `{runs: list[CronJobRun], limit: int}`.
- `CronJobRunSummary` — `{job, run}` returned by trigger.
- `CronJobSummary` — extends the existing `_schedule_job` shape with
  `delivery_targets: list[CronDeliveryTarget]`.

Validation: `target_type` is a closed `Literal` — an unknown value is a 422 at
the model boundary. A `channel` destination whose platform is not in
`cron_delivery_targets()` is accepted but returns `available=false` +
`degraded_reason` (Plan 005 not connected). A `file` destination is path-checked
against the profile workspace root in the service before persisting.

## Integration adapter (`integrations/cron_delivery.py`)

Thin, policy-free, no HTTP. Responsibilities:

- `list_blueprints() -> list[dict]` — wrap `CATALOG` via
  `blueprint_catalog_entry`; rewrite `deliver` field options from
  `available_channel_email_targets()` (mirrors the Hermes route behavior).
- `fill(blueprint_key, values) -> dict` — `get_blueprint` + `fill_blueprint`;
  translate `BlueprintFillError` to a typed adapter error for the service to map
  to 422; return the `{prompt, schedule, name, deliver, skills}` spec.
- `available_channel_email_targets() -> list[dict]` — `local` +
  `cron.scheduler.cron_delivery_targets()`, tagged with `target_type` (`email`
  for `email`, `channel` for the rest).
- `deliver_channel(content, platform, chat_id, job_id, job_name) -> result` —
  build a `DeliveryTarget` and route via a `DeliveryRouter` bound to the profile
  gateway config + adapters (Plan 005 supplies adapters). If no adapter/config,
  return a degraded result (do not raise).
- `deliver_email(content, address, job_id, job_name) -> result` — route via the
  email `DeliveryTarget`/home address from gateway config. Independent of Plan
  005.

Kanban and file delivery live in the **service** (they use existing XNOBrain
integrations `kanban.py` and the workspace/files repo), not in this adapter, so
the adapter stays a pure Hermes boundary.

## How a channel target references Plan 005

Plan 005 (Messaging Channels) owns connecting platforms (telegram/discord/…):
credentials, adapters, and the gateway config that `cron_delivery_targets()`
reads. A XNOBrain `channel` delivery target stores only the **platform id**
(and optional `chat_id`). At delivery time, `deliver_channel` resolves the live
adapter from the Plan 005 gateway config. Before Plan 005, `cron_delivery_targets()`
returns no channel platforms, so a stored channel target reports
`available=false` and `degraded_reason="channel not connected"`. When Plan 005
connects a platform, the same stored target becomes live with no migration.

## React UI

Under Settings > Automations (Plan 003 already established this destination):

- **Blueprint gallery** — cards from `GET /cron/blueprints`, grouped by
  `category`, showing `title`, `description`, `schedule_human`, tags. "Use"
  opens a form generated from `fields[]` (time/enum/text/weekdays inputs), plus
  an assignee (agent) picker and an optional "Deliver to" selector. Submit →
  `POST /cron/blueprints/instantiate`; on success navigate to the created board
  card.
- **"Deliver to" selector on a cron job** — on the job detail/editor, a control
  listing attached targets and an "Add target" affordance. The add flow picks a
  `target_type` (channel | email | kanban | file) then a destination:
  channel/email from `GET /cron/delivery-targets` options (disabled +
  "connect a channel first" when `available=false`), kanban → board picker,
  file → workspace path input. Attached targets show a badge and a remove
  control. A "Run now" button calls `POST /cron/jobs/{id}/trigger`; a runs
  panel reads `GET /cron/jobs/{id}/runs` and shows per-run delivery status.

Frontend files: extend `src/api/crons.ts` (add blueprint/target/trigger/
runs calls), `src/hooks/useCrons.ts` (or a new `useBlueprints`/
`useDeliveryTargets` hook), and the automations feature view. Add strings to
every locale in `src/locales/*.json`.

## ASCII sequence diagram — daily brief → Telegram

```
User        React            XNOBrain API         Service            cron_delivery/Kanban        Hermes/Gateway
 │  pick "Morning Brief"     │                     │                       │                          │
 │──"Use" blueprint────────► │                     │                       │                          │
 │  fill time=08:00,         │  GET /cron/blueprints                        │                          │
 │  deliver→channel:telegram │───────────────────► │ list_blueprints()     │                          │
 │                           │ ◄─────────────────── │──────────────────────┼─ blueprint_catalog_entry ►│
 │  submit + agent + target  │  POST /cron/blueprints/instantiate           │                          │
 │──────────────────────────►│───────────────────► │ fill_blueprint()      │                          │
 │                           │                     │──────────────────────┼─ fill_blueprint(...) ────►│
 │                           │                     │ create Kanban schedule │  create_task(default)    │
 │                           │                     │ attach channel target  │  (schedule + target meta)│
 │                           │ ◄─── CronJobSummary ─┤                       │                          │
 │                           │                     │                       │                          │
 │   ...08:00 next day...    │      Kanban dispatcher tick (the only scheduler)                        │
 │                           │                     │  occurrence created → worker runs → Done + output │
 │                           │                     │ on_occurrence_complete │                          │
 │                           │                     │  key=occ:target dedup  │                          │
 │                           │                     │  deliver_channel(text, telegram) ───────────────►│ DeliveryRouter → Telegram adapter (Plan 005)
 │                           │                     │  record delivered      │                          │
 │  open Runs panel          │  GET /cron/jobs/{id}/runs                    │                          │
 │──────────────────────────►│───────────────────► │ list_runs()           │  run history (safe)      │
 │                           │ ◄── runs + delivery status ─────────────────┤                          │
```
