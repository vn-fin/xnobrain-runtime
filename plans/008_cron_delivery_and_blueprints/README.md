# 008 — Cron delivery targets & blueprints

Priority: P2. Depends on the existing Kanban-backed cron (Plan 003) being live
and on Plan 005 (Messaging Channels) **only** for the `channel` delivery-target
type. Email, Kanban, and workspace-file targets ship without Plan 005.

Companion documents (read together):

- [findings.md](findings.md) — what Hermes and Brain4All already provide, the
  exact gap, compatibility surface to pin, risks, open questions.
- [architecture.md](architecture.md) — layering, data flow, the new API
  contract, where config lives, the React surface, sequence diagram.
- [approaches.md](approaches.md) — options weighed and the chosen approach.
- [implementation.md](implementation.md) — ordered, file-by-file steps.
- [validation.md](validation.md) — how to prove it works, acceptance checklist.

## Goal

Extend Brain4All's **existing** cron so a scheduled job can (a) send its output
to a chosen **delivery target** — a messaging **channel**, **email**, a
**Kanban card**, or a **workspace file** — and (b) be created from a reusable,
parameterized **blueprint** (template) that a user instantiates into a concrete
job.

Brain4All already has cron CRUD (list/create/pause/resume/run/delete), and since
Plan 003 that cron is **Kanban-backed**: a cron job is a scheduled task on the
default board, ticked by the one in-process Kanban dispatcher. This plan adds
delivery targets and blueprints **on top of that** model. It does not introduce a
second scheduler, a second job store, or an app database.

## Non-goals

- No second scheduler, dispatcher loop, cron daemon, or job store. The Kanban
  dispatcher from Plan 001/003 stays the only tick. We do **not** wire up
  Hermes's native `cron/scheduler.py` `run_job` loop or its `/api/cron/fire`
  managed-Chronos webhook.
- No copied/forked blueprint engine or delivery router. We import the same
  Hermes modules the native routes use; we never re-implement them.
- No new messaging transport. The `channel` target reuses whatever Plan 005
  configures; this plan does not build channel connectivity.
- No app database, no ORM, no second API process, no Go/PostgreSQL.
- No mock/demo blueprints or fake delivery. Real Hermes catalog, real files.
- No credential or raw-output leakage in responses, logs, or traces.

## Priority & dependencies

| Capability | Depends on | Ships without Plan 005? |
| --- | --- | --- |
| Blueprints (list / instantiate) | Plan 003 cron | Yes |
| Delivery target: `email` | Hermes email gateway config (`EMAIL_HOME_ADDRESS`, SMTP) | Yes |
| Delivery target: `kanban` | Plan 001–004 Kanban (already shipped) | Yes |
| Delivery target: `file` | Brain4All workspace/files repo (already shipped) | Yes |
| Delivery target: `channel` | **Plan 005 Messaging Channels** (for a connected platform + adapter) | No — degrades cleanly |

Design rule: `email`, `kanban`, and `file` targets are fully functional now.
`channel` targets validate and persist now, but delivery is **degraded with an
explicit reason** ("no connected channel — see Messaging Channels") until Plan
005 lands a configured, connected platform. Never silently drop a channel
delivery.

## Scope

In scope:

1. A Brain4All integration adapter over Hermes's blueprint catalog
   (`cron.blueprint_catalog`) and delivery-target discovery
   (`cron.scheduler.cron_delivery_targets`), plus a post-run delivery step.
2. Versioned Brain4All routes for: list blueprints, instantiate a blueprint,
   list available delivery targets, list/add/remove targets attached to a job,
   trigger a job now, list a job's runs.
3. Strict Pydantic models with explicit target-type validation
   (`channel | email | kanban | file`).
4. Post-run delivery: after an occurrence completes, route its safe output to
   every attached target, idempotently, recording a delivery result.
5. React surface: a blueprint gallery and a "Deliver to" selector on a cron job.
6. Compatibility test that pins the imported Hermes modules and shapes.

Out of scope: the native Chronos managed-cron path, new channel transports,
per-run streaming delivery, and blueprint authoring UI (catalog is upstream).

## Phase overview

- **Phase 0 — Pin & prove.** Pin the Hermes commit that exports
  `cron.blueprint_catalog`, `cron.scheduler.cron_delivery_targets`, and the
  delivery modules; add a compatibility test asserting their import and shapes.
  See [implementation.md](implementation.md#phase-0) and
  [findings.md](findings.md#compatibility-surface-to-pin).
- **Phase 1 — Models.** Add Pydantic request/response models in
  `brain4all/models/api.py`. Explicit `target_type` enum.
- **Phase 2 — Integration.** Add `brain4all/integrations/cron_delivery.py`
  adapting the Hermes blueprint catalog + delivery-target discovery + the
  post-run delivery routing (reusing `gateway.delivery` for channel/email).
- **Phase 3 — Service.** Extend the cron service in
  `brain4all/services/platform.py` with blueprint and delivery-target
  operations, and hook post-run delivery into occurrence completion.
- **Phase 4 — Handlers + routes.** Add operations in
  `brain4all/handlers/api.py` and `Route(...)` lines in the existing `Cron`
  group in `brain4all/routes/setup.py`.
- **Phase 5 — Frontend.** Blueprint gallery + "Deliver to" selector under
  Settings > Automations; extend `src/api/crons.ts` / `useCrons.ts`.
- **Phase 6 — Tests + validation.** Unit, integration (temp `HERMES_HOME`),
  `make check`, `make smoke-api`, manual journey. See
  [validation.md](validation.md).

## Definition of done

- A user can browse blueprints from a real Hermes catalog, instantiate one, and
  see the resulting cron job on the default board.
- A user can attach one or more delivery targets (`email`, `kanban`, `file`,
  and `channel` when available) to a cron job, and remove them.
- Triggering a job (run now) produces a run record retrievable via the runs
  route, and each attached target receives the job's safe output exactly once
  (channel degrades with an explicit reason when Plan 005 is absent).
- Target-type validation rejects unknown types with a stable 4xx; a `channel`
  target for an unconnected platform is stored but reports a degraded reason.
- No credentials, prompts, tool args/output, or absolute stored paths appear in
  any response or log.
- No second scheduler/store/database is introduced; the Kanban dispatcher stays
  the only tick.
- Compatibility test, focused backend tests, `make check`, and the manual
  journey in [validation.md](validation.md) pass.
