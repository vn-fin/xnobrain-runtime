# 003 — Kanban-native automation

Priority: P1. The Phase 0 execution bridge is a release blocker.

## Goal

Scheduled work is a Kanban capability, not a separate product. Users create an
automation from a task, from Settings, or by prompting an agent. Every
automation has a visible template card on the default board, and every due
execution becomes a normal Kanban occurrence handled by the existing dispatcher.

Hermes remains authoritative for schedule timing. Brain4All must remove its
duplicate scheduler only after migration is proven.

## User model

An **automation template** is a default-board card describing future work. It
shows schedule, timezone, next run, paused state, assignee/profile, and recent
occurrences.

An **occurrence** is a normal task created for one scheduled instant. It moves
through Todo, In Progress, Review, Done, and archive like any other task and
retains its run/comments/output summary.

Recurring schedules retain one template and many linked occurrences. This
preserves history and permits an earlier run to remain active when the next one
is due. For a one-shot schedule, the implementation may promote a single card
at due time only if that behavior remains idempotent and equally auditable;
using the same template/occurrence model is simpler and preferred.

## Phase 0 — Execution bridge

### Preferred path

Contribute or consume a small upstream Hermes cron execution target:

```text
native cron timing
  -> execution_target = kanban
  -> public kanban create_task
  -> embedded kanban dispatcher
  -> worker run and task history
```

The extension must:

- be valid from the native CLI, `cronjob` tool, and Brain4All Settings;
- enqueue rather than instantiate a direct `AIAgent`;
- use the configured/default board and an eligible profile/assignee;
- carry a deterministic occurrence idempotency key;
- return enough safe metadata to link the run to the template;
- preserve existing Hermes behavior for non-Kanban cron jobs outside
  Brain4All.

Add the capability to Hermes at the edge of its cron execution service rather
than forking the scheduler. Pin the first upstream revision containing it.

### Bounded fallback

If the upstream target is not available, create a Hermes no-agent script job
whose generated helper calls the public Kanban database API. This fallback is
allowed only after tests prove:

- deterministic scheduled-instant/idempotency information is available;
- the helper is generated atomically beneath the profile’s permitted script
  area and contains no credentials;
- upgrades are versioned and do not rewrite user scripts;
- Settings, prompt, CLI, pause/resume, manual trigger, missed ticks, and restart
  all behave consistently;
- no polling/reconciliation process is needed.

If any proof fails, block the plan and complete the upstream target. Do not
substitute a second scheduler or an after-the-fact shadow card.

## Data linkage

Prefer upstream-supported metadata fields. At minimum, persist durable opaque
links:

- template task ID;
- Hermes cron job ID and owner profile;
- occurrence scheduled instant and trigger type;
- occurrence task ID;
- optional previous/next occurrence links;
- schedule revision used for the occurrence.

Use a deterministic key equivalent to:

`cron:<profile-id>:<job-id>:<scheduled-instant>`

The exact encoding is an implementation detail. It must be stable across
restarts and manual retries. “Run now” uses a distinct trigger/event ID so
intentional manual runs are not deduplicated against a scheduled run.

Do not duplicate the full task description or schedule in a new Brain4All
database. Store Kanban metadata in Hermes Kanban and schedule metadata in
Hermes cron, resolving both through the service adapter.

## Creation paths

All three paths call the same Brain4All/upstream domain operation:

1. **Task UI:** turn on “Repeat or run later” in quick create/task drawer.
2. **Settings:** create/manage automations in Settings > Automations.
3. **Prompt:** the agent’s `cronjob` tool creates the schedule and associated
   default-board template.

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

- There is one schedule ticker: native Hermes.
- There is one execution queue: Hermes Kanban.
- There is one visible default-board template for every new automation.
- Due work is never executed invisibly before becoming a Kanban occurrence.
- UI and prompt creation have the same lifecycle controls and persistence.
- Existing supported YAML schedules migrate without silent data loss.
- Focused tests, `make check`, and the automation journeys in
  `plans/VERIFICATION.md` pass.
