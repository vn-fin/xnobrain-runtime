# Kanban implementation verification

This is the release test plan for plans 001–004. Record the command, date,
tested Hermes revision, and result in the implementing change or release notes.
Do not replace real-stack checks with mocked screenshots.

## Automated checks

During implementation, run focused tests first:

```bash
python -m unittest <focused-backend-test-module>
cd src && npm test -- <focused-frontend-test>
```

Before each plan is handed off:

```bash
make check
```

The final frontend verification must include `cd src && npm run build`, whether
directly or through `make check`.

## Start the real application

From a clean test profile/data directory:

```bash
make run
docker compose ps
make smoke-api
```

Check runtime-generated OpenAPI and every new Kanban route. Open the actual UI
at `http://localhost:5152` in the available browser automation tool. Save
screenshots or traces only as test artifacts and ensure they contain no
credentials or private prompts.

Use the built images and their installed Hermes artifact—not a separate local
checkout—for final compatibility tests.

## API matrix

Verify success, validation, conflict, not-found, stale-write, and degraded
behavior as relevant:

- list/create/select/update/delete boards and protect the default board;
- list/filter/page/create/get/edit tasks;
- assign/reassign with valid, missing, and ineligible profiles;
- every allowed five-state transition and every rejected transition;
- comments, links/dependencies, cycle rejection;
- attachment upload/list/download/delete and traversal/oversize rejection;
- archive/unarchive/include-archived;
- worker/run summaries and bounded sanitized logs;
- dispatcher nudge, singleton behavior, and diagnostics;
- event connect, resume/reconnect, and full-refetch fallback;
- automation create/edit/pause/resume/run/delete/occurrences;
- native CLI/tool mutations appearing immediately in API reads.

Inspect response bodies and application logs to ensure task bodies, prompts,
tool data, secrets, authorization headers, stored paths, and worker environment
are absent.

## Browser journey A — First use and navigation

1. Start with an empty Hermes home.
2. Confirm the primary rail contains Agents and Kanban only, plus the Settings
   utility.
3. Confirm old deep links redirect to their new Settings sections.
4. Open Kanban and see a helpful empty default board with exactly five columns.
5. Switch to list view and back; refresh and confirm the preference/filter URL.
6. Visit every Settings destination moved from the old sidebar.

Check desktop, narrow laptop, and mobile widths; keyboard-only operation; light
and dark themes; reduced motion; and one non-English locale.

## Browser journey B — Human-managed task

1. Quick-create a Todo task with title only.
2. Edit description, priority, tags, assignee, and an attachment.
3. Add a comment and a dependency.
4. Move it with the keyboard “Move to…” control and separately test pointer
   drag on another task.
5. Trigger a stale conflict from a concurrent API/worker update and confirm the
   UI restores/refetches cleanly.
6. Complete through Review, inspect the activity/run summary, archive it, and
   find it using the archived filter.
7. Repeat key discovery/actions from list view.

Refresh between steps to prove persistence rather than client memory.

## Browser journey C — Agent worker lane

1. Create and assign a task to a real configured profile.
2. Observe Todo detail become Ready and then In Progress only after worker
   claim.
3. Observe safe progress/heartbeat without exposing raw tool data.
4. Exercise completion and Review/needs-input behavior.
5. Create a dependency pair and confirm the child does not dispatch early.
6. Resolve the dependency and confirm one claim/run.
7. Exercise terminate/reclaim after plan 004 implements them.

Restart the application while a controlled test task is recoverable. Confirm
upstream recovery semantics and no duplicate worker.

## Browser journey D — Settings-created automation

1. In Settings > Automations, create a short interval schedule with a clearly
   displayed timezone and next run.
2. Confirm exactly one template appears on the default board even if another
   board was selected.
3. Use Run now and confirm one linked occurrence enters Todo and is dispatched
   normally.
4. Wait for a due tick and confirm exactly one scheduled occurrence.
5. Pause and pass a due time without a new occurrence.
6. Resume/edit the schedule and confirm the next-run preview.
7. Archive/delete the schedule and confirm history remains available.
8. Refresh and restart the stack; confirm no duplicate template or occurrence.

## Browser journey E — Prompt-created automation

1. Ask an agent in ordinary language to create a recurring task.
2. Confirm the approval/safety path and successful native `cronjob` tool result.
3. Follow the response link or open Kanban; confirm one template exists on the
   default board.
4. Confirm its settings and lifecycle controls match a Settings-created
   automation.
5. Trigger/wait for an occurrence and confirm it executes only through Kanban.
6. Ask the agent to pause/resume/edit/remove it and confirm the UI updates.

This journey is required; UI-only interception is not acceptable.

## Browser journey F — Failure and recovery

Verify clear, non-destructive behavior for:

- backend temporarily unavailable;
- event stream disconnected;
- invalid/missing assignee at automation fire;
- dispatcher unavailable while manual board edits remain possible;
- task changed during drag;
- worker heartbeat stale or worker failure;
- attachment rejected;
- incompatible Hermes contract;
- corrupt board isolated by upstream recovery;
- unsupported/conflicting legacy cron migration.

## Persistence and concurrency

- Restart frontend/backend containers and confirm boards/tasks/schedules/runs.
- Attempt two idempotent creates with the same key; get one resource.
- Attempt two worker claims; get one run.
- Simulate cron retry/two tick attempts for one scheduled instant; get one
  occurrence.
- Change a task from UI and native CLI in close succession; preserve or reject
  with an explicit conflict, never silently lose data.
- Verify multiple boards use isolated upstream SQLite files and the default
  board remains the automation destination.

## Regression checks

- Agent chat, streaming events, stop/cancel, and approvals.
- Profile selection and local profile isolation.
- Runtime/9router configuration and health.
- Skills, workspace, memory, import/export, and connections after moving them
  into Settings.
- OpenTelemetry propagation and structured safe logs.
- Optional Enterprise API outage does not restrict local Kanban.
- No additional app process, database, or standalone dispatcher/cron daemon in
  Compose.

## Release evidence

Attach or link:

- pinned Hermes revision and compatibility-test result;
- focused and full check outputs;
- API contract test summary;
- browser automation trace/screenshot index;
- migration dry-run and test-migration result;
- security/redaction review;
- documentation changes;
- any intentionally deferred plan 004 feature with rationale.
