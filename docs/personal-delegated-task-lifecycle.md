# Personal delegated Task Board lifecycle

Personal task creation keeps the native `/kanban/boards/{board}/tasks` contract.
`team_id` refers to a saved team. All-local teams retain the existing native DAG.
A saved team with enabled external members instead creates a native, unassigned
root task with a durable `xnobrain_personal_task_executions` extension binding.
The binding records the saved team's content revision; it is not a comment or
browser-supplied workspace address. Direct agent assignment cannot replace it.
The initial task and creation intent commit atomically. A durable completion
checkpoint lets a retry or restarted reconciler finish Todo/schedule setup;
partially created tasks cannot run. A saved schedule that becomes due during
recovery keeps its original occurrence identity rather than being reset.

Backlog/Todo stay editable. Moving to In Progress or releasing a scheduled
occurrence makes the task eligible for the existing dispatcher lifecycle's
reconciliation worker. Dispatcher ticks wake one background worker without
waiting for Control; slow remote requests do not delay local dispatch. Native
claim interception excludes every bound task,
including when the feature flag is disabled. No remote alias can fall back to
a local profile. Interval occurrences receive the binding before promotion,
and keep the existing native schedule idempotency identity.

Before remote admission, SQLite persists the exact source revision and stable
idempotency key. The Runtime calls Control's authenticated internal personal
execution adapter, which resolves the workspace owner and tenant from persisted
provisioning state. It never accepts owner or tenant authority from the task.
Control revalidates the exact grants, destination and funding, and runs the
shared team orchestration with owner-routed workers. External references keep
their original owner workspaces. No organization workspace or administrator is
invented for a personal composition.

The callback polls durable execution receipts. A lost POST response retries the
same source/key; receipt revisions cannot reopen a completed execution or switch
its identity. Cancellation remains pending until the execution receipt confirms
it. Started task configuration and archive/move operations remain locked while
the execution is unresolved. Board archival rejects incomplete creation, ready
delegated tasks and unknown or active admission outcomes. Stable shared board
connection locks serialize against exclusive archive, and stale connections
cannot recreate the removed board directory. Deleting a referenced team or losing authorization
produces a bounded error; it does not dispatch elsewhere or stop reconciliation
of unrelated tasks. Terminal receipts close the native task and retain the
orchestrator's result in the normal task result surface.

`team_id` and `delegated_execution: {id,state,error,cancel_requested}` are additive
task fields. They contain no runner capability, workspace URL, credentials or
remote conversation ID. Native comments, schedule controls, lifecycle events
and task details continue to use their existing routes. Existing executions
remain recoverable/cancellable when new feature exposure is disabled.

Verification uses real pinned Hermes SQLite and native dispatcher calls, with
a controlled Control transport to test unknown admission, restart, cancellation,
schedule replay, immutable source bindings, result propagation and flag rollback.
This is separate from PostgreSQL grant/owner-routing integration and from live
provider execution in a deployed multi-workspace environment.
