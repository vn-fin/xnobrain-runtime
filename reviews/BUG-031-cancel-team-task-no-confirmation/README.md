# BUG-031: Cancel team run executes immediately without confirmation

## Severity

High — one click permanently cancels and archives every native team stage.

## Area

Kanban team-task detail

## Environment

- Local development started with `make dev`
- Chromium 148, headed browser
- Tested 2026-08-14

## Prerequisites

Retained task `t_76dcac9d`, created from the QA discussion team. The task had not executed; all five native workflow nodes were Todo.

## Reproduction

1. Open the team-task detail.
2. Select **Cancel team run** once.

## Actual result

No confirmation appears. The task immediately changes to 100%/Cancelled, moves to Done, reports `Team run cancelled`, and marks coordinator, three stages, and synthesizer `ARCHIVED`.

## Expected result

Open a confirmation naming the task and the number of affected stages, with Cancel as the default safe action. A scheduled/not-started workflow should also use language that distinguishes cancelling a schedule from cancelling an active run.

## Reproducibility

Reproduced once on 2026-08-14. The task record and event history were retained; its prior runnable state could not be preserved because the action had no confirmation boundary.

## Impact

Users can irreversibly stop an entire workflow with an accidental click, including before any worker starts.

## Suggested fix

Gate the mutation behind the shared confirmation component, disable it while submitting, show the exact target/stage count, and return focus to the trigger on safe dismissal. Add integration coverage proving that opening and cancelling the dialog causes no API mutation.

## Evidence

- [Retained team task after the immediate cancellation](../evidence/kanban-team-task-cancelled-no-confirmation.png)
