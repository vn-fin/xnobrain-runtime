# BUG-025: Moving an unscheduled task to Todo makes it immovable as “scheduled”

## Severity

High — a normal board move changes task control semantics and traps the task in Todo.

## Area

Kanban task detail → Move task

## Environment

Local development started with `make dev`, headed Chromium 148, tested 2026-08-14.

## Prerequisites

- Retained task `t_ef87c5af`, created unassigned in Backlog with no schedule

## Reproduction

1. Open the unassigned, unscheduled Backlog task.
2. Choose **Todo** in Move task.
3. After the move completes, choose **Backlog**.

The same state split also occurs when creating a saved-Team task with either Backlog or Todo selected: the parent is immediately stored as native `scheduled`/Kanban `todo`, all DAG nodes remain Todo, no start control is exposed, and waiting/reload does not dispatch it.

## Actual result

The first move adds `Task Prepared`, `Ready To Run`, and `Task Scheduled` events and parks the task in Todo. The detail now says “Scheduled tasks are controlled by their schedule,” despite no schedule being configured. Selecting Backlog produces no change; the value remains `todo` and the task is trapped.

## Expected result

A manually moved unscheduled task should remain manually movable between Backlog and Todo. If Todo requires an assignee/schedule, the transition should be validated before mutation with actionable guidance.

## Reproducibility

Reproduced on the retained QA backlog task on 2026-08-14. The task was left in the resulting Todo state for review rather than mutated out-of-band.

## Impact

Routine triage can accidentally convert an unassigned task into a schedule-controlled record that cannot be restored through the UI.
Saved-Team tasks can likewise be expanded into a stranded native DAG with no visible way to start it.

## Suggested fix

- Keep board column separate from scheduling/native execution status.
- Do not emit `Task Scheduled` for a task without schedule configuration.
- Validate transitions atomically and preserve an explicit manual move-back path.
- Add transition-matrix tests for unassigned/assigned and scheduled/unscheduled tasks.

## Evidence

- [Todo task incorrectly treated as schedule-controlled](../evidence/kanban-todo-cannot-return-backlog.png)
- [Team task remains scheduled with every native node stuck in Todo](../evidence/kanban-team-task-stuck-scheduled.png)
