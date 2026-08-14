# BUG-012: Delete Automation executes immediately without confirmation

## Severity

Critical — a single click permanently removes the job and its stored run outputs.

## Area

Automation list → Delete

## Reproduction

1. Create retained job `QA 2026-08-14 retained heartbeat` and run it multiple times.
2. Select **Delete** on its list card, expecting the product’s standard confirmation boundary.
3. Attempt to locate and cancel the confirmation.

## Actual result

- No confirmation dialog appears.
- The job is immediately removed from `cron/jobs.json`.
- Its `cron/output/<job-id>/` Markdown run outputs are removed at the same time.
- The list count and card update immediately.

This happened during the preservation-focused QA pass. The job record, original ID, schedule, model snapshot, and delivery target were restored from the product’s retained `snapshots/cron/jobs` snapshot. A new successful run repopulated the output directory. The two original Markdown output files could not be restored from the job snapshot, although their UI screenshots and test evidence remain.

## Expected result

Delete must open a target-specific confirmation with Cancel as the safe/default action. The dialog should explain whether run history and output artifacts will also be removed. No data should change before explicit confirmation.

## Suggested fix

- Route Delete through the shared `ConfirmDialog`, including job name and the scope of associated data.
- Default focus to Cancel, support Escape, and disable duplicate confirmation submission.
- Consider a recoverable archive/soft-delete period for job definitions and outputs.
- Snapshot both the job record and its output directory before confirmed deletion.
- Add an end-to-end test asserting the initial click performs zero API mutation, Cancel retains all files, and only explicit confirm deletes.

## Evidence

- [State immediately after the unconfirmed delete](../../evidence/automation-delete-confirmation.png)
- [Restored retained job](../../evidence/automation-restored-after-delete.png)
- [Successful run after restoration](../../evidence/automation-restored-run.png)
