# 003 — Kanban-native automation

Priority: P1. The Phase 0 execution bridge is a release blocker.

## Goal

Scheduled work is a Kanban capability, not a separate product. Users create an
automation from a task, from Settings, or by prompting an agent. Every
automation has a visible template card on the default board, and every due
execution becomes a normal Kanban occurrence handled by the existing dispatcher.

The existing in-process Kanban dispatcher owns schedule timing. Brain4All adds
schedule metadata beside native tasks in the same Kanban SQLite database; it
does not start another scheduler or create another database.

## User model

An **automation template** is a default-board card describing future work. It
shows schedule, timezone, next run, paused state, assignee/profile, and recent
occurrences.

An **occurrence** is a normal task created for one scheduled instant. It moves
through Todo, Running, Done, and Archived like any other task and
retains its run/comments/output summary.

Recurring schedules retain one template and many linked occurrences. This
preserves history and permits an earlier run to remain active when the next one
is due. For a one-shot schedule, the implementation may promote a single card
at due time only if that behavior remains idempotent and equally auditable;
using the same template/occurrence model is simpler and preferred.

## Phase 0 — SQLite scheduling extension

The pinned runtime does not expose the documented `scheduled_at` field or a
cron-to-Kanban execution target. Extend the database at the adapter boundary:

```text
existing Kanban dispatcher tick
  -> read due brain4all_task_schedules rows
  -> release one-shot task or create idempotent recurring occurrence
  -> embedded kanban dispatcher
  -> worker run and task history
```

The extension:

- leaves upstream task/run/event schemas unchanged;
- stores only timing, recurrence, timezone, enabled state, and counters;
- uses the native task as the schedule template and source content;
- promotes one-shot tasks through public transition operations;
- creates recurring tasks with a deterministic scheduled-instant key;
- runs immediately before the existing native dispatch pass.

## Data linkage

Prefer upstream-supported metadata fields. At minimum, persist durable opaque
links:

- template task ID;
- template task ID and owner profile;
- occurrence scheduled instant and trigger type;
- occurrence task ID;
- optional previous/next occurrence links;
- schedule revision used for the occurrence.

Use a deterministic key equivalent to:

`schedule:<template-task-id>:<scheduled-instant>`

The exact encoding is an implementation detail. It must be stable across
restarts and manual retries. “Run now” uses a distinct trigger/event ID so
intentional manual runs are not deduplicated against a scheduled run.

Do not duplicate the task description. Store schedule metadata in
`brain4all_task_schedules` inside the same board database and resolve it by
native task ID.

## Creation paths

All three paths call the same Brain4All/upstream domain operation:

1. **Task UI:** turn on “Repeat or run later” in quick create/task drawer.
2. **Settings:** create/manage automations in Settings > Automations.
3. **Prompt:** a Brain4All scheduling tool must call the same Kanban schedule
   service and create the associated default-board template.

Prompt-created schedules must not rely on a UI-only post-processing hook. The
tool/CLI contract itself must support the Kanban target or be safely overridden
at the registry edge with explicit configuration and tests.

The default board is mandatory for new automation templates and occurrences,
even when another board is selected. The UI explains this and links to the
created card. Future user-driven moves of occurrences may be supported;
templates remain on the default board so automation management is predictable.

## Schedule experience

Offer plain-language controls first:

- run once at a date/time;
- every N minutes/hours/days/weeks;
- daily/weekly/monthly at a time;
- timezone;
- start/end boundaries where upstream supports them.

Show a human-readable summary and next occurrence before saving. Put raw cron
expression/ISO input under “Advanced”, validate it server-side, and explain the
actual next runs. Never assume the browser timezone without displaying it.

Expose:

- pause — prevents future occurrences and preserves template/history;
- resume — recalculates next run through Hermes;
- edit — changes future occurrences only and increments schedule revision;
- run now — enqueues one manual occurrence;
- archive template — pauses first and hides it, preserving history;
- delete schedule — requires confirmation, removes future timing, and archives
  the template; it does not delete occurrence tasks or runs.

Archiving an occurrence never deletes or pauses its automation.

## Execution policy

- Copy the template’s profile/assignee, skills, workspace, priority, tags,
  goal/model overrides, and safe task content into the occurrence at fire time.
- Validate the assignee at create and fire time. If no valid worker exists,
  create the occurrence in Review with a “Needs assignee” detail rather than
  losing the scheduled event.
- Let normal Kanban dependency and concurrency rules control dispatch.
- Prevent recursive cron creation using the upstream cron job-session guard.
- Apply upstream safety scanning for prompt-created jobs and preserve approval
  behavior.
- Never log schedule prompts, task bodies, tool calls/output, credentials, or
  generated script contents.
- Define missed-tick behavior explicitly from upstream semantics; retries must
  not duplicate an occurrence.

## Legacy migration and cutover

Inventory both current sources before mutation:

- Brain4All per-profile YAML jobs under its data repository;
- native Hermes cron jobs already present for the same profiles.

Implement a one-time, idempotent migration:

1. Create immutable snapshots using repository persistence guarantees.
2. Produce a dry-run report with valid jobs, unsupported schedules, conflicts,
   and proposed default-board templates.
3. Convert supported legacy jobs to native Hermes definitions with Kanban
   execution target. Preserve owner, schedule, paused state, skills, prompt,
   working directory, and safe delivery semantics.
4. Create/link the template using an idempotency key derived from the legacy
   record; never create duplicates on restart.
5. Leave unsupported/conflicting jobs paused and report exact user action.
6. Mark migration completion atomically only after every record is accounted
   for.
7. Keep a documented rollback window using snapshots.

After successful migration:

- remove `self.service.scheduler_loop()` from the Brain4All lifespan;
- remove file-repository cron scheduling behavior;
- remove the separate right-panel Cron UI;
- either retire `/agent-gateway/v1/cron/jobs` with a versioned migration path or
  retain a temporary compatibility facade backed by the native automation
  service—never the YAML scheduler;
- update/supersede `docs/implementation/02-local-cron.md`.

## API extension

Extend the stable API with:

- `GET|POST /agent-gateway/v1/kanban/automations`
- `GET|PATCH /agent-gateway/v1/kanban/automations/{automation_id}`
- `POST /.../{automation_id}/pause`
- `POST /.../{automation_id}/resume`
- `POST /.../{automation_id}/run`
- `DELETE /.../{automation_id}`
- `GET /.../{automation_id}/occurrences`

Creation responses contain both template task and automation summaries.
Mutations use revision/precondition checks to prevent a stale settings screen
from overwriting a newer prompt/CLI edit.

## Tests

Backend contract tests:

- Settings, native CLI/tool, and API creation produce equivalent native cron
  definitions and exactly one default-board template;
- delay, interval, cron expression, ISO/one-shot, and timezone behavior;
- due tick creates exactly one occurrence with copied execution fields;
- restart, overlapping ticks, retry, and two ticker attempts create no
  duplicates;
- run now intentionally creates one distinct occurrence;
- pause/resume/edit/delete/archive semantics and history retention;
- missing assignee, invalid schedule, dispatcher degraded, worker failure;
- recursive creation guard and safe logging;
- dry-run/migrate/retry/rollback across YAML/native conflicts.

Browser tests:

- create/edit/pause/resume/run from Settings;
- schedule during quick task creation;
- ask an agent to create a schedule and see its template on the default board;
- observe a due occurrence move through the board and keep its run history;
- refresh/restart and verify next run/history without duplication;
- verify the old separate Cron panel is gone.

## Acceptance criteria

- There is one schedule ticker: the existing Kanban dispatcher loop.
- There is one execution queue: Hermes Kanban.
- There is one SQLite database per board; scheduling is an extension table in
  that database, not another persistence service.
- There is one visible default-board template for every new automation.
- Due work is never executed invisibly before becoming a Kanban occurrence.
- UI and prompt creation have the same lifecycle controls and persistence.
- Existing supported YAML schedules migrate without silent data loss.
- Focused tests, `make check`, and the automation journeys in
  `plans/VERIFICATION.md` pass.
