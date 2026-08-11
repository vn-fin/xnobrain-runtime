# Kanban research and architecture decisions

Status: planning baseline, 2026-07-23.

This document records the evidence and decisions the implementation plans rely
on. Recheck them against the pinned Hermes revision before implementation.

## Desired outcome

Brain4All will expose one approachable task system for human work, agent work,
and scheduled work:

- users see exactly **Backlog**, **Todo**, **Running**, **Done**, and
  **Archived** in board and list views;
- Hermes owns task persistence, worker dispatch, dependencies, run history, and
  cron timing;
- Brain4All supplies a stable product API, the five-state presentation, and an
  accessible UI;
- schedules created in Settings or by prompting an agent create an automation
  card on the default board, and each firing becomes an auditable Kanban task;
- there is no separate Brain4All cron daemon, scheduler loop, database, or
  standalone cron product.

This remains a single-user, local-first OSS product. Multi-board support is
useful organization within that product; it is not a multi-user control plane.
All Hermes integration work targets the current `hermes_cli` package and its
documented extension edges. It must not revive or modify a legacy CLI.

## What exists locally

### Frontend

`src/api/kanban.ts` is an in-memory adapter with seeded sample boards and
tasks. `src/hooks/useKanban.ts` and `src/components/KanbanView.tsx`
provide a substantial visual prototype, but production behavior is not
persistent and the model allows user-defined statuses. The existing tests
exercise this mock rather than a backend contract.

The sidebar currently exposes Skills, Runtime, Kanban, Teams, Connections,
Settings, and Data. Cron is a separate right-panel tab with demo behavior. This
does not match the requested information architecture.

### Backend and runtime

Brain4All already extends `hermes_cli.web_server.app` and registers routes
through `brain4all/routes/setup.py`. This is the correct host for a thin
Kanban integration.

`brain4all/app.py`, `brain4all/services/platform.py`, and
`brain4all/repositories/files.py` currently implement a separate Brain4All
cron loop and YAML job storage. At the same time, the Hermes web server already
starts its native desktop cron ticker when `HERMES_DESKTOP=1`. Keeping both
would create competing schedulers and sources of truth.

`Dockerfile.backend` installs the moving Hermes `main` branch. Kanban depends on
internal contracts that can change, so an immutable tested Hermes revision is a
prerequisite.

## Hermes capabilities reviewed

The referenced documentation describes a shared SQLite Kanban database used by
the CLI, agent tools, and gateway dispatcher. It covers multiple boards, task
assignment, dependencies, comments, attachments, workspaces, dispatch,
heartbeats, completion/blocking, runs, diagnostics, and worker lanes.

The current upstream implementation reviewed for this plan was
`NousResearch/hermes-agent` commit
`d9165d7a678d4105f42921a7fc1886df3804531b`. It also exposes a large dashboard
plugin API for boards, tasks, profiles, workers, runs, diagnostics, bulk
actions, decomposition, attachments, subscriptions, and live events.

Current upstream internal states are:

`triage`, `todo`, `scheduled`, `ready`, `running`, `blocked`, `review`, `done`,
and `archived`.

The dispatcher belongs in the messaging gateway in a normal Hermes deployment.
Brain4All hosts the Hermes web app directly, so the implementation must embed
the supported dispatcher component in the existing FastAPI lifespan. Starting
the deprecated standalone daemon would violate the project architecture.

Hermes cron supports delay, interval, cron-expression, and ISO schedules,
profiles, skills, delivery, working directories, scripts, pause/resume/edit,
manual triggering, and natural-language creation through the `cronjob` tool.
The current scheduler normally creates a fresh agent run, which is the seam that
must change for Kanban-native automation.

## Fixed five-state projection

Hermes execution substates must not be discarded because they determine whether
a worker can claim a task. They are projected into five user-facing states:

| User state | Hermes state | User-facing detail |
| --- | --- | --- |
| Backlog | `triage` | Needs clarification or prioritization |
| Todo | `todo`, `scheduled` | Waiting for work or a dependency |
| Running | `ready`, `running` | Eligible for a worker or actively running |
| Done | `blocked`, `review`, `done` | Completed or stopped for attention |
| Archived | `archived` | Retained history |

Archived is always visible as the fifth column. Moving a task there requires
confirmation because Hermes closes any active run while archiving it.

Cards show a small detail badge such as “Ready”, “Scheduled”, “Needs input”, or
“Worker stopped” when that distinction is actionable. The board never adds a
column for it.

### Transition rule

The UI expresses intent; the integration invokes a legal Hermes operation.
Examples:

- dropping a card into Running assigns it if necessary, makes it eligible,
  and nudges dispatch; it must not forge `running`;
- dropping into Done invokes completion and requires the same completion data
  as the worker tool;
- moving a blocked task to Todo invokes unblock and respects dependencies;
- archiving invokes the public archive operation after user confirmation.

Direct SQL status writes are forbidden. Phase 001 must first confirm public
dispatcher operations or contribute them upstream.

## Sources of truth and boundaries

- Hermes Kanban SQLite is authoritative for boards, tasks, links, comments,
  attachments, assignments, worker state, and runs.
- Hermes cron persistence and ticker are authoritative for schedule timing.
- Brain4All owns Pydantic HTTP translation, the five-state projection, settings,
  UX, and migration from its legacy YAML jobs.
- Local layers call the Hermes Python package directly; they do not call the
  application’s own HTTP endpoints.
- There is one FastAPI/Hermes process and one 9router process. Worker child
  processes started by Hermes are expected and are not extra application
  services.

Do not create a parallel Kanban repository, copy Hermes schemas, fork the
dispatcher, or redefine cron timing in a custom provider.

## Cron-to-Kanban decision gate

The preferred upstream-compatible extension is a cron execution target such as
`kanban`: when due, the native cron runner creates an occurrence task through
the public Kanban API rather than creating a direct agent session. This keeps
all creation surfaces—Settings, CLI, and the `cronjob` tool—consistent.

If that small upstream extension cannot be accepted or consumed, the bounded
fallback is an upstream no-agent cron job that runs a generated, versioned
helper script which calls the public Kanban API. It is acceptable only if an
integration test proves deterministic occurrence idempotency, safe upgrades,
and coverage for both Settings and prompt creation.

The following are rejected:

- another polling or reconciliation loop;
- a second cron database;
- using a scheduler provider to redefine job execution contrary to its
  scheduling-only contract;
- silently observing only UI-created jobs while prompt/CLI jobs behave
  differently;
- invoking an invisible agent run and adding a Kanban card afterward.

## Information architecture

The primary navigation contains:

1. Agents
2. Kanban

Settings remains a persistent utility entry, not a third work area. Existing
Skills, Runtime, Connections, Teams, Data, workspace/memory controls, agent
details, and automation defaults move into clearly named Settings sections.
Agent conversations remain nested in Agents.

Kanban opens the default board, provides board and list view toggles, and uses
progressive disclosure: title, assignee, and optional schedule are enough to
create; dependencies, skills, workspace, model, goal, and orchestration belong
in the task drawer’s advanced area.

## Feature priority

- P0: pinned upstream contract, persistence adapter, API, state projection,
  lifecycle-safe dispatcher, core task operations.
- P1: production board/list UX, simple navigation/settings, comments,
  dependencies, attachments, assignment, archive, events, accessibility.
- P1: default-board automation from Settings and prompts, occurrence history,
  migration, removal of the duplicate scheduler.
- P2: multi-board management, worker diagnostics/recovery, advanced execution
  configuration, bulk actions, statistics, and decomposition.
- P3: optional advanced orchestration only where the pinned Hermes public API is
  stable and the experience remains understandable.

## Reference documents

- [Hermes Kanban](https://hermes-agent.nousresearch.com/docs/user-guide/features/kanban)
- [Kanban tutorial](https://hermes-agent.nousresearch.com/docs/user-guide/features/kanban-tutorial)
- [Kanban worker lanes](https://hermes-agent.nousresearch.com/docs/user-guide/features/kanban-worker-lanes)
- [Hermes cron](https://hermes-agent.nousresearch.com/docs/user-guide/features/cron)
- [Contributing](https://hermes-agent.nousresearch.com/docs/developer-guide/contributing)
- [Extending the CLI](https://hermes-agent.nousresearch.com/docs/developer-guide/extending-the-cli)
- [Hermes architecture](https://hermes-agent.nousresearch.com/docs/developer-guide/architecture)
