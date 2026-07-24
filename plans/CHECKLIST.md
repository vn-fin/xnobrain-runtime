# Kanban implementation checklist

This is the execution index for the Brain4All Kanban program. Implement the
plans in numeric order. An item is complete only when its acceptance checks
pass; changing a checkbox without the corresponding evidence is not complete.

Legend:

- `[ ]` not started
- `[~]` in progress
- `[x]` verified
- `[!]` blocked, with the blocker recorded beside the item

Implementation snapshot (2026-07-24): the foundation and real-data board/list
vertical slice are verified below. The pinned Hermes runtime currently has no
public cron execution-target hook, active-task edit operation, or unarchive
operation; those items remain open rather than using private SQL or a second
scheduler. Production Kanban contains no mock, demo, or smoke-hook records.

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
- [x] Keep review/blocked as native Hermes detail mapped into Done; do not add
  a product Review status or write task status directly in SQLite.
- [ ] Prove the cron-to-Kanban execution bridge described in plan 003 before
  removing the legacy Brain4All scheduler.
- [ ] Record the accepted Hermes version and compatibility decisions in
  `docs/development.md` and `docs/architecture.md`.

## 001 — Foundation

- [x] Add a thin `brain4all` integration adapter over Hermes Kanban APIs.
- [x] Keep Hermes SQLite as the only source of truth for Kanban state.
- [x] Start and stop the Hermes Kanban dispatcher from the existing FastAPI
  lifespan, with singleton and restart behavior covered by tests.
- [ ] Add Pydantic request/response models for boards, tasks, comments, links,
  assignments, transitions, attachments, runs, and events.
- [x] Add the versioned Brain4All Kanban routes through
  `brain4all/routes/setup.py`.
- [x] Implement the fixed five-state presentation mapping:
  Backlog, Todo, Running, Done, and Archived.
- [x] Return native `status` and five-column `kanban_status` separately, and
  preserve native execution states as distinct colored badges/reasons without
  exposing more draggable workflow columns.
- [x] Support changing the single native execution assignee in Backlog and
  before a task starts; do not present a misleading multi-assignee control.
- [~] Implement legal task creation, triage editing, assignment, reassignment,
  transition, completion, blocking/unblocking, and archival operations.
- [~] Implement task comments, dependency links, and safe event projection;
  attachment upload/download routes remain open.
- [x] Implement board/task filters, bounded pagination, and an initial event feed.
- [x] Stream safe, resumable board events to the Kanban activity panel and
  refresh canonical task data when events arrive.
- [ ] Return actionable compatibility/service errors without leaking prompts,
  tool arguments, output, credentials, or absolute stored paths.
- [x] Add temporary-`HERMES_HOME` backend tests using the real Hermes package.
- [~] Verify restart persistence and claim/idempotency behavior (task
  idempotency is Hermes-backed; lifecycle restart coverage remains).

## 002 — Product experience

- [x] Replace production mock Kanban data with the versioned API client.
- [x] Remove seeded mock/demo and smoke-hook Kanban data; smoke and browser
  checks must create and verify real records in Hermes SQLite.
- [x] Remove custom status creation and render exactly five workflow states.
- [x] Build the board view with accessible drag/drop and a non-drag move
  control.
- [x] Build the list view over the same filters, mutations, and state model.
- [ ] Implement fast task creation and an advanced task editor.
- [ ] Implement clear assignment, dependencies, comments, attachments, run
  history, worker state, and archive experiences.
- [x] Add loading, empty, partial-failure, offline/retry, and permission/error
  states.
- [x] Notify on successful task moves/assignments and restore the prior board
  state with a clean notification when a transition conflicts.
- [x] Make the left navigation contain only Agents and Kanban, with Settings as
  a utility destination.
- [ ] Move Skills, Runtime, Connections, Teams, Data, workspace, memory, agent
  detail, and automation defaults into organized Settings sections.
- [ ] Preserve deep links and compatible redirects from existing routes.
- [ ] Add responsive list fallback, keyboard operation, focus management,
  screen-reader announcements, reduced-motion support, and contrast checks.
- [ ] Translate all new strings in every locale currently shipped by the app.
- [x] Add component, hook, routing, and build tests (accessibility audit remains).

## 003 — Kanban-native automation

- [~] Use the upstream Hermes cron scheduler/ticker; no second scheduler is
  started by Brain4All, but the current Hermes runtime exposes no Kanban
  execution target.
  loop, daemon, API process, or scheduler database.
- [!] Add the approved cron execution target that enqueues a Kanban occurrence
  instead of starting an invisible cron agent run (blocked: the pinned Hermes
  package exposes no public `execution_target=kanban` hook; implementing this
  safely requires an upstream Hermes extension or a newly pinned release).
- [x] Create every new automation template on the default Kanban board (the
  compatibility `/cron/jobs` facade links a visible template card).
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
- [x] Remove the Brain4All `scheduler_loop` from the FastAPI lifespan; legacy
  endpoints remain a compatibility facade and are not a started scheduler.
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

- [x] All focused backend and frontend tests pass.
- [x] `make check` passes.
- [ ] `make run`, `make smoke-api`, and container health checks pass.
- [x] The Kanban browser journey in `VERIFICATION.md` passes against a real
  stack; automation journeys remain open with the cron execution-target gate.
- [ ] Refresh and container restart preserve tasks, boards, schedules, and
  occurrence history without duplication.
- [x] Production contains no seeded demo Kanban records.
- [x] No smoke-test hook or fallback can inject synthetic Kanban records into
  the running application.
- [ ] No regression to chat streaming, stop behavior, approvals, telemetry
  propagation, profile isolation, or 9router behavior.
- [ ] Architecture, API, development, user, migration, and troubleshooting
  documentation match the shipped behavior.
