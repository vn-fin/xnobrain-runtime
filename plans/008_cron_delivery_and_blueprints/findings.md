# 008 — Findings

Evidence gathered from the pinned Hermes source under `.tools/hermes-agent/`
(read-only) and the current Brain4All tree. Line references are from the state
inspected while writing this plan; re-verify during Phase 0.

Cross-links: [README.md](README.md) · [architecture.md](architecture.md) ·
[approaches.md](approaches.md) · [implementation.md](implementation.md) ·
[validation.md](validation.md).

## What Hermes provides

### Native cron dashboard routes (`hermes_cli/web_server.py`)

These are Hermes's own dashboard HTTP routes. They confirm the shapes we mirror,
but Brain4All **rides the underlying Python modules in-process** (like
`integrations/kanban.py` imports `hermes_cli.kanban_db`) rather than HTTP-calling
these routes.

- `GET /api/cron/delivery-targets` (≈ line 12157) →
  `{"targets": [{"id","name","home_target_set","home_env_var"}, ...]}`.
  Always prepends the implicit `local` target, then extends with
  `cron.scheduler.cron_delivery_targets()`.
- `GET /api/cron/blueprints` (≈ 12382) →
  `{"blueprints": [entry, ...]}` where each `entry` is
  `cron.blueprint_catalog.blueprint_catalog_entry(...)`. The route rewrites the
  `deliver` field's `options` from the user's connected platforms.
- `POST /api/cron/blueprints/instantiate` (≈ 12416), body
  `AutomationBlueprintInstantiate = {blueprint: str, values: dict}`, query
  `?profile=`. Calls `fill_blueprint(...)` then `cron.jobs.create_job(**spec)`
  and returns the created native job. Raises 404 unknown blueprint, 422 on
  `BlueprintFillError`.
- `POST /api/cron/jobs/{job_id}/trigger` (≈ 12264) → `cron.jobs.trigger_job` —
  marks a native job to run at next tick.
- `GET /api/cron/jobs/{job_id}/runs` (≈ 12109) → `{"runs":[...], "limit":n}`;
  runs are ordinary sessions whose id is `cron_{job_id}_{timestamp}` (see
  `cron/scheduler.py::run_job`), listed via `SessionDB.list_cron_job_runs`.
- `POST /api/cron/fire` (≈ 12313) — **managed-Chronos NAS→agent webhook**, JWT
  gated, background `provider.fire_due`. **Out of scope**: this is the scale-to-
  zero managed path, not a user action, and it runs the native scheduler.
- `POST /api/cron/jobs` (≈ 12152) native create; `CronJobCreate` (≈ 11788) has
  `prompt, schedule, name, deliver="local", skills, model, provider, base_url,
  script, context_from, enabled_toolsets, workdir, no_agent`.

### Blueprint catalog (`cron/blueprint_catalog.py`, 713 lines)

Single source of truth for parameterized automations. Pure Python, no scheduler.

- `CATALOG: list[AutomationBlueprint]` — curated in-repo blueprints.
- `AutomationBlueprint(key, title, description, category, schedule_template,
  prompt_template, slots: list[BlueprintSlot], deliver_default="origin",
  skills, tags)`.
- `BlueprintSlot(name, type in {time,enum,text,weekdays}, label, default,
  options, optional, help, strict)`. A non-strict `deliver` enum slot exists so
  the real platform set is validated downstream.
- `get_blueprint(key) -> AutomationBlueprint | None`.
- `blueprint_form_schema(bp) -> {key,title,description,category,tags,fields[]}`
  where each field is `{name,type,label,default,options,optional,strict,help}`.
- `blueprint_catalog_entry(bp)` = form schema **plus**
  `{schedule, scheduleHuman, command, appUrl}`.
- `fill_blueprint(bp, values, *, origin=None) -> dict` — validates values
  (unknown-slot rejection, required-slot check, strict-enum check), resolves
  the schedule and prompt, and returns a `create_job` kwargs dict:
  `{prompt, schedule, name, deliver, [skills], [origin]}`. Raises
  `BlueprintFillError` (subclass of `ValueError`).

Key point: `fill_blueprint` yields a **generic job spec** (`prompt`, `schedule`,
`name`, `deliver`, `skills`). We reuse this to seed a **Kanban schedule**, not a
native cron job — no second scheduler.

### Delivery-target discovery (`cron/scheduler.py`)

- `cron_delivery_targets() -> list[dict]` (≈ 1095) — the platforms a job can
  auto-deliver to. A platform is included when it is a known delivery platform
  **and** its gateway is configured (enabled + credentials). Each entry:
  `{"id","name","home_target_set","home_env_var"}`. Callers prepend `local`.
- `_KNOWN_DELIVERY_PLATFORMS` (≈ 244) includes `telegram, discord, slack,
  whatsapp, signal, matrix, mattermost, ..., sms, email, webhook`.
- `_HOME_TARGET_ENV_VARS` (≈ 253) maps each platform to its home-target env var,
  e.g. `email → EMAIL_HOME_ADDRESS`, `telegram → TELEGRAM_HOME_CHANNEL`.
- **Email is a first-class gateway platform** (`Platform.EMAIL = "email"`,
  `gateway/config.py` ≈ 290; SMTP via `EMAIL_SMTP_HOST`, `EMAIL_ADDRESS`,
  `EMAIL_PASSWORD` ≈ 2038). So email delivery does **not** require Plan 005's
  channel registry — it rides the email gateway config directly.

### Delivery mechanism (`gateway/delivery.py`, `gateway/delivery_ledger.py`)

- `DeliveryTarget` (≈ 142) parses target strings: `origin`, `local`,
  `<platform>`, `<platform>:<chat_id>[:<thread_id>]`. `Platform.LOCAL = "local"`
  writes to `get_hermes_home()/cron/output`.
- `DeliveryRouter` (≈ 222) `async deliver(content, targets, job_id, job_name,
  metadata)` routes to platform adapters or local files; silence-narration
  filtering; `MAX_PLATFORM_OUTPUT = 4000` truncation with a "saved to …" footer.
- `delivery_ledger.py` — durable at-least-once ledger in shared `state.db`
  (`record_obligation / mark_attempting / mark_delivered / mark_failed /
  sweep_recoverable`, `compute_obligation_id`). It is **gateway-scoped** (it
  drives the gateway send loop), so we do not reuse its rows directly, but it is
  the model for our own idempotent post-run delivery key.
- There is **no native `kanban` or workspace-`file` delivery platform.** `local`
  writes to `cron/output`, not the agent workspace. Kanban and workspace-file
  delivery must be **Brain4All-owned** post-run steps.

### Cron→Kanban bridge (`gateway/kanban_watchers.py`, ~1000 LOC)

Gateway background-loop mixin that subscribes to Kanban boards, delivers
notifications/artifacts, and drives the multi-agent dispatcher. It is the
gateway's Kanban integration, not a per-job delivery target. We do **not** run
it; Brain4All already owns the Kanban dispatcher (Plan 001/003) and creates
cards through `services/kanban.py`.

## What Brain4All's cron has today

Current operations (all present):

- Routes (`brain4all/routes/setup.py` ≈ 84–89): `GET/POST
  /agent-gateway/v1/cron/jobs`, `POST .../{job_id}/pause|resume|run`,
  `DELETE .../{job_id}`.
- Handler map (`brain4all/handlers/api.py` ≈ 97–99):
  `cron_list, cron_create, cron_pause, cron_resume, cron_run, cron_delete`.
- Service (`brain4all/services/platform.py` ≈ 351–454): `list_crons`,
  `create_cron`, `set_cron_enabled`, `delete_cron`, `run_cron`, `_schedule_job`.
  **These are Kanban-backed** (Plan 003): a cron job is a `scheduled` task on the
  `default` board with `schedule` metadata; `run_cron` calls
  `kanban.schedule_action("default", id, "run_now")`.
- Model (`brain4all/models/api.py` ≈ 39): `CronCreate = {agent_id, name,
  prompt, interval_minutes, schedule, timezone, mode="local"}`.
- Legacy file repo (`brain4all/repositories/files.py` ≈ 141–174):
  `list_crons/put_cron/delete_cron` over per-profile YAML — retained for
  migration/back-compat, not the live path.
- Frontend (`src/src/api/crons.ts`, `src/src/hooks/useCrons.ts`,
  `src/src/features/system/SystemView.tsx`): list/create/setState/remove only.

## What Brain4All lacks (the exact gap)

1. **No delivery targets.** A cron job's output is not routed anywhere by
   Brain4All. There is no target model, no "deliver to" API, no post-run
   delivery step. The Kanban-backed occurrence produces a run/output that simply
   sits in the run history.
2. **No blueprints.** No catalog, no instantiate path, no gallery. Users hand-
   author every job via `CronCreate`.
3. **No trigger/runs contract by name.** `run_cron` exists (run now) but there
   is no versioned "trigger" + "list runs" pair exposing run records for a job.

The gap is additive: we keep the Kanban-backed cron model and layer targets +
blueprints + a post-run delivery step on top, reusing Hermes's catalog and
delivery router in-process.

## Compatibility surface to pin

Pin the Hermes commit and assert (Phase 0 test) that these import and keep their
shapes:

- `from cron.blueprint_catalog import CATALOG, get_blueprint,
  blueprint_catalog_entry, blueprint_form_schema, fill_blueprint,
  BlueprintFillError` — and that `blueprint_catalog_entry` returns
  `key/title/description/category/tags/fields/schedule/scheduleHuman/command/
  appUrl`, and `fill_blueprint` returns a dict with `prompt/schedule/name/
  deliver`.
- `from cron.scheduler import cron_delivery_targets` — returns a list of dicts
  with `id/name/home_target_set/home_env_var`.
- `from gateway.delivery import DeliveryTarget, DeliveryRouter` and
  `from gateway.config import Platform, load_gateway_config` — for channel/email
  routing; assert `Platform.EMAIL` and `Platform.LOCAL` exist.
- The existing Kanban surface already pinned by Plan 001
  (`hermes_cli.kanban_db`, `services/kanban.py`).

If any import moves or a shape changes, fail startup readiness with one concise
remediation message (same policy as Plan 001 Phase 0). Do not degrade silently.

## Risks

- **Credential / output leakage.** Delivery moves job output across boundaries
  (email SMTP, channel adapters, Kanban cards, workspace files). Never log the
  content, the recipient credentials, SMTP secrets, home-target env values, or
  absolute stored paths. Responses expose only target ids/types and a delivery
  status; the delivered body is not echoed back in the API.
- **Double delivery.** A retried trigger or a restart mid-delivery could send
  twice. Use a deterministic delivery key per (occurrence, target) — mirroring
  Plan 003's occurrence key and the delivery ledger's `compute_obligation_id` —
  and record delivered/failed so a replay is a no-op.
- **Channel target before Plan 005.** A `channel` target for an unconnected
  platform must not error the whole run; store it and report a degraded reason.
- **Workspace-file traversal.** A `file` target path must resolve beneath the
  agent's profile workspace root (reuse the existing workspace path guard);
  reject traversal/symlink escapes.
- **Blueprint prompt injection into schedule.** `fill_blueprint` already
  validates slots and rejects unknown ones; do not bypass it. Re-validate the
  produced `schedule` before creating the Kanban schedule.
- **Recursive cron.** Preserve upstream cron job-session guards (Plan 003 rule):
  a delivered card/file/email must not spawn another scheduling loop.

## Open questions

1. **Where do attached targets live?** Proposed: on the Kanban schedule task
   metadata in the same board SQLite (consistent with Plan 003 storing schedule
   metadata there). Alternative: profile `config.yaml`. Decide in Phase 2 (see
   [architecture.md](architecture.md#where-config-lives)).
2. **Does an occurrence's output need HTML/markdown shaping per target?** MVP:
   plain safe text (same body to every target), reusing the delivery router's
   truncation. Rich formatting is a follow-up.
3. **Channel default when Plan 005 lands:** should `deliver=origin` semantics be
   exposed, or only explicit `channel:<platform>` targets? MVP: explicit only;
   `origin` needs a messaging session context this plan does not own.
4. **Runs source:** reuse the Kanban occurrence/run history the board already
   exposes, or the native `cron_{job}_{ts}` session ids? Since our jobs are
   Kanban-backed, prefer the Kanban run history (Plan 001/004). Confirm the
   field the board exposes for a completed occurrence's output.
