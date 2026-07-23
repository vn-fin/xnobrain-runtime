# 001 — Hermes Kanban foundation

Priority: P0. Do not start plan 002 or 003 until the compatibility gates and
core API in this plan pass.

## Goal

Expose Hermes Kanban through a stable, tested Brain4All service and HTTP
contract while keeping Hermes SQLite and its state machine authoritative. Run
the supported dispatcher inside the existing application lifecycle.

## Non-goals

- No new database or file representation for Kanban.
- No copied Hermes schema, SQL, dashboard plugin, or gateway watcher.
- No custom workflow statuses.
- No frontend redesign in this plan beyond replacing assumptions needed for API
  contract tests.
- No separate API service or dispatcher daemon.

## Phase 0 — Pin and prove upstream contracts

1. Replace the moving `HERMES_BRANCH=main` build with an immutable, documented
   Hermes release or commit. Prefer a tagged release when it contains all
   required public APIs; otherwise record the tested commit and upgrade
   procedure.
2. Add a compatibility module/test that imports the pinned package and checks:
   board initialization and selection, valid status vocabulary, public task
   CRUD operations, links/comments/attachments, assignment, claim/dispatch,
   complete/block/unblock/archive, run inspection, and cron service access.
3. Confirm a public, lifecycle-safe Kanban dispatcher component. If upstream
   only exposes a private gateway mixin, first contribute/extract a small public
   `KanbanDispatcher` (or equivalent) and consume it. A temporary private
   adapter is allowed only behind the pin and compatibility test, with a
   removal issue and no copied loop.
4. Confirm a public operation for “request review”. If absent, add it upstream
   with transition tests before exposing the Review move.
5. Fail startup readiness with one concise remediation message when the Hermes
   contract is incompatible. Do not limp along with partially working
   mutations.
6. Implement any necessary upstream changes in the current `hermes_cli`
   package and add/extend its current CLI command surface where a user-facing
   CLI operation is required. Do not patch a legacy CLI or duplicate commands
   in Brain4All.

Completion evidence: a focused test imports the same Hermes artifact used in
the image and exercises a task from creation through dispatch, review,
completion, and archive in a temporary home.

## Backend layers

Follow existing Brain4All boundaries:

- `brain4all/integrations/kanban.py` adapts the public `hermes_cli.kanban_db`
  and dispatcher APIs. It owns no policy and does no HTTP.
- `brain4all/services/kanban.py` owns the five-state projection, legal user
  intents, default-board rules, filtering, and safe response shaping.
- `brain4all/models/kanban.py` contains strict Pydantic request/response models.
- `brain4all/handlers/kanban.py` translates service errors to HTTP and the
  existing response envelope.
- `brain4all/routes/setup.py` is the only route registration point.
- The existing application lifespan starts/stops the embedded dispatcher and
  closes it cleanly on shutdown.

Names may be adjusted to match nearby modules if inspection during
implementation reveals a better existing convention. Do not mix these
responsibilities into `platform.py`.

### Integration rules

- Initialize/select boards with upstream helpers; never construct a database
  path from an unchecked user slug.
- Use upstream transaction, idempotency, attachment, and task-transition
  functions.
- Serialize domain records to Brain4All models at the adapter boundary so
  upstream implementation details do not leak to React.
- Never expose attachment `stored_path`, raw worker environment, credentials,
  prompts, tool arguments/output, or unredacted logs.
- Convert known conflicts and invalid transitions to stable errors; log only
  structured identifiers and error categories.
- Keep the default board stable and discoverable. A first launch creates it
  through Hermes if it does not exist.

## Dispatcher lifecycle

The dispatcher must run in the same FastAPI/Hermes process:

1. Acquire the upstream singleton/leader protection before dispatching.
2. Sweep active boards using upstream configuration and recovery behavior.
3. Use upstream `dispatch_once`/claim semantics so concurrent requests cannot
   double-start a task.
4. Observe upstream concurrency, model/provider, profile, worktree, dependency,
   auto-decomposition, and heartbeat rules.
5. Stop accepting work, cancel/watch child tasks safely, and release locks
   during application shutdown.
6. Surface dispatcher health and an actionable degraded reason through the
   existing health/diagnostics model, without blocking unrelated local OSS
   features.

Tests must cover two attempted dispatcher instances, restart after abrupt
worker loss, no eligible work, dependency release, and a corrupt board database
using upstream recovery behavior.

## Five-state service projection

Implement the mapping in the research plan in one shared backend function and
mirror it with a generated or exhaustively tested frontend type:

- Backlog: `triage`
- Todo: `todo`, `ready`, `scheduled`
- In Progress: `running`
- Review: `review`, `blocked`
- Done: `done`
- Archive filter: `archived`

Every response includes both:

- `status`: the five-state product value; and
- `state_detail`: a safe discriminator and reason needed to understand
  scheduling, readiness, dependencies, blocking, or active work.

Only the five product states are accepted from the UI. The service resolves a
requested move to legal Hermes operations according to the transition matrix in
`API_CONTRACT.md`.

## Core feature slices

Implement and test vertical slices rather than all reads followed by all writes:

1. List/create/select a board; list/get/create/edit a task.
2. Assign/reassign and move through legal task lifecycle operations.
3. Archive/unarchive and include-archived filtering.
4. Add/list comments and dependency links; validate cycles/conflicts upstream.
5. Upload/list/download/remove attachments with size, name, and path checks.
6. Inspect active worker and completed run summaries.
7. Filter/search/sort/page by board, product state, assignee, priority, tag,
   automation source, and archived state.
8. Stream invalidation/activity events so the client can update after workers
   mutate tasks. Prefer the repository’s existing SSE patterns; bridge upstream
   events without exposing a second auth protocol.

Comments and activity are different: user comments are persisted upstream;
activity is derived from durable task/run events and should not duplicate task
state in Brain4All.

## Tests

Use a temporary `HERMES_HOME` and the real pinned Hermes package:

- first-run default board and empty-list behavior;
- create idempotency and duplicate client request;
- complete lifecycle through all five projected states;
- illegal and stale transitions;
- assign/reassign/profile-not-found;
- dependency blocking and unblocking;
- comments and attachment traversal/size/name rejection;
- archive filtering and safe unarchive target;
- list pagination/filter/sort determinism;
- concurrent claim/dispatch and restart recovery;
- safe redaction in responses, logs, and error text;
- events after API and worker changes;
- native Hermes CLI/tool mutation is visible through the Brain4All API.

Use handler tests for status/envelope behavior and integration tests for the
real SQLite lifecycle. Mock only external model execution; do not mock the
Kanban database in contract tests.

## Acceptance criteria

- All routes in `API_CONTRACT.md` have success, validation, conflict, and
  not-found coverage.
- Creating a task through Brain4All makes it visible to the Hermes Kanban CLI,
  and a task created by the CLI becomes visible through Brain4All without
  import or synchronization.
- An eligible assigned task is claimed once and run by the embedded dispatcher.
- The database survives application restart without conversion or duplication.
- The API never returns more than the five product statuses.
- No direct task status SQL or separate Kanban persistence exists.
- Focused tests and `make check` pass.

## Documentation deliverables

Update `docs/architecture.md`, `docs/api.md`, `docs/development.md`, and the
relevant README sections with the pinned Hermes version, data ownership,
dispatcher lifecycle, routes, and local troubleshooting. Continue generating
OpenAPI at runtime; do not add `docs/openapi.yaml`.
