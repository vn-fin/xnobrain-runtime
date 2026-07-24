# 008 — Implementation

Ordered, phased, file-by-file steps. Follow the chosen approach in
[approaches.md](approaches.md): ride Hermes `cron.blueprint_catalog` +
`gateway.delivery`, instantiate into a Kanban schedule, deliver post-run.

Cross-links: [README.md](README.md) · [findings.md](findings.md) ·
[architecture.md](architecture.md) · [validation.md](validation.md).

Constraints restated (obey all): no Go/PostgreSQL/ORM/second API process; one
FastAPI/Hermes (:8642) + one 9router (:20128); preserve Hermes core and extend
from `brain4all`; never copy/fork Hermes internals (no second scheduler/dispatch
loop); `routes/setup.py` is the only route-assembly point; handlers/services/
repositories/integrations/models layering; provider forced 9router; no app DB;
atomic files; snapshot before mutations; never log/return credentials; pin
Hermes + compatibility test; no mock/demo data.

## Phase 0 — Pin + compatibility test {#phase-0}

1. **Pin Hermes.** In `Dockerfile.backend`, replace the moving
   `ARG HERMES_BRANCH=main` with the immutable commit/tag that exports the
   modules in [findings.md](findings.md#compatibility-surface-to-pin). Record the
   version and upgrade steps in `docs/development.md` and `docs/architecture.md`
   (reuse the Plan 001 pin if it already covers these modules — verify it does).
2. **Compatibility test.** Add to `brain4all/tests/test_cron_delivery.py` a
   `CronDeliveryCompatibilityTests` (skipped unless Hermes importable, like
   `test_kanban.py` lines 20–25) that asserts:
   - `from cron.blueprint_catalog import CATALOG, get_blueprint,
     blueprint_catalog_entry, blueprint_form_schema, fill_blueprint,
     BlueprintFillError` succeed and `CATALOG` is non-empty;
   - `blueprint_catalog_entry(CATALOG[0])` has keys `key,title,description,
     category,tags,fields,schedule,scheduleHuman,command,appUrl`;
   - `fill_blueprint(CATALOG[0], <valid values>)` returns a dict with
     `prompt,schedule,name,deliver`;
   - `from cron.scheduler import cron_delivery_targets` returns a list (possibly
     empty) whose entries, when present, have `id,name,home_target_set,
     home_env_var`;
   - `from gateway.delivery import DeliveryTarget, DeliveryRouter` and
     `from gateway.config import Platform` succeed; `Platform.EMAIL` and
     `Platform.LOCAL` exist.
3. **Readiness guard.** If any import/shape check fails at startup, fail
   readiness with one concise remediation message (mirror Plan 001 policy). Do
   not partially enable delivery.

## Phase 1 — Models

File: `brain4all/models/api.py` (add near the existing `CronCreate`, ≈ line 39).

Add (names from [architecture.md](architecture.md#new-brain4all-api-contract)):

```python
CronDeliveryTargetType = Literal["channel", "email", "kanban", "file"]

class CronDeliveryTargetCreate(BaseModel):
    target_type: CronDeliveryTargetType
    destination: str = Field(min_length=0, max_length=512)

class CronDeliveryTarget(BaseModel):
    id: str
    target_type: CronDeliveryTargetType
    destination: str
    available: bool
    degraded_reason: str | None = None

class CronBlueprintField(BaseModel):
    name: str; type: str; label: str
    default: Any | None = None
    options: list[Any] = []
    optional: bool = False; strict: bool = True; help: str = ""

class CronBlueprintSummary(BaseModel):
    key: str; title: str; description: str; category: str
    tags: list[str] = []
    fields: list[CronBlueprintField] = []
    schedule: str; schedule_human: str; command: str; app_url: str

class CronBlueprintInstantiate(BaseModel):
    blueprint: str
    agent_id: str
    values: dict[str, Any] = {}
    deliver_targets: list[CronDeliveryTargetCreate] = []
```

Also add `CronBlueprintList`, `CronDeliveryTargetList`,
`CronDeliveryTargetOptions`, `CronJobRun`, `CronJobRunList`, `CronJobRunSummary`,
and extend the `_schedule_job` response shape with `delivery_targets`
(a `CronJobSummary` model or add the field to the existing dict). Keep
`Literal["channel","email","kanban","file"]` closed so an unknown type is a 422
at the boundary. Export the new names via `brain4all/models/__init__.py` so
`routes/setup.py` can import them.

## Phase 2 — Integration adapter

File: **new** `brain4all/integrations/cron_delivery.py`. Policy-free, no HTTP,
imports Hermes in-process (guard imports so a missing module raises a typed
adapter error caught by the readiness guard). Provide:

- `list_blueprints() -> list[dict]`: for each `b in CATALOG`,
  `entry = blueprint_catalog_entry(b)`; rewrite the `deliver` field's `options`
  from `available_channel_email_targets()` (mirrors Hermes route ≈ 12402–12409);
  return entries. Rename `scheduleHuman`→`schedule_human`, `appUrl`→`app_url` at
  this boundary so React sees snake_case.
- `fill(blueprint_key, values) -> dict`: `b = get_blueprint(key)`; if None raise
  `CronBlueprintNotFound`; `try: return fill_blueprint(b, values)` /
  `except BlueprintFillError as e: raise CronBlueprintInvalid(str(e))`.
- `available_channel_email_targets() -> list[dict]`: start with the implicit
  `local`; extend with `cron.scheduler.cron_delivery_targets()`; tag each with
  `target_type` = `"email"` when `id == "email"` else `"channel"`.
- `deliver_channel(content, platform, chat_id, job_id, job_name) -> DeliveryResult`:
  build `DeliveryTarget` (`f"{platform}:{chat_id}"` or bare platform for home
  channel); construct a `DeliveryRouter` from the profile gateway config +
  adapters (Plan 005 supplies adapters); `await router.deliver(...)`. If gateway
  config/adapter is unavailable, return `DeliveryResult(status="degraded",
  reason="channel not connected")` — never raise.
- `deliver_email(content, address, job_id, job_name) -> DeliveryResult`: route
  via an `email` `DeliveryTarget` (explicit address or the configured home
  address). Independent of Plan 005.

Return a small `DeliveryResult` dataclass `{status: "delivered"|"failed"|
"degraded", reason: str|None}`. Never include the content or credentials in the
result or any log line.

Do **not** put kanban/file delivery here (they belong in the service, using
existing Brain4All integrations).

## Phase 3 — Service

File: `brain4all/services/platform.py` (the `PlatformService`/cron methods,
≈ 351–454). Construct the new adapter alongside the existing `self.kanban`.

Add methods:

- `list_blueprints() -> dict`: `return {"blueprints":
  self.cron_delivery.list_blueprints()}`.
- `instantiate_blueprint(body) -> dict`:
  1. validate `agent_id` via `self.agents.describe_agent(agent_id,
     include_memory=False)` (as `create_cron` does);
  2. `spec = self.cron_delivery.fill(body["blueprint"], body["values"])`;
  3. re-validate `spec["schedule"]` with the existing schedule parsing in
     `create_cron` (`_schedule_seconds`/ISO), reusing that code path — do not
     duplicate it: refactor `create_cron` to accept a pre-built
     `{name, prompt, schedule, interval, agent_id}` and call it from both;
  4. create the Kanban schedule template (existing `create_task(..., status=
     "scheduled", schedule={...})`);
  5. for each `t in body["deliver_targets"]`, call `add_delivery_target(
     job_id, t)`; also translate the blueprint's own `spec["deliver"]` into a
     target when it names a platform (channel/email) and the caller did not pass
     explicit targets;
  6. return `_schedule_job(template)` enriched with `delivery_targets`.
- `list_delivery_target_options() -> dict`: `channel`/`email` from
  `self.cron_delivery.available_channel_email_targets()` plus static
  `{"target_type":"kanban"}` (board picker) and `{"target_type":"file"}`.
- `list_job_targets(job_id) -> dict`: read attached targets from the schedule
  template metadata; annotate `available`/`degraded_reason` (channel availability
  from the options list).
- `add_delivery_target(job_id, body) -> dict`:
  - validate `target_type` (model already closed);
  - `channel`/`email`: destination is a platform id / address; mark
    `available=false` + reason if the platform is absent from options (Plan 005
    not connected) — still persist;
  - `kanban`: destination is a board slug; validate it exists via
    `self.kanban.get_board(slug)`;
  - `file`: destination is a relative workspace path; resolve beneath the
    profile workspace root using the **existing workspace path guard** (reject
    traversal/symlink); store the relative form only;
  - **snapshot before mutation**, then atomically append the target record to
    the schedule template metadata (same board SQLite, Plan 003 storage);
  - return the created `CronDeliveryTarget`.
- `remove_delivery_target(job_id, target_id) -> dict`: snapshot, remove, return
  `{"deleted": True}`.
- `trigger_job(job_id) -> dict`: reuse `run_cron` (schedule_action `run_now`);
  return `{"job": ..., "run": ...}`.
- `list_job_runs(job_id, limit) -> dict`: read the occurrence/run history for the
  template from the Kanban board (Plan 001/004 run history), shaped as
  `CronJobRun` with per-target `deliveries` status; never expose prompt/tool
  content.

Post-run delivery hook:

- `on_occurrence_complete(occurrence) -> None`: called from the existing Plan
  003 occurrence-finish path (the Kanban dispatcher already reports completion —
  wire this in; do **not** add a new loop). Steps:
  1. resolve the template + its attached targets;
  2. read the occurrence's safe output summary (the same sanitized field the
     board exposes — no raw tool output);
  3. for each target, compute `delivery_key = sha256(f"{occurrence_id}:{target_id}")`
     (modeled on `delivery_ledger.compute_obligation_id`); if already recorded
     delivered/failed for this occurrence, skip;
  4. route by `target_type`: `channel`→`cron_delivery.deliver_channel`;
     `email`→`cron_delivery.deliver_email`; `kanban`→`self.kanban.create_task(
     board, {title, description: summary})`; `file`→workspace write beneath the
     profile root via the files repo (snapshot + atomic write);
  5. record the `DeliveryResult` (delivered/failed/degraded + reason) on the
     occurrence metadata (atomic). Log only `{occurrence_id, target_id,
     target_type, status}` — never content/credentials/paths.

Idempotency: the delivery key makes a retried trigger or a restart-mid-delivery
a no-op for already-delivered targets. A `degraded` channel result is retried on
the next run (not marked terminal), so it self-heals when Plan 005 connects.

## Phase 4 — Handlers + routes

File: `brain4all/handlers/api.py` (operation map, add beside the cron block
≈ 97–99):

```python
"cron_blueprints": (s.list_blueprints, "cron blueprints retrieved", 200),
"cron_blueprint_instantiate": (lambda: s.instantiate_blueprint(body), "cron job created from blueprint", 201),
"cron_delivery_targets": (s.list_delivery_target_options, "delivery targets retrieved", 200),
"cron_job_targets_list": (lambda: s.list_job_targets(p["job_id"]), "job delivery targets retrieved", 200),
"cron_job_target_add": (lambda: s.add_delivery_target(p["job_id"], body), "delivery target added", 201),
"cron_job_target_remove": (lambda: s.remove_delivery_target(p["job_id"], p["target_id"]), "delivery target removed", 200),
"cron_trigger": (lambda: s.trigger_job(p["job_id"]), "cron job triggered", 200),
"cron_runs": (lambda: s.list_job_runs(p["job_id"], int(q.get("limit", 20))), "cron job runs retrieved", 200),
```

`run_cron`/`trigger_job` are async — match the existing `cron_run` await
pattern.

File: `brain4all/routes/setup.py` — import the new models and add to the
**existing `Cron` group** (after ≈ line 89), keeping `tags=("Cron",)`:

```python
Route("GET",    "/agent-gateway/v1/cron/blueprints", "cron_blueprints", tags=("Cron",)),
Route("POST",   "/agent-gateway/v1/cron/blueprints/instantiate", "cron_blueprint_instantiate", CronBlueprintInstantiate, tags=("Cron",)),
Route("GET",    "/agent-gateway/v1/cron/delivery-targets", "cron_delivery_targets", tags=("Cron",)),
Route("GET",    "/agent-gateway/v1/cron/jobs/{job_id}/delivery-targets", "cron_job_targets_list", tags=("Cron",)),
Route("POST",   "/agent-gateway/v1/cron/jobs/{job_id}/delivery-targets", "cron_job_target_add", CronDeliveryTargetCreate, tags=("Cron",)),
Route("DELETE", "/agent-gateway/v1/cron/jobs/{job_id}/delivery-targets/{target_id}", "cron_job_target_remove", tags=("Cron",)),
Route("POST",   "/agent-gateway/v1/cron/jobs/{job_id}/trigger", "cron_trigger", tags=("Cron",)),
Route("GET",    "/agent-gateway/v1/cron/jobs/{job_id}/runs", "cron_runs", tags=("Cron",)),
```

Update `from ..models import (...)` (≈ line 14–24) to include the new model
names. Do not register routes anywhere else.

## Phase 5 — Frontend

Files under `src/src/`:

- `api/crons.ts`: add `listBlueprints()`, `instantiateBlueprint(input)`,
  `listDeliveryTargetOptions()`, `listJobTargets(jobId)`,
  `addJobTarget(jobId, target)`, `removeJobTarget(jobId, targetId)`,
  `trigger(jobId)`, `listRuns(jobId, limit)`; add DTO→domain mappers.
- `types.ts`: add `CronBlueprint`, `CronBlueprintField`, `CronDeliveryTarget`,
  `CronDeliveryTargetType = 'channel'|'email'|'kanban'|'file'`, `CronJobRun`.
- Hooks: extend `hooks/useCrons.ts` or add `hooks/useBlueprints.ts` /
  `hooks/useDeliveryTargets.ts` (React Query patterns already in the repo).
- Feature view (Settings > Automations): a **blueprint gallery** (cards grouped
  by category, "Use" opens a form generated from `fields[]` + agent picker +
  optional "Deliver to"), and on the cron job editor a **"Deliver to" selector**
  (list/add/remove targets; channel/email options disabled with "connect a
  channel first" when `available=false`; kanban→board picker; file→path input),
  a **Run now** button (`trigger`), and a **runs panel** (`listRuns` with
  per-target delivery status).
- `locales/*.json`: add all new strings to every shipped locale (en, vi, ja, zh,
  de, es, fr).

Run `cd src && npm run build` for type/build verification.

## Phase 6 — Tests

Add `brain4all/tests/test_cron_delivery.py` (temp `HERMES_HOME`, real Hermes,
same harness as `test_kanban.py`). See [validation.md](validation.md) for the
full matrix. Cover at minimum: blueprint list/instantiate; target-type
validation (reject unknown; channel degraded when no gateway); add/remove
targets persisted on the template; trigger produces a run record; post-run
delivery routes to email/kanban/file exactly once (dedup key); channel degrades
without raising; no secret/path/output leakage in responses or logs; file target
rejects traversal. Add frontend component/hook tests for the gallery, selector,
and runs panel.

Then run `make check` and `make smoke-api`.
