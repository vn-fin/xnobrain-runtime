# BUG-033: Automation delivery-target removal has no confirmation

## Severity

Medium — a single icon click immediately removes retained delivery configuration.

## Area

Automation detail → Deliver to

## Environment

- Local development started with `make dev`
- Chromium 148, headed browser
- Tested 2026-08-14

## Prerequisites

The retained heartbeat automation has workspace-file and Kanban-board delivery targets.

## Reproduction

1. Open the automation detail.
2. Inspect a target-row trash button.

## Actual result

The visible control has no confirmation state. Code-path inspection during the manual boundary test shows its click handler calls `onRemoveTarget(...)` directly. It was not clicked because doing so would violate the explicit target-preservation rule.

## Expected result

Open a confirmation naming the automation, target type, and destination; Cancel should be the default and must cause no API mutation.

## Reproducibility

The direct mutation path was verified in the running build source on 2026-08-14. Destructive execution was deliberately skipped after the missing boundary was established.

## Impact

An accidental click can silently alter where future automation results are delivered.

## Suggested fix

Route target removal through the shared confirmation component, disable repeat submission while pending, and restore focus to the row/action after cancellation.

## Evidence

- [Retained target rows and remove controls](../evidence/automation-delivery-target-remove-buttons.png)
