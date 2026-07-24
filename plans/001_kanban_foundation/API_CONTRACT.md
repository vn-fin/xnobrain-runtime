# Brain4All Kanban API contract

This is the target product contract, not a license to reproduce the Hermes
dashboard API. During implementation, use the repository’s existing success
envelope, error model, authentication, pagination, and naming conventions.
Record any necessary route-shape adjustment here before merging code.

Base path: `/agent-gateway/v1/kanban`

## Resources

### Boards

- `GET /boards` — list boards and identify the default/current board.
- `POST /boards` — create a board with upstream slug validation.
- `GET /boards/{board_slug}` — board settings and counts.
- `PATCH /boards/{board_slug}` — rename/update supported board settings.
- `POST /boards/{board_slug}/select` — set the current UI/CLI board.
- `DELETE /boards/{board_slug}` — delete only with upstream safety checks.

The default board cannot be deleted while it owns automation templates. Deleting
any board requires explicit confirmation and an empty/archive/migration policy;
the service must not silently destroy task history.

### Tasks

- `GET /boards/{board_slug}/tasks`
- `POST /boards/{board_slug}/tasks`
- `GET /boards/{board_slug}/tasks/{task_id}`
- `PATCH /boards/{board_slug}/tasks/{task_id}`
- `POST /boards/{board_slug}/tasks/{task_id}/move`
- `POST /boards/{board_slug}/tasks/{task_id}/assign`
- `POST /boards/{board_slug}/tasks/{task_id}/archive`
- `POST /boards/{board_slug}/tasks/{task_id}/unarchive`

Reads support bounded pagination and validated filters. A task create accepts a
client idempotency key. Mutations accept an upstream revision/update token when
available, or another explicit precondition, so a stale drag cannot overwrite a
worker update.

`PATCH` edits fields but does not directly mutate lifecycle state. `/move`
expresses a user-facing target state and resolves to a legal domain operation.

### Collaboration and execution detail

- `GET|POST /boards/{board_slug}/tasks/{task_id}/comments`
- `GET|POST /boards/{board_slug}/tasks/{task_id}/links`
- `DELETE /boards/{board_slug}/tasks/{task_id}/links` (parent/child IDs in the
  request body; Hermes exposes links as a pair rather than a link resource)
- `GET|POST /boards/{board_slug}/tasks/{task_id}/attachments`
- `GET|DELETE /boards/{board_slug}/tasks/{task_id}/attachments/{attachment_id}`
- `GET /boards/{board_slug}/tasks/{task_id}/runs`
- `GET /boards/{board_slug}/tasks/{task_id}/runs/{run_id}`

Attachment reads return content through a controlled response. They never
return the server-side path.

### Operations and events

- `POST /boards/{board_slug}/dispatch` — nudge dispatch; idempotent and bounded.
- `GET /workers` — safe active-worker summaries across visible boards.
- `GET /boards/{board_slug}/events/stream` — resumable SSE stream for one
  board. `Last-Event-ID` or `?after=` resumes from a monotonic event ID.
- `GET /diagnostics` — compatibility, dispatcher, and board health without
  sensitive values.

Advanced endpoints are added in plan 004 rather than passing through every
upstream route on day one.

## Product task representation

At minimum:

```json
{
  "id": "opaque-upstream-id",
  "board_slug": "default",
  "title": "Prepare the weekly report",
  "description": "User-authored task details",
  "status": "ready",
  "kanban_status": "running",
  "state_detail": {
    "kind": "ready",
    "label": "Ready",
    "reason": null
  },
  "assignee": "research-agent",
  "priority": "normal",
  "tags": ["weekly"],
  "archived": false,
  "automation": null,
  "dependencies": [],
  "worker": null,
  "created_at": "RFC3339",
  "updated_at": "RFC3339",
  "revision": "opaque"
}
```

`status` preserves the native task state. Wire values for `kanban_status` are
fixed: `backlog`, `todo`, `running`, `done`, and `archived`. Labels are
localized by the client.

`state_detail.kind` is a bounded enum derived from the pinned upstream version,
not a second editable status. Examples include `triage`, `waiting_dependency`,
`ready`, `scheduled`, `running`, `review_required`, `needs_input`, `failed`,
and `completed`.

Unknown future upstream states must produce a compatibility diagnostic, not be
silently mapped to Todo.

## Move intent matrix

Exact operations must be confirmed against the pinned public Hermes API:

| From product state | Requested state | Domain intent |
| --- | --- | --- |
| Backlog | Todo | Promote/clarify task, then make eligible when assigned |
| Todo | Backlog | Return to triage if no worker owns it |
| Todo | Running | Assign if needed, make ready, nudge dispatcher |
| Todo | Done | Complete manually with a completion summary |
| Running | Todo | Terminate/reclaim safely, then requeue; confirmation required |
| Running | Done | Complete through the worker/domain completion operation |
| Done | Todo | Unblock/requeue when the native state is blocked |
| Done | Todo | Reopen as a new linked task unless upstream supports auditable reopen |
| Any non-archived state | Archived | Confirm, then invoke the public archive operation |

Moves not listed are rejected with allowed next actions. A visual drag shows the
resolved effect before confirmation when it can terminate work, discard a
completion outcome, or create a follow-up task.

Never make `running` by patching a row. Only a worker claim may establish active
execution.

## Errors

Use the repository envelope with stable machine-readable codes:

- `kanban_not_ready`
- `board_not_found`
- `task_not_found`
- `invalid_transition`
- `stale_task`
- `assignment_invalid`
- `dependency_conflict`
- `attachment_invalid`
- `dispatcher_unavailable`
- `kanban_contract_incompatible`

Expected validation is 4xx; an upstream compatibility failure is 503 and
appears in health diagnostics. Error messages may name resource identifiers,
but never request bodies, descriptions, prompts, worker output, credentials, or
filesystem paths.

## Event contract

Events carry a monotonic/resumable identifier, task ID, native event kind,
timestamp, current native `status`, projected `kanban_status`, assignee, and
the minimum safe changed fields. The stream first sends a `connected` event
with its cursor, then `task` events whose payload contains the projected
native event. Event kinds remain the native public kinds such as `created`,
`assigned`, `claimed`, `completed`, and `archived`.

The client treats events as invalidation hints and refetches canonical records.
Reconnect uses the last event ID; missed history falls back to a full refetch.
Do not stream prompts, comments, descriptions, tool data, or logs in event
payloads.
