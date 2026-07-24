# 008 — Approaches

Two design axes, each with options and trade-offs, then the chosen approach.

Cross-links: [README.md](README.md) · [findings.md](findings.md) ·
[architecture.md](architecture.md) · [implementation.md](implementation.md) ·
[validation.md](validation.md).

## Axis A — Who executes delivery?

### A1. Ride Hermes's native delivery mechanism end-to-end

Use Hermes's native cron: create native jobs via `cron.jobs.create_job` with a
`deliver` string, and let Hermes's `cron/scheduler.py::run_job` +
`gateway.delivery.DeliveryRouter` run and deliver.

- Pros: zero delivery code; canonical routing, truncation, dead-target handling,
  and the durable delivery ledger for free.
- Cons: **introduces a second scheduler** (Hermes cron `run_job`) alongside the
  Kanban dispatcher — a direct violation of Plan 003's "one tick" rule and the
  MANDATORY constraint against a copied/second dispatch loop. Native `deliver`
  also only supports messaging platforms + `local`; **no `kanban` or workspace-
  `file` target exists**. And native jobs are profile-scoped, not tied to a
  Kanban assignee/board, so they would live outside the Plan 003 model.

### A2. Brain4All post-run delivery on the Kanban-backed job

Keep the Kanban dispatcher as the only scheduler. After an occurrence completes,
a Brain4All service step reads the safe output and routes it to each attached
target, reusing Hermes's `DeliveryRouter` for channel/email and Brain4All's own
integrations for kanban/file.

- Pros: one scheduler; consistent with Plan 003; supports all four target types
  including kanban/file that Hermes can't natively deliver; targets attach to the
  existing Kanban schedule template.
- Cons: Brain4All owns the delivery orchestration (dedup key, result recording),
  duplicating a slice of what the gateway ledger does for messaging.

### A3. Hybrid — native routing for messaging, Brain4All for the rest

Post-run step (A2) for orchestration and dedup, but for channel/email hand the
content to Hermes's `DeliveryRouter` (A1's routing) rather than re-implementing
transport; do kanban/file in Brain4All.

- Pros: no second scheduler; reuse canonical messaging transport/truncation;
  cover all four target types; no forked routing code.
- Cons: two delivery code paths (router-backed vs Brain4All-backed) to test.

## Axis B — Where do blueprints come from?

### B1. Ride Hermes blueprints (`cron.blueprint_catalog`)

Import `CATALOG`, `blueprint_catalog_entry`, `fill_blueprint`. List and validate
upstream; instantiate the produced spec.

- Pros: single source of truth; upstream validation (unknown-slot, strict-enum,
  required); new blueprints ship with Hermes; matches the confirmed anchors.
- Cons: catalog content is upstream-controlled; a blueprint's default `deliver`
  vocabulary is messaging-oriented and must be mapped onto our target model.

### B2. Brain4All-authored template store

Define our own blueprint templates as files under `DATA_DIR`.

- Pros: full control of catalog and slot semantics; can bake kanban/file targets
  into a template directly.
- Cons: forks upstream intent; new persistence to build and secure; diverges
  from the confirmed Hermes anchors; duplicate validation logic; more to
  maintain. Violates "never copy/fork Hermes; ride native APIs."

### B3. Ride Hermes catalog, but instantiate into a Kanban schedule (not a native job)

Use `fill_blueprint` to get the generic `{prompt, schedule, name, deliver,
skills}` spec, then feed it into Brain4All's existing Kanban schedule create
(`create_cron`/`create_task`) instead of `cron.jobs.create_job`.

- Pros: upstream catalog + validation, but the instantiated job lives in the
  Plan 003 Kanban model with an assignee and a default-board template — no second
  scheduler, no native job store.
- Cons: the blueprint's `deliver` value must be translated into an attached
  Brain4All delivery target rather than a native `deliver` string.

## Chosen approach

**A3 (hybrid post-run delivery) + B3 (ride the Hermes catalog, instantiate into
a Kanban schedule).**

Rationale:

1. **One scheduler, always.** B3 and A2/A3 keep the Kanban dispatcher (Plan
   001/003) as the only tick and the only job store. We never start Hermes's
   `run_job` loop or its Chronos `fire_due` path. This is the hard constraint the
   Kanban plans enforce, and it is non-negotiable here.
2. **Ride, don't fork.** B3 reuses `cron.blueprint_catalog` (catalog + slot
   validation) and A3 reuses `gateway.delivery.DeliveryRouter` for messaging
   transport. We add no forked catalog, no copied router, no duplicated dispatch
   loop. This satisfies "ride Hermes native APIs; never copy/fork."
3. **All four target types work.** Native delivery covers only messaging +
   `local`. A3 lets us add `kanban` (via the existing `services/kanban.py`) and
   `file` (via the workspace/files repo) as Brain4All-owned deliveries, which the
   task explicitly requires.
4. **Plan 005 decoupling.** `email`, `kanban`, and `file` deliver today; a
   `channel` target degrades cleanly until Plan 005 connects a platform, because
   channel routing is the only path that needs live gateway adapters. Storing
   only a platform id means no migration when Plan 005 lands.
5. **Safety and idempotency stay ours.** A Brain4All per-(occurrence, target)
   delivery key (modeled on the gateway ledger's `compute_obligation_id`)
   guarantees at-most-once user-visible delivery across retries/restarts without
   adopting the gateway-scoped ledger wholesale.

Cost accepted: two delivery code paths (router-backed messaging vs Brain4All
kanban/file) and Brain4All-owned orchestration. Both are small, explicitly
tested (see [validation.md](validation.md)), and far cheaper than the constraint
violations of A1/B2.

Rejected: A1 (second scheduler), B2 (forked catalog + new store). B1 alone is
insufficient because it instantiates native jobs; B3 is B1 adapted to the Kanban
model.
