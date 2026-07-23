# Kanban implementation checklist

This is the execution index for the Brain4All Kanban program. Implement the
plans in numeric order. An item is complete only when its acceptance checks
pass; changing a checkbox without the corresponding evidence is not complete.

Legend:

- `[ ]` not started
- `[~]` in progress
- `[x]` verified
- `[!]` blocked, with the blocker recorded beside the item

## Plan documents

- [Research and decisions](000_kanban_research/README.md)
- [001 — Hermes foundation and Brain4All API](001_kanban_foundation/README.md)
- [API contract](001_kanban_foundation/API_CONTRACT.md)
- [002 — Accessible Kanban and list experience](002_kanban_experience/README.md)
- [003 — Kanban-native automation](003_kanban_automation/README.md)
- [004 — Full Hermes Kanban parity](004_kanban_parity/README.md)
- [End-to-end verification](VERIFICATION.md)

## 000 — Decisions and compatibility gates

- [ ] Pin the Hermes dependency to an immutable, tested release or commit
  instead of building from a moving `main`.
- [ ] Add an automated compatibility test for the Hermes Kanban database,
  dispatcher, statuses, cron service, and required public operations.
- [ ] Confirm or upstream a public dispatcher integration point that can run in
  the existing FastAPI process; do not copy the gateway dispatch loop.
- [ ] Make any required Hermes changes in the current `hermes_cli` extension
  surface; do not revive or patch a legacy CLI implementation.
- [ ] Confirm or upstream a legal public operation for requesting review; do
  not write a task status directly in SQLite.
- [ ] Prove the cron-to-Kanban execution bridge described in plan 003 before
  removing the legacy Brain4All scheduler.
- [ ] Record the accepted Hermes version and compatibility decisions in
  `docs/development.md` and `docs/architecture.md`.

## 001 — Foundation

- [ ] Add a thin `brain4all` integration adapter over Hermes Kanban APIs.
- [ ] Keep Hermes SQLite as the only source of truth for Kanban state.
- [ ] Start and stop the Hermes Kanban dispatcher from the existing FastAPI
  lifespan, with singleton and restart behavior covered by tests.
- [ ] Add Pydantic request/response models for boards, tasks, comments, links,
  assignments, transitions, attachments, runs, and events.
- [ ] Add the versioned Brain4All Kanban routes through
  `brain4all/routes/setup.py`.
- [ ] Implement the fixed five-state presentation mapping:
  Backlog, Todo, In Progress, Review, and Done.
- [ ] Preserve execution substates as badges/reasons without exposing more
  draggable workflow columns.
- [ ] Implement legal task creation, editing, assignment, reassignment,
  transition, completion, blocking/unblocking, and archival operations.
- [ ] Implement task comments, dependency links, attachments, and safe
  attachment download.
- [ ] Implement board/task filters, pagination, and an initial event stream.
- [ ] Return actionable compatibility/service errors without leaking prompts,
  tool arguments, output, credentials, or absolute stored paths.
- [ ] Add temporary-`HERMES_HOME` backend tests using the real Hermes package.
- [ ] Verify restart persistence and claim/idempotency behavior.

## 002 — Product experience

- [ ] Replace production mock Kanban data with the versioned API client.
- [ ] Remove custom status creation and render exactly five workflow states.
- [ ] Build the board view with accessible drag/drop and a non-drag move
  control.
- [ ] Build the list view over the same filters, mutations, and state model.
- [ ] Implement fast task creation and an advanced task editor.
- [ ] Implement clear assignment, dependencies, comments, attachments, run
  history, worker state, and archive experiences.
- [ ] Add loading, empty, partial-failure, offline/retry, and permission/error
  states.
- [ ] Make the left navigation contain only Agents and Kanban, with Settings as
  a utility destination.
- [ ] Move Skills, Runtime, Connections, Teams, Data, workspace, memory, agent
  detail, and automation defaults into organized Settings sections.
- [ ] Preserve deep links and compatible redirects from existing routes.
- [ ] Add responsive list fallback, keyboard operation, focus management,
  screen-reader announcements, reduced-motion support, and contrast checks.
- [ ] Translate all new strings in every locale currently shipped by the app.
- [ ] Add component, hook, routing, accessibility, and build tests.

## 003 — Kanban-native automation

- [ ] Use the upstream Hermes cron scheduler/ticker; do not add another polling
  loop, daemon, API process, or scheduler database.
- [ ] Add the approved cron execution target that enqueues a Kanban occurrence
  instead of starting an invisible cron agent run.
- [ ] Create every new automation template on the default Kanban board.
- [ ] Make automation creation from Settings and from an agent prompt converge
  on the same service and persisted Hermes cron definition.
- [ ] Create recurring occurrence tasks with a deterministic idempotency key,
  source linkage, schedule metadata, and copied execution configuration.
- [ ] Implement pause, resume, edit schedule, run now, archive, and delete
  semantics without deleting task history.
- [ ] Show schedules as plain language with timezone, next run, and advanced
  cron-expression editing.
- [ ] Migrate existing Brain4All YAML jobs once, with a snapshot, idempotency,
  a dry-run report, and safe conflict handling.
- [ ] Remove the Brain4All `scheduler_loop` and retire or adapt the legacy cron
  endpoints only after migration and compatibility tests pass.
- [ ] Verify settings-created and prompt-created schedules in the browser and
  through the API, including pause/restart/no-duplicate behavior.

## 004 — Hermes feature parity

- [ ] Board create/list/switch/rename/archive/delete with a protected default
  board.
- [ ] Search, saved filters, bulk assign/move/archive, and useful board stats.
- [ ] Skills, workspace modes, priority, tags, goal mode, model/provider
  overrides, and profile descriptions.
- [ ] Worker lanes, active-worker view, heartbeat, log tail, run history,
  terminate, reclaim, and recovery diagnostics.
- [ ] Dependency graph, blocking reasons, review metadata, completion summaries,
  and auditable task activity.
- [ ] Manual and automatic specification/decomposition with clear previews.
- [ ] Notifications/subscriptions where supported by the pinned Hermes version.
- [ ] Advanced orchestration features only when they have a stable upstream
  API and a comprehensible non-developer experience.
- [ ] Update public documentation and supersede
  `docs/implementation/02-local-cron.md`.

## Release gate

- [ ] All focused backend and frontend tests pass.
- [ ] `make check` passes.
- [ ] `make run`, `make smoke-api`, and container health checks pass.
- [ ] The browser journeys in `VERIFICATION.md` pass against a real stack.
- [ ] Refresh and container restart preserve tasks, boards, schedules, and
  occurrence history without duplication.
- [ ] Production contains no seeded demo Kanban records.
- [ ] No regression to chat streaming, stop behavior, approvals, telemetry
  propagation, profile isolation, or 9router behavior.
- [ ] Architecture, API, development, user, migration, and troubleshooting
  documentation match the shipped behavior.
